"""
Compare Python script findings against Claude's independent findings.
Outputs a list of discrepancy strings for the email and console.
"""

import csv
import json
from pathlib import Path


def load_csv_results(csv_path: Path) -> dict[str, dict]:
    """
    Load a results CSV produced by check_events.py.
    Expected columns: underlying, event_type, amount_or_ratio, sources, originals
    Returns {underlying: {event_type, amount_or_ratio, sources, originals}}
    """
    results: dict[str, dict] = {}
    if not csv_path.exists():
        return results
    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = f"{row['underlying']}|{row['event_type']}"
            results[key] = dict(row)
    return results


def load_claude_results(json_path: Path) -> dict[str, dict]:
    """
    Load Claude's findings from a JSON file that Claude writes during its workflow.
    Format: [{"underlying": "AAPL", "event_type": "dividend", "amount_or_ratio": "0.25", ...}, ...]
    """
    if not json_path.exists():
        return {}
    with open(json_path) as f:
        data = json.load(f)
    results: dict[str, dict] = {}
    for item in data:
        key = f"{item['underlying']}|{item['event_type']}"
        results[key] = item
    return results


def compare(
    python_results: dict[str, dict],
    claude_results: dict[str, dict],
) -> tuple[list[str], list[str]]:
    """
    Returns (discrepancies, agreements).

    discrepancies : list of human-readable strings describing conflicts
    agreements    : list of strings describing confirmed findings
    """
    discrepancies: list[str] = []
    agreements:    list[str] = []

    all_keys = set(python_results) | set(claude_results)

    for key in sorted(all_keys):
        underlying, event_type = key.split('|', 1)
        in_python = key in python_results
        in_claude = key in claude_results

        if in_python and in_claude:
            agreements.append(
                f"{underlying} — {event_type} confirmed by both Python and Claude"
            )
        elif in_claude and not in_python:
            info = claude_results[key]
            discrepancies.append(
                f"CLAUDE ONLY: {underlying} has a {event_type} "
                f"(amount/ratio: {info.get('amount_or_ratio', '?')}) — "
                f"Python script did NOT find this. VERIFY MANUALLY."
            )
        elif in_python and not in_claude:
            info = python_results[key]
            discrepancies.append(
                f"PYTHON ONLY: {underlying} has a {event_type} "
                f"(amount/ratio: {info.get('amount_or_ratio', '?')}) — "
                f"Claude did NOT find this. VERIFY MANUALLY."
            )

    return discrepancies, agreements


def print_comparison(discrepancies: list[str], agreements: list[str]) -> None:
    if agreements:
        print("\n[OK] AGREED:")
        for a in agreements:
            print(f"  {a}")
    if discrepancies:
        print("\n[!!] DISCREPANCIES (MANUAL VERIFICATION REQUIRED):")
        for d in discrepancies:
            print(f"  {d}")
    if not discrepancies:
        print("\n[OK] Python and Claude agree on all findings.")
