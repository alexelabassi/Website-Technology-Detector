import json

import main
from scanner import Observation, ScanError, ScanResult


def test_cli_writes_evidence_report_and_concise_output(monkeypatch, tmp_path, capsys):
    scan = ScanResult("https://store.example/", "https://store.example/", status_code=200)
    scan.signals["globals"] = [Observation("present", scan.final_url, "Shopify.shop")]
    monkeypatch.setattr(main, "scan_website", lambda *args, **kwargs: scan)
    output = tmp_path / "reports" / "result.json"
    assert main.main(["store.example", "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["technologies"][0]["name"] == "Shopify"
    text = capsys.readouterr().out
    assert "E-commerce" in text and "JavaScript global [Shopify.shop]: present" in text
    assert "JSON report:" in text


def test_cli_fatal_failure_is_nonzero_and_does_not_write_report(monkeypatch, tmp_path, capsys):
    def fail(*args, **kwargs):
        raise ScanError("Navigation failed")
    monkeypatch.setattr(main, "scan_website", fail)
    output = tmp_path / "result.json"
    assert main.main(["store.example", "--output", str(output)]) == 1
    assert not output.exists()
    assert "Navigation failed" in capsys.readouterr().err
