"""A small, data-driven regex matcher with explicit evidence for every result."""

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re

from scanner import Observation, ScanResult, SIGNAL_TYPES


DEFAULT_FINGERPRINTS = Path(__file__).with_name("fingerprints.json")


@dataclass(frozen=True)
class Rule:
    definition: dict
    regex: re.Pattern


class Detector:
    def __init__(self, path: str | Path = DEFAULT_FINGERPRINTS):
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Fingerprint file must have schema_version: 1.")
        technologies = data.get("technologies")
        if not isinstance(technologies, list) or not technologies:
            raise ValueError("Fingerprint file must contain a non-empty technologies list.")
        self.technologies: list[tuple[str, str, list[Rule]]] = []
        self.global_paths: list[str] = []
        names: set[str] = set()
        rule_ids: set[str] = set()
        for technology in technologies:
            if not isinstance(technology, dict):
                raise ValueError("Each technology must be an object.")
            name, category = technology.get("name"), technology.get("category")
            if not all(isinstance(value, str) and value.strip() for value in (name, category)):
                raise ValueError("Each technology needs a non-empty name and category.")
            if name.casefold() in names:
                raise ValueError(f"Duplicate technology: {name}")
            names.add(name.casefold())
            definitions = technology.get("rules")
            if not isinstance(definitions, list) or not definitions:
                raise ValueError(f"{name} needs a non-empty rules list.")
            rules = []
            for definition in definitions:
                if not isinstance(definition, dict):
                    raise ValueError(f"{name}: every rule must be an object.")
                rule_id = definition.get("id")
                signal, pattern = definition.get("signal"), definition.get("pattern")
                if not isinstance(rule_id, str) or not rule_id.strip() or rule_id in rule_ids:
                    raise ValueError(f"{name}: rule IDs must be non-empty and globally unique.")
                rule_ids.add(rule_id)
                if signal not in SIGNAL_TYPES:
                    raise ValueError(f"{rule_id}: unknown signal {signal!r}.")
                if not isinstance(pattern, str) or not pattern:
                    raise ValueError(f"{rule_id}: pattern must be a non-empty regex string.")
                key = definition.get("key")
                if key is not None and (not isinstance(key, str) or not key):
                    raise ValueError(f"{rule_id}: key must be a non-empty string.")
                case_sensitive = definition.get("case_sensitive", False)
                if not isinstance(case_sensitive, bool):
                    raise ValueError(f"{rule_id}: case_sensitive must be a boolean.")
                if signal == "globals":
                    if not key or not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", key):
                        raise ValueError(f"{rule_id}: globals need a dotted property path in key.")
                    if key not in self.global_paths:
                        self.global_paths.append(key)
                try:
                    regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
                except re.error as exc:
                    raise ValueError(f"{rule_id}: invalid regex: {exc}") from exc
                rules.append(Rule(definition.copy(), regex))
            self.technologies.append((name, category, rules))

    def detect(self, signals: Mapping) -> list[dict]:
        """Rules are OR-ed. Keep every distinct rule/source/value match.

        Missing/invalid observations are ignored. Malformed fingerprint files,
        in contrast, fail early rather than silently reducing detection coverage.
        """
        if not isinstance(signals, Mapping):
            raise ValueError("signals must be a mapping of signal names to observations.")
        detected = []
        for name, category, rules in self.technologies:
            evidence = []
            seen = set()
            for rule in rules:
                definition = rule.definition
                signal = definition["signal"]
                observations = signals.get(signal) or []
                if not isinstance(observations, (list, tuple)):
                    continue
                for observation in observations:
                    if not isinstance(observation, Observation) or not isinstance(observation.value, str):
                        continue
                    key = definition.get("key")
                    if key is not None:
                        # HTML header/meta names are case-insensitive; JS names are not.
                        observed_key = observation.key
                        if signal in {"headers", "meta"}:
                            if not isinstance(observed_key, str) or observed_key.casefold() != key.casefold():
                                continue
                        elif observed_key != key:
                            continue
                    match = rule.regex.search(observation.value)
                    if match is None:
                        continue
                    identity = (
                        definition["id"], observation.source_url, observation.key, observation.value,
                        tuple(sorted(observation.details.items())),
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    value = observation.value
                    if signal == "html":
                        start, end = max(0, match.start() - 80), min(len(value), match.end() + 80)
                        value = ("..." if start else "") + value[start:end] + ("..." if end < len(value) else "")
                    item = {
                        "signal": signal,
                        "key": observation.key,
                        "value": value,
                        "matched_text": match.group(0),
                        "source_url": observation.source_url,
                        "rule": definition.copy(),
                    }
                    if observation.details:
                        item["details"] = observation.details.copy()
                    if signal == "html":
                        item["match_span"] = [match.start(), match.end()]
                    evidence.append(item)
            if evidence:
                detected.append({"name": name, "category": category, "evidence": evidence})
        return sorted(detected, key=lambda item: (item["category"].casefold(), item["name"].casefold()))

    def report(self, scan: ScanResult) -> dict:
        return {
            "schema_version": 1,
            "requested_url": scan.requested_url,
            "final_url": scan.final_url,
            "scanned_at": scan.scanned_at,
            "status_code": scan.status_code,
            "title": scan.title,
            "warnings": scan.warnings,
            "signal_counts": {name: len(scan.signals.get(name, [])) for name in SIGNAL_TYPES},
            "fingerprint_count": len(self.technologies),
            "technologies": self.detect(scan.signals),
        }
