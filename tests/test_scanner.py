import sys

import pytest

from scanner import ScanError, extract_html_signals, normalize_url, scan_website


def test_url_normalization_and_supported_local_urls():
    assert normalize_url(" example.com/shop#section ") == "https://example.com/shop"
    assert normalize_url("http://localhost:8080") == "http://localhost:8080/"
    assert normalize_url("https://[::1]:8080/") == "https://[::1]:8080/"


@pytest.mark.parametrize("url", [
    "", None, "ftp://example.com", "file:///tmp/test.html", "javascript:alert(1)",
    "https://", "https://user:password@example.com", "https://example.com:bad",
    "https://example.com:99999", "https://example.com/has space",
])
def test_invalid_urls(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_html_parsing_handles_empty_content_and_meta_properties():
    assert extract_html_signals("", "https://example.com/")["scripts"] == []
    signals = extract_html_signals(
        '<script src="//cdn.example.com/a.js"></script>'
        '<meta property="OG:TYPE" content="website">'
        '<link rel="preload" href="/not-a-stylesheet.css">',
        "https://example.com/",
    )
    assert signals["scripts"][0].value == "https://cdn.example.com/a.js"
    assert signals["meta"][0].key == "og:type"
    assert signals["stylesheets"] == []


def test_missing_browser_error_uses_the_current_python_environment(monkeypatch):
    from playwright.sync_api import BrowserType, Error

    def missing_browser(*args, **kwargs):
        raise Error("BrowserType.launch: Executable doesn't exist at missing-browser.exe")

    monkeypatch.setattr(BrowserType, "launch", missing_browser)
    with pytest.raises(ScanError) as error:
        scan_website("https://example.com")
    assert sys.executable in str(error.value)
    assert "-m playwright install chromium" in str(error.value)
