#!/usr/bin/env python3
"""
tools/eval_sold.py - Evaluates dataset records using sold_rules.py functions.

Computes total, correct, accuracy, precision (for non-AVAILABLE detection),
recall, and records any mismatches. Can be imported as evaluate_sold_dataset()
or executed directly from the command line.
"""

import json
import os
import sys
from typing import Any, Dict, List

# Ensure parent directory is on sys.path so sold_rules can be imported
sys_path_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if sys_path_root not in sys.path:
    sys.path.insert(0, sys_path_root)

from sold_rules import (
    AVAILABLE,
    NO_MAIL,
    RESERVED,
    SOLD,
    _REOPEN_REGEX,
    classify_author_pushes,
    classify_text_state,
    classify_title_state,
)


def classify_record(record: Dict[str, Any]) -> str:
    """
    Classify a dataset record into SOLD | RESERVED | NO_MAIL | AVAILABLE.
    Evaluates author pushes (chronological post updates), title, and body lines.
    """
    title = record.get("title", "")
    pushes = record.get("author_pushes", [])
    body_lines = record.get("body_lines", [])

    # 1. Author push comments take precedence as they represent post-creation updates
    if pushes:
        p_state, _ = classify_author_pushes(pushes)
        if p_state != AVAILABLE:
            return p_state
        # Check if pushes explicitly contained a reopen/relist phrase
        if any(_REOPEN_REGEX.search(p) for p in pushes):
            return AVAILABLE

    # 2. Title classification
    if title:
        t_state, _ = classify_title_state(title)
        if t_state != AVAILABLE:
            return t_state

    # 3. Body lines
    for ln in body_lines:
        b_state, _ = classify_text_state(ln, require_short_or_boundary=True)
        if b_state != AVAILABLE:
            return b_state

    return AVAILABLE


def evaluate_sold_dataset(
    jsonl_path: str = "tests/data/sold_labels.jsonl",
) -> Dict[str, Any]:
    """
    Evaluates each record in jsonl_path using sold_rules.
    Computes total, correct, accuracy, precision, recall, and mismatches.
    """
    # Resolve relative paths relative to repository root if needed
    if not os.path.isabs(jsonl_path):
        resolved_path = os.path.join(sys_path_root, jsonl_path)
        if os.path.exists(resolved_path):
            jsonl_path = resolved_path

    if not os.path.exists(jsonl_path):
        raise FileNotFoundError(f"Dataset file not found: {jsonl_path}")

    records = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            records.append(json.loads(line_str))

    total = len(records)
    correct = 0
    tp = 0  # non-AVAILABLE expected & non-AVAILABLE predicted
    fp = 0  # AVAILABLE expected & non-AVAILABLE predicted
    fn = 0  # non-AVAILABLE expected & AVAILABLE predicted
    tn = 0  # AVAILABLE expected & AVAILABLE predicted
    mismatches: List[Dict[str, Any]] = []

    for r in records:
        rec_id = r.get("id", "unknown")
        expected = r.get("expected_state", AVAILABLE)
        predicted = classify_record(r)

        if predicted == expected:
            correct += 1
        else:
            mismatches.append(
                {
                    "id": rec_id,
                    "title": r.get("title", ""),
                    "expected": expected,
                    "predicted": predicted,
                    "note": r.get("note", ""),
                }
            )

        exp_non_avail = expected != AVAILABLE
        pred_non_avail = predicted != AVAILABLE

        if exp_non_avail and pred_non_avail:
            tp += 1
        elif not exp_non_avail and pred_non_avail:
            fp += 1
        elif exp_non_avail and not pred_non_avail:
            fn += 1
        else:
            tn += 1

    accuracy = correct / total if total > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0

    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "mismatches": mismatches,
    }


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "tests/data/sold_labels.jsonl"
    print(f"Evaluating dataset: {path}")
    results = evaluate_sold_dataset(path)

    print("=" * 60)
    print(f"Total records  : {results['total']}")
    print(f"Correct        : {results['correct']}")
    print(f"Accuracy       : {results['accuracy']:.4f} ({results['accuracy'] * 100:.2f}%)")
    print(f"Precision      : {results['precision']:.4f} ({results['precision'] * 100:.2f}%)")
    print(f"Recall         : {results['recall']:.4f} ({results['recall'] * 100:.2f}%)")
    print(f"TP: {results['tp']}, FP: {results['fp']}, FN: {results['fn']}, TN: {results['tn']}")
    print("=" * 60)

    if results["mismatches"]:
        print(f"\n[!] Found {len(results['mismatches'])} mismatches:")
        for m in results["mismatches"]:
            print(f"  - [{m['id']}] Expected: {m['expected']}, Got: {m['predicted']}")
            print(f"    Title: {m['title']}")
            print(f"    Note : {m['note']}")
        sys.exit(1)
    else:
        print("\nAll records classified correctly! (0 mismatches)")


if __name__ == "__main__":
    main()
