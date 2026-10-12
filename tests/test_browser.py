"""Deterministic end-to-end tests; no live third-party sites or services."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time

import pytest

from detector import Detector
from scanner import ScanError, scan_website


pytestmark = pytest.mark.browser

HTML = b"""<!doctype html><html><head><title>Dynamic fixture</title></head>
<body><div id="app">Store</div><script>
setTimeout(() => {
    window.klaviyo = {track() {}};
    document.querySelector('#app')['__reactFiber$fixture'] = {};
    const meta = document.createElement('meta');
    meta.name = 'generator'; meta.content = 'WordPress 6.8'; document.head.append(meta);
    const style = document.createElement('link');
    style.rel = 'stylesheet'; style.href = '/bootstrap.min.css'; document.head.append(style);
    const script = document.createElement('script');
    script.src = '/_next/static/chunks/page.js'; document.head.append(script);
    fetch('/test-analytics/beacon');
}, 100);
</script></body></html>"""


@pytest.fixture(scope="module")
def local_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/abort":
                self.connection.close()
                self.close_connection = True
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            if self.path == "/slow.js":
                time.sleep(0.8)
            body = HTML
            content_type = "text/html"
            status = 503 if self.path == "/unavailable" else 200
            if self.path == "/slow":
                body = b'<html><head><meta name="generator" content="WordPress 6.8"><script src="/slow.js"></script></head><body>Partial</body></html>'
            elif self.path.endswith(".js"):
                body, content_type = b"window.jQuery = {fn: {jquery: '3.7.1'}};", "application/javascript"
            elif self.path.endswith(".css"):
                body, content_type = b"body {color: black;}", "text/css"
            elif self.path == "/test-analytics/beacon":
                body, content_type = b"{}", "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Powered-By", "Next.js")
            self.send_header("Set-Cookie", "_ga=do-not-store-this-value; Path=/; HttpOnly; SameSite=Lax")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_dynamic_scan_redirect_headers_network_and_cookie_privacy(local_site, tmp_path):
    detector = Detector()
    scan = scan_website(local_site + "/redirect", global_paths=detector.global_paths, settle_ms=700)
    report = detector.report(scan)
    assert scan.final_url == local_site + "/"
    assert scan.status_code == 200 and scan.title == "Dynamic fixture"
    assert {item["name"] for item in report["technologies"]} == {
        "React", "Next.js", "jQuery", "Klaviyo", "WordPress", "Bootstrap", "Google Analytics",
    }
    assert any(item.value == local_site + "/test-analytics/beacon" for item in scan.signals["network"])
    assert any(item.key == "jQuery.fn.jquery" for item in scan.signals["globals"])
    assert not any(item.key == "set-cookie" for item in scan.signals["headers"])
    assert scan.signals["cookies"][0].details == {"domain": "127.0.0.1", "path": "/"}
    assert "do-not-store-this-value" not in json.dumps(report)
    # A network-only fingerprint must work through the same engine.
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps({"schema_version": 1, "technologies": [{
        "name": "Fixture tracker", "category": "Testing", "rules": [{
            "id": "fixture-beacon", "signal": "network", "pattern": "/test-analytics/beacon$",
        }],
    }]}), encoding="utf-8")
    assert Detector(custom).detect(scan.signals)[0]["evidence"][0]["value"].endswith("/test-analytics/beacon")


def test_navigation_timeout_keeps_available_evidence(local_site):
    scan = scan_website(local_site + "/slow", timeout_ms=250, settle_ms=50)
    assert scan.status_code == 200
    assert any("partial observations" in warning for warning in scan.warnings)
    assert "WordPress" in {item["name"] for item in Detector().detect(scan.signals)}


def test_http_error_page_is_explicitly_flagged(local_site):
    scan = scan_website(local_site + "/unavailable", settle_ms=0)
    assert scan.status_code == 503
    assert any("HTTP 503" in warning for warning in scan.warnings)


def test_fatal_navigation_error_is_actionable(local_site):
    with pytest.raises(ScanError, match="Navigation failed"):
        scan_website(local_site + "/abort", timeout_ms=1000, settle_ms=0)
