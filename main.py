"""Command-line interface: python main.py example.com --output report.json."""

import argparse
import json
from pathlib import Path
import sys

from detector import DEFAULT_FINGERPRINTS, Detector
from scanner import ScanError, scan_website


LABELS = {
    "html": "HTML", "headers": "HTTP header", "scripts": "Script URL",
    "stylesheets": "Stylesheet URL", "meta": "Meta tag", "cookies": "Cookie name",
    "network": "Network request", "globals": "JavaScript global", "dom": "DOM property",
}


def display_text(value: str, limit: int | None = 180) -> str:
    # Keep terminal output on one line, including pages containing control codes.
    value = "".join(char if char.isprintable() else " " for char in value)
    return value if limit is None or len(value) <= limit else value[:limit - 3] + "..."


def print_report(report: dict, *, verbose: bool = False) -> None:
    print(f"Website: {display_text(report['final_url'])}")
    count = len(report["technologies"])
    technology_label = "technology" if count == 1 else "technologies"
    print(f"HTTP: {report['status_code']} | Detected: {count} {technology_label}")
    for warning in report["warnings"]:
        print(f"Warning: {display_text(warning)}")
    category = None
    for technology in report["technologies"]:
        if technology["category"] != category:
            category = technology["category"]
            print(f"\n{display_text(category)}")
        evidence = technology["evidence"]
        item_label = "item" if len(evidence) == 1 else "items"
        print(f"  {display_text(technology['name'])} ({len(evidence)} evidence {item_label})")
        for item in evidence if verbose else evidence[:1]:
            key = f" [{display_text(item['key'])}]" if item["key"] else ""
            print(f"    {LABELS[item['signal']]}{key}: {display_text(item['value'], None if verbose else 180)}")
            if verbose:
                print(f"      Source: {display_text(item['source_url'], None)}")
                print(f"      Rule: {display_text(item['rule']['id'])} /{display_text(item['rule']['pattern'], None)}/")
    if not report["technologies"]:
        print("No supported technology fingerprints matched the observed page.")


def positive_seconds(value: str) -> float:
    try:
        number = float(value)
        if not 0 < number <= 120:
            raise ValueError
        return number
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number greater than 0 and at most 120 seconds") from exc


def settle_seconds(value: str) -> float:
    try:
        number = float(value)
        if not 0 <= number <= 30:
            raise ValueError
        return number
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number between 0 and 30 seconds") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Identify observable website technologies with supporting evidence.")
    parser.add_argument("url", help="HTTP(S) URL, or hostname (defaults to HTTPS)")
    parser.add_argument("-o", "--output", type=Path, default=Path("report.json"), help="JSON report path (default: report.json)")
    parser.add_argument("--fingerprints", type=Path, default=DEFAULT_FINGERPRINTS, help="Custom fingerprint JSON file")
    parser.add_argument("--timeout", type=positive_seconds, default=20.0, metavar="SECONDS", help="Navigation timeout (default: 20)")
    parser.add_argument("--wait", type=settle_seconds, default=2.5, metavar="SECONDS", help="Observe dynamic activity after navigation (default: 2.5)")
    parser.add_argument("--headed", action="store_true", help="Show the browser during the scan")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print every evidence item and its rule")
    args = parser.parse_args(argv)
    try:
        detector = Detector(args.fingerprints)
        scan = scan_website(
            args.url, global_paths=detector.global_paths,
            timeout_ms=max(1, int(args.timeout * 1000)), settle_ms=int(args.wait * 1000),
            headless=not args.headed,
        )
        report = detector.report(scan)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (ValueError, OSError, ScanError) as exc:
        print(f"Error: {display_text(str(exc), None)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Scan interrupted.", file=sys.stderr)
        return 130
    print_report(report, verbose=args.verbose)
    print(f"\nJSON report: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
