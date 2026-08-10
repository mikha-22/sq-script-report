#!/usr/bin/env python3
"""
csv_stratified_sampler.py

Samples a CSV by VARIETY instead of position: groups rows by the unique
combination of key columns and takes up to N rows per combination.

Defaults (auto-detected from the file's headers, in this order):
  project_key, type, severity, resolution
plus the first date-like column found (created_at / resolution_date) is
bucketed by MONTH and added to the combination, so the sample also
spans old/new rows (in-period and out-of-period).

Usage:
  python csv_stratified_sampler.py open_issues_2026-07-05.csv
  python csv_stratified_sampler.py closed_issues_2026-07-05.csv -n 3
  python csv_stratified_sampler.py file.csv --by project_key,type
  python csv_stratified_sampler.py file.csv --no-date-bucket

Output: <originalname>_sample.csv next to the input.
"""

import os
import csv
import argparse
from collections import defaultdict

CANDIDATE_COLS = ["project_key", "type", "severity", "resolution"]
DATE_COLS      = ["created_at", "resolution_date"]


def month_bucket(value):
    """'2026-07-03T17:04:07+0700' -> '2026-07' ; empty/unparseable -> 'nodate'."""
    v = (value or "").strip()
    return v[:7] if len(v) >= 7 and v[4] == "-" else "nodate"


def sample(input_path, n, by_cols, use_date_bucket):
    if not os.path.isfile(input_path):
        print(f"ERROR: file not found: {input_path}")
        return

    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        print(f"ERROR: '{input_path}' has no data rows.")
        return

    # pick stratification columns
    if by_cols:
        cols = [c for c in by_cols if c in headers]
        missing = [c for c in by_cols if c not in headers]
        if missing:
            print(f"WARNING: columns not in file, ignored: {missing}")
    else:
        cols = [c for c in CANDIDATE_COLS if c in headers]
    if not cols:
        print(f"ERROR: none of the stratification columns exist. Headers: {headers}")
        return

    date_col = next((c for c in DATE_COLS if c in headers), None) if use_date_bucket else None

    # group
    groups = defaultdict(list)
    for r in rows:
        key = tuple(r.get(c, "") for c in cols)
        if date_col:
            key += (month_bucket(r.get(date_col)),)
        groups[key].append(r)

    # take up to N per group, preserve original order in output
    keep = set()
    for key, group_rows in groups.items():
        for i, r in enumerate(group_rows):
            if i < n:
                keep.add(id(r))
    sampled = [r for r in rows if id(r) in keep]

    base, ext = os.path.splitext(input_path)
    output_path = f"{base}_sample{ext or '.csv'}"
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(sampled)

    strat_desc = list(cols) + ([f"{date_col}(month)"] if date_col else [])
    print(f"Input : {input_path} ({len(rows)} rows)")
    print(f"Strat : {', '.join(strat_desc)} -> {len(groups)} unique combinations")
    print(f"Output: {output_path} ({len(sampled)} rows, up to {n} per combination)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Stratified CSV sampler - samples by value variety.")
    ap.add_argument("input", help="Path to source CSV")
    ap.add_argument("-n", type=int, default=2,
                    help="Max rows per combination (default: 2)")
    ap.add_argument("--by", help="Comma-separated stratification columns "
                                 "(default: auto from project_key,type,severity,resolution)")
    ap.add_argument("--no-date-bucket", action="store_true",
                    help="Don't add the date column's month to the combination")
    args = ap.parse_args()

    by = [c.strip() for c in args.by.split(",")] if args.by else None
    sample(args.input, args.n, by, not args.no_date_bucket)
