import json
from pathlib import Path

import pytest

from detector import Detector
from scanner import Observation, ScanResult, extract_html_signals


URL = "https://store.example/"


@pytest.fixture(scope="module")
def detector():
    return Detector()


def test_controlled_html_fixture(detector):
    html = Path(__file__).with_name("fixtures").joinpath("store.html").read_text(encoding="utf-8")
    signals = extract_html_signals(html, URL)
    results = detector.detect(signals)
    assert {item["name"] for item in results} == {"WordPress", "Bootstrap", "jQuery", "Klaviyo", "Angular"}
    assert signals["scripts"][0].value == "https://store.example/assets/jquery-3.7.1.min.js"
    klaviyo = next(item for item in results if item["name"] == "Klaviyo")
    assert len(klaviyo["evidence"]) == 1
    evidence = klaviyo["evidence"][0]
    assert evidence["source_url"] == URL
    assert evidence["value"] == "https://static.klaviyo.com/onsite/js/klaviyo.js?company_id=TEST"
    assert evidence["matched_text"].startswith("https://static.klaviyo.com/onsite/")
    assert evidence["rule"]["signal"] == "scripts"
    assert evidence["rule"]["id"].startswith("klaviyo-")


@pytest.mark.parametrize("name,signal,value,key", [
    ("Shopify", "globals", "present", "Shopify.shop"),
    ("WooCommerce", "stylesheets", "https://store.example/wp-content/plugins/woocommerce/assets/css/style.css", None),
    ("React", "dom", "__reactFiber$abc123", None),
    ("Next.js", "scripts", "https://store.example/_next/static/chunks/app.js", None),
    ("Vue", "dom", "__vue_app__", None),
    ("Nuxt", "globals", "present", "__NUXT__"),
    ("Google Analytics", "scripts", "https://www.googletagmanager.com/gtag/js?id=G-ABC123", None),
    ("Google Tag Manager", "network", "https://www.googletagmanager.com/gtm.js?id=GTM-ABC123", None),
    ("Meta Pixel", "cookies", "_fbp", "_fbp"),
    ("Hotjar", "scripts", "https://static.hotjar.com/c/hotjar-123.js?sv=7", None),
    ("Microsoft Clarity", "network", "https://www.clarity.ms/tag/abc123", None),
    ("Cloudflare", "headers", "Cloudflare", "Server"),
    ("Drupal", "meta", "Drupal 10", "Generator"),
    ("Triple Whale", "globals", "present", "TriplePixelData"),
    ("Elevar", "globals", "present", "ElevarDebugMode"),
])
def test_representative_signals(detector, name, signal, value, key):
    results = detector.detect({signal: [Observation(value, URL, key)]})
    technology = next(item for item in results if item["name"] == name)
    assert technology["evidence"][0]["value"] == value


def test_html_evidence_is_an_excerpt_with_exact_match_and_location(detector):
    marker = '<div ng-version="19.0.0"'
    html = "a" * 1000 + marker + ">test</div>" + "z" * 1000
    evidence = detector.detect({"html": [Observation(html, URL)]})[0]["evidence"][0]
    assert evidence["matched_text"] == marker
    start, end = evidence["match_span"]
    assert html[start:end] == marker
    assert evidence["value"] == "..." + html[start - 80:end + 80] + "..."
    assert len(evidence["value"]) < 250


def test_deduplication_retains_distinct_sources_and_rules(detector):
    observation = Observation("present", URL, "ElevarDebugMode")
    results = detector.detect({"globals": [
        observation, observation,
        Observation("present", "https://store.example/product", "ElevarDebugMode"),
        Observation("present", URL, "Elevar"),
    ]})
    assert len(results) == 1
    assert len(results[0]["evidence"]) == 3


def test_cookie_names_on_distinct_domains_remain_distinct_evidence(detector):
    results = detector.detect({"cookies": [
        Observation("__kla_id", URL, "__kla_id", {"domain": "store.example", "path": "/"}),
        Observation("__kla_id", URL, "__kla_id", {"domain": ".store.example", "path": "/"}),
    ]})
    assert len(results[0]["evidence"]) == 2


def test_missing_and_malformed_observations_are_ignored(detector):
    assert detector.detect({}) == []
    assert detector.detect({"scripts": None, "html": [None, {}, 7], "globals": "present"}) == []
    assert detector.detect({"scripts": [Observation(None, URL)]}) == []
    with pytest.raises(ValueError, match="mapping"):
        detector.detect(None)


def test_names_shared_queues_and_ad_tags_do_not_imply_technologies(detector):
    signals = extract_html_signals(
        "<p>Shopify React Vue jQuery Klaviyo Hotjar Cloudflare Bootstrap</p>"
        '<script src="https://www.googletagmanager.com/gtag/js?id=AW-123"></script>'
        '<script src="https://static.klaviyo.com.evil.example/onsite/klaviyo.js"></script>'
        '<script src="https://example.com/tracker.js?vendor=static.klaviyo.com/onsite/"></script>',
        URL,
    )
    signals["globals"] = [Observation("present", URL, "dataLayer"), Observation("present", URL, "gtag")]
    assert detector.detect(signals) == []


def test_javascript_property_names_are_case_sensitive(detector):
    assert detector.detect({"globals": [Observation("present", URL, "shopify.shop")]}) == []


@pytest.mark.parametrize("change,message", [
    ({"pattern": "["}, "invalid regex"),
    ({"signal": "unknown"}, "unknown signal"),
    ({"pattern": ""}, "non-empty regex"),
    ({"case_sensitive": "false"}, "boolean"),
    ({"signal": "globals", "key": "window['bad']"}, "property path"),
])
def test_invalid_rules_fail_early(tmp_path, change, message):
    rule = {"id": "example-script", "signal": "scripts", "pattern": "example"}
    rule.update(change)
    path = tmp_path / "fingerprints.json"
    path.write_text(json.dumps({"schema_version": 1, "technologies": [
        {"name": "Example", "category": "Testing", "rules": [rule]},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        Detector(path)


def test_report_contains_only_matching_evidence_and_scan_metadata(detector):
    scan = ScanResult(URL, URL, status_code=200, title="Test store")
    scan.signals["cookies"] = [Observation("__kla_id", URL, "__kla_id", {"domain": "store.example", "path": "/"})]
    report = detector.report(scan)
    assert report["fingerprint_count"] == 60
    assert report["signal_counts"]["cookies"] == 1
    assert "signals" not in report
    evidence = report["technologies"][0]["evidence"][0]
    assert evidence["details"] == {"domain": "store.example", "path": "/"}
    assert "confidence" not in json.dumps(report)
    assert json.loads(json.dumps(report)) == report
