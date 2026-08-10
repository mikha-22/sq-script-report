#!/usr/bin/env python3
"""
report_metrics_from_issues.py

Extracts ALL issue-based numbers for the CISO monthly report from the
issue fetcher CSVs (AUTHORITATIVE for issue data).

  From OPEN issues csv (current snapshot):
  - Section 3.1   Security Issues table (severity matrix + rating, WITH DELTAS)
  - Section 3.1.1 Critical & High vulnerability breakdown by rule
  - Section 3.1.2 Unreviewed Security Hotspots by rule (+ per-project totals)
  - Section 3.2   Reliability Issues table (with deltas)
  - Section 3.3   Maintainability Issues table (with deltas + INFO footnote)

  From OPEN + CLOSED csvs (period flow):
  - Section 2.1   New Issues   : created_at inside the period (both files;
                                 created-and-closed-in-period counts as new)
  - Section 2.1   Closed Issues: resolution_date inside the period
  - Section 2.2   Resolutions  : Fixed / False Positive / Accepted
                                 + hotspot-only Safe / Fixed / Acknowledged

  DELTAS (the (+-x) in section 3):
  Reconstructed from the fetcher's rolling ~3-month history - no baseline
  files needed. An issue counts as OPEN at the previous snapshot date S
  (= day before --start) if:
      created_at <= S  AND  ( still open now  OR  resolution_date > S )
  Caveat: issues created AND resolved before the fetcher's backdate window
  are invisible, but S is always inside the rolling window in practice.

  NOT produced here (by design): LoC / license / last scan / language /
  duplication / QG / QP -> monthly_platform_snapshot.py

  File names may use either date separator (2026-07-05 or 2026_07_05);
  the script retries with the flipped separator automatically.

Usage:
  python report_metrics_from_issues.py \
      --open   open_issues_2026-07-05.csv \
      --closed closed_issues_2026-07-05.csv \
      --start  2026-06-06 --end 2026-07-05
"""

import csv
import os
import re
import argparse
from datetime import datetime, date, timedelta
from collections import defaultdict

# --- normalisation maps
TYPE_MAP = {
    "vulnerability":        "Vulnerabilities",
    "security_hotspot":     "Security Hotspots",
    "reliability_issue":    "Reliability Issues",
    "maintainability_issue":"Maintainability Issues",
}
TYPE_ORDER = ["Vulnerabilities", "Security Hotspots", "Reliability Issues", "Maintainability Issues"]

SEV_MAP = {"blocker": "CRITICAL", "critical": "CRITICAL", "high": "HIGH", "major": "HIGH",
           "medium": "MEDIUM", "minor": "MEDIUM", "low": "LOW", "info": "INFO"}
SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]   # INFO tracked separately

# rating per report section 3 definition: worst severity present (INFO excluded)
RATING_BY_WORST = {"CRITICAL": "E", "HIGH": "D", "MEDIUM": "C", "LOW": "B"}

REGULAR_RES = {"fixed": "Fixed", "false positive": "False Positive", "false-positive": "False Positive",
               "accepted": "Accepted", "wontfix": "Accepted", "won't fix": "Accepted"}
HOTSPOT_RES = {"safe": "Safe", "fixed": "Fixed", "acknowledged": "Acknowledged"}


def parse_ts(ts):
    """Parse ISO 8601 like 2026-07-03T17:04:07+0700 -> date (None if empty/unparseable)."""
    if not ts or not ts.strip():
        return None
    ts = ts.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return None


def resolve_path(path):
    """Accept the path as given; if missing, retry with the date's
    separator flipped (2026-07-05 <-> 2026_07_05)."""
    if os.path.isfile(path):
        return path
    m = re.search(r"(\d{4})([-_])(\d{2})([-_])(\d{2})", path)
    if m:
        flip = "_" if m.group(2) == "-" else "-"
        alt = (path[:m.start()]
               + m.group(1) + flip + m.group(3) + flip + m.group(5)
               + path[m.end():])
        if os.path.isfile(alt):
            print(f"  (note: using '{alt}' - '{path}' not found)")
            return alt
    return path  # let open() raise the original error


def load_csv(path):
    with open(resolve_path(path), newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def norm_type(raw):
    return TYPE_MAP.get((raw or "").strip().lower(), None)


def norm_sev(raw):
    return SEV_MAP.get((raw or "").strip().lower(), None)


def in_period(d, start, end):
    return d is not None and start <= d <= end


def rating_for(sev_counts):
    for sev in SEV_ORDER:                      # worst first
        if sev_counts.get(sev, 0) > 0:
            return RATING_BY_WORST[sev]
    return "A"


def cell(cur, prev):
    delta = cur - prev
    return f"{cur} ({delta:+})" if delta else f"{cur} (0)"


def aggregate_snapshot(rows):
    """Build severity/hotspot structures from a list of issue rows."""
    sev  = {t: defaultdict(lambda: defaultdict(int)) for t in TYPE_ORDER}
    info = {t: defaultdict(int) for t in TYPE_ORDER}
    hotspot_proj = defaultdict(int)
    hotspot_rule = defaultdict(lambda: defaultdict(int))
    hotspot_msg  = {}
    vuln_chi     = defaultdict(lambda: defaultdict(int))   # (project, sev) -> rule -> n
    vuln_chi_msg = {}
    skipped_type, skipped_sev = defaultdict(int), defaultdict(int)

    for r in rows:
        t = norm_type(r.get("type"))
        if not t:
            skipped_type[r.get("type", "")] += 1
            continue
        p = r.get("project_key", "?")
        s = norm_sev(r.get("severity"))
        if not s:
            skipped_sev[r.get("severity", "")] += 1
            continue
        if s == "INFO":
            info[t][p] += 1
            continue
        sev[t][p][s] += 1

        if t == "Security Hotspots":
            rule = r.get("rule", "UNKNOWN")
            hotspot_proj[p] += 1
            hotspot_rule[p][rule] += 1
            hotspot_msg.setdefault((p, rule), r.get("message", ""))
        if t == "Vulnerabilities" and s in ("CRITICAL", "HIGH"):
            rule = r.get("rule", "UNKNOWN")
            vuln_chi[(p, s)][rule] += 1
            vuln_chi_msg.setdefault((p, s, rule), r.get("message", ""))

    return {"sev": sev, "info": info, "hotspot_proj": hotspot_proj,
            "hotspot_rule": hotspot_rule, "hotspot_msg": hotspot_msg,
            "vuln_chi": vuln_chi, "vuln_chi_msg": vuln_chi_msg,
            "skipped_type": skipped_type, "skipped_sev": skipped_sev}


def snapshot_rows_at(open_rows, closed_rows, snap_date):
    """Rows representing issues OPEN at snap_date (reconstructed from history)."""
    rows = []
    for r in open_rows:
        cd = parse_ts(r.get("created_at"))
        if cd and cd <= snap_date:
            rows.append(r)                       # created before S, still open now
    for r in closed_rows:
        cd = parse_ts(r.get("created_at"))
        rd = parse_ts(r.get("resolution_date"))
        if cd and cd <= snap_date and rd and rd > snap_date:
            rows.append(r)                       # open at S, resolved afterwards
    return rows


def print_table(title, header, rows):
    print(f"\n## {title}")
    if not rows:
        print("  (no rows)")
        return
    widths = [max(len(str(r[i])) for r in [header] + rows) for i in range(len(header))]
    fmt = " | ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(fmt.format(*r))


def print_type_matrix(title, counts, projects):
    header = ["Project"] + TYPE_ORDER + ["TOTAL"]
    rows = []
    col_tot = defaultdict(int)
    for p in projects:
        row = [p]
        row_total = 0
        for t in TYPE_ORDER:
            v = counts[p].get(t, 0)
            row.append(str(v))
            row_total += v
            col_tot[t] += v
        row.append(str(row_total))
        rows.append(row)
    rows.append(["TOTAL"] + [str(col_tot[t]) for t in TYPE_ORDER] +
                [str(sum(col_tot.values()))])
    print_table(title, header, rows)


def print_sev_matrix(title, sev_counts, info_counts, projects, prev_sev=None):
    """Section 3 style: Critical/High/Medium/Low/Total/Rating with (+-x) deltas."""
    header = ["Project"] + SEV_ORDER + ["Total", "Rating"]
    rows = []
    for p in projects:
        sc  = sev_counts[p]
        psc = prev_sev[p] if prev_sev else defaultdict(int)
        total      = sum(sc.get(s, 0) for s in SEV_ORDER)
        prev_total = sum(psc.get(s, 0) for s in SEV_ORDER)
        rating      = rating_for(sc)
        prev_rating = rating_for(psc) if prev_sev else None
        rows.append([p] +
                    [cell(sc.get(s, 0), psc.get(s, 0)) if prev_sev else str(sc.get(s, 0))
                     for s in SEV_ORDER] +
                    [cell(total, prev_total) if prev_sev else str(total),
                     f"{rating} ({prev_rating})" if prev_sev else rating])
    print_table(title, header, rows)
    infos = {p: info_counts[p] for p in projects if info_counts.get(p, 0)}
    if infos:
        detail = ", ".join(f"{p}: {n}" for p, n in sorted(infos.items()))
        print(f"  INFO-severity (tidak ditampilkan pada tabel): {detail}")


def main():
    ap = argparse.ArgumentParser(description="Extract CISO report issue data from issue fetcher CSVs.")
    ap.add_argument("--open",   required=True, help="Open issues CSV (from the issue fetcher)")
    ap.add_argument("--closed", required=True, help="Closed issues CSV (from the issue fetcher)")
    ap.add_argument("--start",  required=True, help="Period start YYYY-MM-DD (e.g. 2026-06-06)")
    ap.add_argument("--end",    required=True, help="Period end   YYYY-MM-DD (e.g. 2026-07-05)")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    prev_snap = start - timedelta(days=1)        # e.g. 2026-06-05

    open_rows   = load_csv(args.open)
    closed_rows = load_csv(args.closed)

    # ================= SECTION 3 (snapshots + deltas) =================
    cur  = aggregate_snapshot(open_rows)
    prev = aggregate_snapshot(snapshot_rows_at(open_rows, closed_rows, prev_snap))

    snap_projects = sorted({p for t in TYPE_ORDER
                            for p in set(list(cur["sev"][t]) + list(prev["sev"][t]))})

    # ================= SECTION 2 (flow from open+closed) =================
    new_counts    = defaultdict(lambda: defaultdict(int))
    closed_counts = defaultdict(lambda: defaultdict(int))
    regular_res   = defaultdict(int)
    hotspot_res   = defaultdict(int)
    created_and_closed = 0

    for rows in (open_rows, closed_rows):
        for r in rows:
            t = norm_type(r.get("type"))
            if not t:
                continue
            if in_period(parse_ts(r.get("created_at")), start, end):
                new_counts[r.get("project_key", "?")][t] += 1

    for r in closed_rows:
        t = norm_type(r.get("type"))
        if not t:
            continue
        if not in_period(parse_ts(r.get("resolution_date")), start, end):
            continue
        proj = r.get("project_key", "?")
        closed_counts[proj][t] += 1
        if in_period(parse_ts(r.get("created_at")), start, end):
            created_and_closed += 1
        res = (r.get("resolution") or "").strip().lower()
        if t == "Security Hotspots":
            label = HOTSPOT_RES.get(res)
            if label:
                hotspot_res[label] += 1
        else:
            label = REGULAR_RES.get(res)
            if label:
                regular_res[label] += 1

    flow_projects = sorted(set(list(new_counts) + list(closed_counts)))

    # ================= OUTPUT =================
    print("=" * 78)
    print(f"CISO REPORT ISSUE DATA  |  period {start} -> {end}")
    print(f"Delta baseline        |  open-state snapshot reconstructed at {prev_snap}")
    print("=" * 78)
    print(f"Open CSV rows: {len(open_rows)} | Closed CSV rows: {len(closed_rows)}")
    print(f"(created AND closed within period: {created_and_closed} - counted as both new+closed)")
    if cur["skipped_type"]:
        print(f"WARNING: unmapped type labels skipped: {dict(cur['skipped_type'])}")
    if cur["skipped_sev"]:
        print(f"WARNING: unmapped severity labels skipped: {dict(cur['skipped_sev'])}")

    print_type_matrix("SECTION 2.1 - NEW ISSUES (created_at in period)", new_counts, flow_projects)
    print_type_matrix("SECTION 2.1 - CLOSED ISSUES (resolution_date in period)", closed_counts, flow_projects)

    print(f"\n## SECTION 2.2 - CLOSED ISSUE RESOLUTIONS (non-hotspot)")
    for label in ("Fixed", "False Positive", "Accepted"):
        print(f"  {label:<15} {regular_res.get(label, 0)}")
    print(f"\n## SECTION 2.2 - CLOSED SECURITY HOTSPOT RESOLUTIONS")
    for label in ("Safe", "Fixed", "Acknowledged"):
        print(f"  {label:<15} {hotspot_res.get(label, 0)}")

    print_sev_matrix("SECTION 3.1 - SECURITY ISSUES (open vulnerabilities)",
                     cur["sev"]["Vulnerabilities"], cur["info"]["Vulnerabilities"],
                     snap_projects, prev["sev"]["Vulnerabilities"])

    # unreviewed hotspots per project with delta (for the 3.1 table's last column)
    print("\n## SECTION 3.1 - UNREVIEWED HOTSPOTS per project (with delta)")
    hs_projs = sorted(set(list(cur["hotspot_proj"]) + list(prev["hotspot_proj"])))
    for p in hs_projs:
        print(f"  {p:<25} {cell(cur['hotspot_proj'].get(p, 0), prev['hotspot_proj'].get(p, 0))}")
    print(f"  {'TOTAL':<25} {cell(sum(cur['hotspot_proj'].values()), sum(prev['hotspot_proj'].values()))}")

    # 3.1.1 Critical & High vulnerability breakdown by rule (current only)
    rows = []
    for (p, s), rules in sorted(cur["vuln_chi"].items(),
                                key=lambda kv: (kv[0][0], SEV_ORDER.index(kv[0][1]))):
        for rule, n in sorted(rules.items(), key=lambda kv: -kv[1]):
            rows.append([p, s, rule, str(n), cur["vuln_chi_msg"].get((p, s, rule), "")[:80]])
    tot_c = sum(sum(r.values()) for (p, s), r in cur["vuln_chi"].items() if s == "CRITICAL")
    tot_h = sum(sum(r.values()) for (p, s), r in cur["vuln_chi"].items() if s == "HIGH")
    print_table(f"SECTION 3.1.1 - CRITICAL & HIGH VULNERABILITIES (total: {tot_c} Critical | {tot_h} High)",
                ["Project", "Severity", "Rule", "Count", "Message (truncated)"], rows)

    # 3.1.2 hotspots by rule (current only)
    rows = []
    for p in sorted(cur["hotspot_rule"]):
        for rule, n in sorted(cur["hotspot_rule"][p].items(), key=lambda kv: -kv[1]):
            rows.append([p, rule, str(n), cur["hotspot_msg"].get((p, rule), "")[:80]])
    per_proj = ", ".join(f"{p}: {cur['hotspot_proj'][p]}" for p in sorted(cur["hotspot_proj"]))
    print_table(f"SECTION 3.1.2 - UNREVIEWED HOTSPOTS BY RULE (total {sum(cur['hotspot_proj'].values())}: {per_proj})",
                ["Project", "Rule", "Count", "Message (truncated)"], rows)

    print_sev_matrix("SECTION 3.2 - RELIABILITY ISSUES (open)",
                     cur["sev"]["Reliability Issues"], cur["info"]["Reliability Issues"],
                     snap_projects, prev["sev"]["Reliability Issues"])
    print_sev_matrix("SECTION 3.3 - MAINTAINABILITY ISSUES (open)",
                     cur["sev"]["Maintainability Issues"], cur["info"]["Maintainability Issues"],
                     snap_projects, prev["sev"]["Maintainability Issues"])

    print("\nDone.")


if __name__ == "__main__":
    main()
