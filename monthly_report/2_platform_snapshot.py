#!/usr/bin/env python3
"""
monthly_platform_snapshot.py  (trimmed Script 2)

Fetches ONLY the platform/config data the CISO report needs that the
issue fetcher CSVs do NOT provide:

  - project list + Active/Inactive status (90-day rule)
  - last scan date per project
  - LoC (ncloc) per project + license usage totals
  - primary language
  - duplicated_lines_density  (section 3.3)
  - Quality Gate + Quality Profile per project  (section 1.5)
  - alert_status (QG pass/fail on main branch, for highlights)

ALL issue data (severities, hotspots, new/closed, resolutions) comes from
the issue fetcher CSVs via report_metrics_from_issues.py - NOT from here.

Output (into results/YYYY_MM/):
  platform_report_YYYY_MM_DD.csv    - one row per project, everything above.
                                      NEXT month, pass THIS file as --baseline:
                                      the delta logic only reads the columns
                                      project_key / ncloc / duplicated_lines_density,
                                      so the report file doubles as its own baseline.

Env:  SONAR_URL (default http://localhost:9000), SONAR_TOKEN (required)
Usage:
  python monthly_platform_snapshot.py --date 2026-07-05
  python monthly_platform_snapshot.py --date 2026-08-05 --baseline results/2026_07/platform_report_2026_07_05.csv
"""

import os
import sys
import csv
import json
import argparse
import urllib.request
import urllib.parse
import base64
from datetime import datetime, timezone, date, timedelta

# =========================================================
# Args & dates
# =========================================================

parser = argparse.ArgumentParser(description="Fetch platform/config snapshot for the CISO report.")
parser.add_argument("--date",     type=str, help="Report date YYYY-MM-DD (default: today)")
parser.add_argument("--baseline", type=str, help="Last month's platform_report CSV (for LoC/dup deltas)")
args = parser.parse_args()

if args.date:
    try:
        TODAY = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        print("FATAL: invalid date, use YYYY-MM-DD")
        sys.exit(1)
else:
    TODAY = date.today()

DATE_STR  = TODAY.strftime("%Y_%m_%d")
MONTH_STR = TODAY.strftime("%Y_%m")

# =========================================================
# Env & paths
# =========================================================

SONAR_URL   = os.environ.get("SONAR_URL", "http://localhost:9000").rstrip("/")
SONAR_TOKEN = os.environ.get("SONAR_TOKEN")
if not SONAR_TOKEN:
    print("FATAL: missing SONAR_TOKEN env var")
    sys.exit(1)

OUTPUT_DIR = os.path.join("results", MONTH_STR)
os.makedirs(OUTPUT_DIR, exist_ok=True)

REPORT_CSV = os.path.join(OUTPUT_DIR, f"platform_report_{DATE_STR}.csv")

PREV_BASELINE = args.baseline
if PREV_BASELINE and not os.path.exists(PREV_BASELINE):
    print(f"WARNING: baseline not found: {PREV_BASELINE} - deltas skipped")
    PREV_BASELINE = None

LICENSE_LOC = 1_000_000   # license cap, adjust if tier changes

# =========================================================
# API helper
# =========================================================

def api_get(endpoint, params=None):
    url = f"{SONAR_URL}{endpoint}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    b64 = base64.b64encode(f"{SONAR_TOKEN}:".encode("ascii")).decode("ascii")
    req.add_header("Authorization", f"Basic {b64}")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except Exception as e:
        print(f"  API Error at {url}: {e}")
        return None

# =========================================================
# Helpers
# =========================================================

def scan_status(date_str):
    """Active if last scan within 90 days of TODAY, else Inactive."""
    if not date_str:
        return "Inactive", "Never"
    try:
        last = datetime.strptime(str(date_str)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        status = "Active" if (TODAY - last.date()).days <= 90 else "Inactive"
        return status, str(date_str)
    except Exception:
        return "Inactive", "Never"


def primary_language(lang_dist):
    if not lang_dist:
        return "N/A"
    best_lang, best_loc = "N/A", -1
    for part in str(lang_dist).split(";"):
        if "=" in part:
            lang, loc = part.split("=")
            try:
                loc = int(loc)
            except ValueError:
                continue
            if loc > best_loc:
                best_lang, best_loc = lang.upper(), loc
    return best_lang


def fetch_qg(pkey):
    data = api_get("/api/qualitygates/get_by_project", {"project": pkey})
    if data and "qualityGate" in data:
        qg = data["qualityGate"]
        name = qg.get("name", "N/A")
        if not qg.get("default", False) and name.lower() != "sonar way":
            return f"{name} (Custom)"
        return name
    return "N/A"


def fetch_qp(pkey):
    """Deduped profile names (the old script listed 'Sonar way' once per language)."""
    data = api_get("/api/qualityprofiles/search", {"project": pkey})
    if data and "profiles" in data:
        names = []
        for p in data["profiles"]:
            n = p.get("name", "")
            if n and n not in names:
                names.append(n)
        custom = [n for n in names if n.lower() != "sonar way"]
        if custom:
            return ", ".join(custom) + " (Custom)"
        return "Sonar way"
    return "N/A"

# =========================================================
# Main
# =========================================================

def main():
    print(f"SonarQube : {SONAR_URL}")
    print(f"Date      : {TODAY} (status cutoff: scanned within 90 days)")
    print(f"Baseline  : {PREV_BASELINE or 'none (deltas skipped)'}")

    projects = {}

    # 1. project list
    print("\nMengambil daftar project...")
    page = 1
    while True:
        data = api_get("/api/components/search", {"qualifiers": "TRK", "p": page, "ps": 500})
        if not data or not data.get("components"):
            break
        for comp in data["components"]:
            projects[comp["key"]] = {
                "project_key": comp["key"],
                "name":        comp.get("name", ""),
            }
        if page * 500 >= data.get("paging", {}).get("total", 0):
            break
        page += 1

    # 2. per-project platform metrics
    print("Mengambil last scan, LoC, duplication, QG/QP...")
    for pkey, d in projects.items():
        # last analysis
        ana = api_get("/api/project_analyses/search", {"project": pkey, "ps": 1})
        last = ""
        if ana and ana.get("analyses"):
            last = ana["analyses"][0].get("date", "")
        d["status"], d["last_analysis"] = scan_status(last)

        # measures: LoC, language, duplication, QG status
        meas = api_get("/api/measures/component", {
            "component": pkey,
            "metricKeys": "ncloc,ncloc_language_distribution,duplicated_lines_density,alert_status",
        })
        m = {}
        if meas:
            m = {x["metric"]: x.get("value") for x in meas.get("component", {}).get("measures", [])}
        d["ncloc"]     = int(m.get("ncloc") or 0)
        d["primary_language"] = primary_language(m.get("ncloc_language_distribution"))
        try:
            d["duplicated_lines_density"] = round(float(m.get("duplicated_lines_density") or 0) / 100, 3)
        except (ValueError, TypeError):
            d["duplicated_lines_density"] = "N/A"
        d["alert_status"] = m.get("alert_status", "N/A")

        # config
        d["quality_gate"]    = fetch_qg(pkey)
        d["quality_profile"] = fetch_qp(pkey)

        d["ncloc_delta"] = "N/A"
        d["dup_delta"]   = "N/A"

    # 3. deltas vs previous baseline
    if PREV_BASELINE:
        print(f"Menghitung delta dari {PREV_BASELINE}...")
        with open(PREV_BASELINE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pkey = row.get("project_key")
                if pkey not in projects:
                    continue
                d = projects[pkey]
                try:
                    d["ncloc_delta"] = d["ncloc"] - int(row.get("ncloc") or 0)
                except (ValueError, TypeError):
                    pass
                try:
                    d["dup_delta"] = round(d["duplicated_lines_density"]
                                           - float(row.get("duplicated_lines_density") or 0), 3)
                except (ValueError, TypeError):
                    pass

    # 4. write report CSV
    fields = ["project_key", "name", "status", "last_analysis", "ncloc", "ncloc_delta",
              "primary_language", "duplicated_lines_density", "dup_delta",
              "quality_gate", "quality_profile", "alert_status"]
    rows = [projects[k] for k in sorted(projects)]
    with open(REPORT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # 5. summary
    total_loc = sum(d["ncloc"] for d in projects.values())
    active    = [d for d in projects.values() if d["status"] == "Active"]
    inactive  = [d for d in projects.values() if d["status"] == "Inactive"]
    inact_loc = sum(d["ncloc"] for d in inactive)

    print("\n" + "=" * 60)
    print(f"PLATFORM SUMMARY: {TODAY.strftime('%d %b %Y')}")
    print("=" * 60)
    print(f"Projects        : {len(projects)} ({len(active)} Active, {len(inactive)} Inactive)")
    print(f"Total LoC       : {total_loc:,} / {LICENSE_LOC:,} ({total_loc / LICENSE_LOC:.1%})")
    print(f"Remaining       : {LICENSE_LOC - total_loc:,} LoC")
    print(f"Inactive LoC    : {inact_loc:,}")
    print(f"QG status       : " + ", ".join(f"{d['project_key']}={d['alert_status']}" for d in rows))
    print("=" * 60)
    print(f"\nOutput:\n  {REPORT_CSV}")
    print(f"\n(keep this file - pass it as --baseline next month for LoC/dup deltas)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
