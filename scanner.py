"""Collect publicly observable website signals in one fresh Chromium context."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import shlex
import sys
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


SIGNAL_TYPES = (
    "html", "headers", "scripts", "stylesheets", "meta", "cookies",
    "network", "globals", "dom",
)


@dataclass(frozen=True)
class Observation:
    value: str
    source_url: str
    key: str | None = None
    details: dict[str, str] = field(default_factory=dict)


@dataclass
class ScanResult:
    requested_url: str
    final_url: str
    scanned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status_code: int | None = None
    title: str = ""
    signals: dict[str, list[Observation]] = field(
        default_factory=lambda: {name: [] for name in SIGNAL_TYPES}
    )
    warnings: list[str] = field(default_factory=list)


class ScanError(RuntimeError):
    """A scan could not start or yielded no usable website response."""


def normalize_url(url: str) -> str:
    """Accept a hostname shorthand, but only navigate HTTP(S) URLs."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Enter a non-empty HTTP(S) URL or hostname.")
    url = url.strip()
    if re.search(r"\s", url):
        raise ValueError("URLs cannot contain whitespace.")
    if "://" not in url:
        # Reject explicit non-web schemes while allowing localhost:8080.
        if re.match(r"^[a-zA-Z][\w+.-]*:", url) and not re.match(
            r"^[^/:]+:\d+(?:/|$)", url
        ):
            raise ValueError("Only HTTP and HTTPS URLs are supported.")
        url = "https://" + url
    try:
        parts = urlsplit(url)
        _ = parts.port  # Validate malformed/out-of-range ports.
        hostname = parts.hostname
    except ValueError as exc:
        raise ValueError(f"Invalid URL: {exc}") from exc
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError("Enter a valid HTTP(S) URL with a hostname.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URLs containing credentials are not supported.")
    # The browser handles Unicode hostnames; fragments are not sent to servers.
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, ""))


def extract_html_signals(html: str, source_url: str) -> dict[str, list[Observation]]:
    """Parse rendered HTML, resolving relative URLs and preserving meta keys."""
    signals: dict[str, list[Observation]] = {name: [] for name in SIGNAL_TYPES}
    signals["html"].append(Observation(html, source_url))
    soup = BeautifulSoup(html, "html.parser")
    base = soup.find("base", href=True)
    base_url = urljoin(source_url, str(base["href"])) if base else source_url
    for tag in soup.find_all("script", src=True):
        signals["scripts"].append(Observation(urljoin(base_url, str(tag["src"])), source_url))
    for tag in soup.find_all("link", href=True):
        if "stylesheet" in [str(rel).lower() for rel in tag.get("rel", [])]:
            signals["stylesheets"].append(
                Observation(urljoin(base_url, str(tag["href"])), source_url)
            )
    for tag in soup.find_all("meta", content=True):
        key = tag.get("name") or tag.get("property") or tag.get("http-equiv")
        if key:
            signals["meta"].append(Observation(str(tag["content"]), source_url, str(key).lower()))
    return signals


# Inspect presence only: no JavaScript values, cookie values, or request bodies.
# JSON-provided paths are traversed as properties, never evaluated as code.
GLOBAL_PROBE = """paths => paths.filter(path => {
    try {
        let value = window;
        for (const part of path.split('.')) {
            if (value === null || value === undefined || !(part in Object(value))) return false;
            value = value[part];
        }
        return value !== undefined && value !== null;
    } catch { return false; }
})"""

DOM_PROBE = """() => {
    const keys = new Set();
    const nodes = [document, ...Array.from(document.querySelectorAll('*')).slice(0, 2000)];
    for (const node of nodes) {
        for (const key of Object.keys(node)) keys.add(key);
    }
    return {keys: Array.from(keys), truncated: document.querySelectorAll('*').length > 2000};
}"""


def scan_website(
    url: str,
    *,
    global_paths: list[str] | None = None,
    timeout_ms: int = 20_000,
    settle_ms: int = 2_500,
    headless: bool = True,
) -> ScanResult:
    """Scan the homepage, including requests and DOM changes after navigation.

    A DOMContentLoaded timeout preserves partial observations if a main document
    was received. Fatal navigation errors become ScanError. Resource request URLs
    show attempted loading, not successful initialization of the named service.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    normalized = normalize_url(url)
    if timeout_ms <= 0 or settle_ms < 0:
        raise ValueError("timeout_ms must be positive and settle_ms cannot be negative.")
    result = ScanResult(normalized, normalized)
    seen: set[tuple] = set()

    def add(signal: str, observation: Observation) -> None:
        identity = (
            signal, observation.value, observation.source_url, observation.key,
            tuple(sorted(observation.details.items())),
        )
        if identity not in seen:
            seen.add(identity)
            result.signals[signal].append(observation)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless, timeout=timeout_ms)
            try:
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                main_response = None
                request_limit_reached = False

                def on_request(request) -> None:
                    nonlocal request_limit_reached
                    if len(result.signals["network"]) >= 2000:
                        request_limit_reached = True
                        return
                    add("network", Observation(
                        request.url, page.url if page.url != "about:blank" else normalized,
                        details={"resource_type": request.resource_type},
                    ))
                    # Capture scripts even if they remove themselves from the DOM.
                    if request.resource_type in {"script", "stylesheet"}:
                        signal = "scripts" if request.resource_type == "script" else "stylesheets"
                        add(signal, Observation(request.url, page.url if page.url != "about:blank" else normalized))

                def on_response(response) -> None:
                    nonlocal main_response
                    if response.request.is_navigation_request() and response.frame == page.main_frame:
                        main_response = response

                page.on("request", on_request)
                page.on("response", on_response)
                try:
                    page.goto(normalized, wait_until="domcontentloaded", timeout=timeout_ms)
                except PlaywrightTimeout:
                    result.warnings.append(
                        f"Navigation exceeded {timeout_ms} ms; reporting available partial observations."
                    )
                except PlaywrightError as exc:
                    raise ScanError(f"Navigation failed for {normalized}: {str(exc).splitlines()[0]}") from exc

                # A bounded observation window works even on pages with perpetual
                # analytics connections, where waiting for networkidle would stall.
                page.wait_for_timeout(settle_ms)
                result.final_url = page.url
                if main_response is None:
                    raise ScanError(f"No HTTP document response received from {normalized}.")
                result.status_code = main_response.status
                if main_response.status >= 400:
                    result.warnings.append(
                        f"HTTP {main_response.status}: evidence may describe an error or challenge page."
                    )
                for key, value in main_response.all_headers().items():
                    if key.lower() not in {"set-cookie", "authorization", "proxy-authorization"}:
                        add("headers", Observation(value, main_response.url, key.lower()))

                try:
                    html = page.content()
                    result.title = page.title()
                    for signal, observations in extract_html_signals(html, page.url).items():
                        for observation in observations:
                            add(signal, observation)
                except PlaywrightError as exc:
                    result.warnings.append(f"Could not read rendered HTML: {str(exc).splitlines()[0]}")

                try:
                    for path in page.evaluate(GLOBAL_PROBE, global_paths or []):
                        add("globals", Observation("present", page.url, path))
                    dom = page.evaluate(DOM_PROBE)
                    for key in dom["keys"]:
                        add("dom", Observation(key, page.url))
                    if dom["truncated"]:
                        result.warnings.append("DOM property inspection was limited to the first 2000 elements.")
                except PlaywrightError as exc:
                    result.warnings.append(f"Could not inspect JavaScript: {str(exc).splitlines()[0]}")

                for cookie in context.cookies():
                    add("cookies", Observation(
                        cookie["name"], page.url, cookie["name"],
                        {"domain": cookie["domain"], "path": cookie["path"]},
                    ))
                if request_limit_reached:
                    result.warnings.append("Network observation was limited to 2000 distinct requests.")
            finally:
                browser.close()
    except PlaywrightError as exc:
        message = f"Browser scan failed: {str(exc).splitlines()[0]}."
        if "Executable doesn't exist" in str(exc):
            python_command = f'& "{sys.executable}"' if sys.platform == "win32" else shlex.quote(sys.executable)
            message += f" Install Chromium using this Python environment: {python_command} -m playwright install chromium"
        raise ScanError(message) from exc
    return result
