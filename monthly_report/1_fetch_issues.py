import os
import sys
import csv
import json
import urllib.request
import urllib.parse
import urllib.error
import base64
import argparse
from datetime import datetime, timezone, date

# =========================================================
# 1. CLI Arguments
# =========================================================
parser = argparse.ArgumentParser(
    description=(
        "Fetch SonarQube open and closed issues.\n\n"
        "No --date  : live snapshot as of today (normal mode)\n"
        "--date     : reconstruct historical state as of that date\n\n"
        "Outputs:\n"
        "  results/YYYY_MM/open_issues_YYYY_MM_DD.csv\n"
        "  results/YYYY_MM/closed_issues_YYYY_MM_DD.csv"
    ),
    formatter_class=argparse.RawDescriptionHelpFormatter
)
parser.add_argument(
    "--date",
    required=False,
    default=None,
    help="Cutoff date: '2026-06-05' or '2026-06-05T23:59:59+0700'. Omit for live snapshot."
)
args = parser.parse_args()

# --- Parse and normalise the cutoff date ---
CUTOFF_DT  = None   # timezone-aware datetime, or None in live mode
date_label = str(date.today())

if args.date:
    raw = args.date.strip()
    if "T" not in raw:
        raw += "T23:59:59+0700"
    try:
        CUTOFF_DT  = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
        date_label = CUTOFF_DT.strftime("%Y-%m-%d")
    except ValueError:
        print("\n[ERROR] Invalid --date format.")
        print("  Expected : YYYY-MM-DD  or  YYYY-MM-DDTHH:MM:SS+ZZZZ")
        print("  Example  : --date 2026-06-05\n")
        sys.exit(1)

HISTORICAL_MODE = CUTOFF_DT is not None

# --- Unified output naming: results/YYYY_MM/, files dated YYYY_MM_DD ---
FILE_DATE = date_label.replace("-", "_")          # "2026-07-05" -> "2026_07_05"
MONTH_STR = date_label[:7].replace("-", "_")      # "2026-07-05" -> "2026_07"

# =========================================================
# 2. Environment
# =========================================================
SONAR_URL   = os.environ.get("SONAR_URL", "http://localhost:9000").rstrip("/")
SONAR_TOKEN = os.environ.get("SONAR_TOKEN")
if not SONAR_TOKEN:
    print("FATAL ERROR: Missing SONAR_TOKEN environment variable.")
    sys.exit(1)

# =========================================================
# 3. Output Paths
# =========================================================
OUTPUT_DIR = os.path.join("results", MONTH_STR)
os.makedirs(OUTPUT_DIR, exist_ok=True)

OPEN_ISSUES_CSV   = os.path.join(OUTPUT_DIR, f"open_issues_{FILE_DATE}.csv")
CLOSED_ISSUES_CSV = os.path.join(OUTPUT_DIR, f"closed_issues_{FILE_DATE}.csv")

# =========================================================
# 4. API Helper
# =========================================================
def api_get(endpoint, params=None, silent_404=False):
    url = f"{SONAR_URL}{endpoint}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    b64_auth = base64.b64encode(
        f"{SONAR_TOKEN}:".encode("ascii")
    ).decode("ascii")
    req.add_header("Authorization", f"Basic {b64_auth}")
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if silent_404 and e.code == 404:
            return None
        print(f"  API Error at {url}: {e}")
        return None
    except Exception as e:
        print(f"  API Error at {url}: {e}")
        return None

# =========================================================
# 5. Project & User Maps
# =========================================================
def fetch_all_project_keys():
    projects = {}
    page = 1
    while True:
        data = api_get(
            "/api/components/search",
            {"qualifiers": "TRK", "p": page, "ps": 500}
        )
        if not data or not data.get("components"):
            break
        for comp in data["components"]:
            projects[comp["key"]] = comp.get("name", comp["key"])
        if page * 500 >= data.get("paging", {}).get("total", 0):
            break
        page += 1
    return projects

def fetch_user_map():
    user_map = {}
    page = 1
    while True:
        data = api_get("/api/users/search", {"p": page, "ps": 500})
        if not data or not data.get("users"):
            break
        for u in data["users"]:
            user_map[u["login"]] = u.get("name", u["login"])
        if page * 500 >= data.get("paging", {}).get("total", 0):
            break
        page += 1
    return user_map

# =========================================================
# 6. Shared Helpers
# =========================================================
SEVERITY_RANK  = {"BLOCKER": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "INFO": 1}
SEVERITY_LABEL = {"BLOCKER": "CRITICAL"}

ISSUE_TYPE_MAP = {
    "BUG":          "reliability_issue",
    "CODE_SMELL":   "maintainability_issue",
    "VULNERABILITY":"vulnerability",
}

RESOLUTION_LABEL = {
    "FIXED":          "Fixed",
    "FALSE-POSITIVE": "False Positive",
    "WONTFIX":        "Accepted",
    "SAFE":           "Safe",
    "ACKNOWLEDGED":   "Acknowledged",
}

def clean_text(text):
    if not isinstance(text, str):
        return text
    return text.replace("\n", " ").replace("\r", " ").strip()

def display_severity(raw):
    s = str(raw or "").upper()
    return SEVERITY_LABEL.get(s, s)

def resolve_severity(issue):
    impacts = issue.get("impacts", [])
    if impacts:
        best = max(
            impacts,
            key=lambda imp: SEVERITY_RANK.get(str(imp.get("severity", "")).upper(), 0)
        )
        return display_severity(best.get("severity", "UNKNOWN"))
    return display_severity(issue.get("severity", "UNKNOWN"))

def resolution_label(raw):
    return RESOLUTION_LABEL.get(str(raw).upper(), raw or "Unknown")

def parse_sonar_dt(dt_str):
    """Parse a SonarQube datetime string into a timezone-aware datetime, or None."""
    if not dt_str:
        return None
    try:
        # Handle both +0700 and +07:00 offset formats
        cleaned = dt_str.strip()
        if len(cleaned) > 5 and cleaned[-3] == ":" and cleaned[-6] in "+-":
            # already +HH:MM — python strptime handles %z with colon on 3.7+
            pass
        elif len(cleaned) > 4 and cleaned[-5] in "+-" and ":" not in cleaned[-5:]:
            # +0700 -> +07:00
            cleaned = cleaned[:-2] + ":" + cleaned[-2:]
        return datetime.strptime(cleaned[:25], "%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        try:
            return datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None

def age_days_from(created_str):
    """Days from creationDate until today (used in live mode)."""
    try:
        c = datetime.strptime(created_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(tz=timezone.utc) - c).days)
    except Exception:
        return ""

def age_days_at_cutoff(created_str, cutoff):
    """Days from creationDate until the cutoff date (used in historical mode)."""
    try:
        c = datetime.strptime(created_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return max(0, (cutoff - c).days)
    except Exception:
        return ""

def days_between(created_str, resolved_str):
    try:
        c = datetime.strptime(created_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        r = datetime.strptime(resolved_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return max(0, (r - c).days)
    except Exception:
        return ""

def get_assigner_from_issue(issue_key):
    data = api_get("/api/issues/changelog", {"issue": issue_key}, silent_404=True)
    if not data or "changelog" not in data:
        return ""
    for log in data["changelog"]:
        for diff in log.get("diffs", []):
            if diff.get("key") == "assignee":
                return log.get("userName") or log.get("user", "")
    return ""

def get_hotspot_users(hotspot_key, user_map):
    data = api_get("/api/hotspots/show", {"hotspot": hotspot_key}, silent_404=True)
    if not data:
        return "", ""
    for u in data.get("users", []):
        if "login" in u and "name" in u:
            user_map[u["login"]] = u["name"]
    real_assignee = data.get("assignee", "")
    assigner = ""
    for log in data.get("changelog", []):
        for diff in log.get("diffs", []):
            if diff.get("key") == "assignee":
                assigner = log.get("userName") or log.get("user", "")
                break
        if assigner:
            break
    return assigner, real_assignee

def get_assigner_from_hotspot_detail(h_detail):
    if not h_detail:
        return ""
    for log in h_detail.get("changelog", []):
        for diff in log.get("diffs", []):
            if diff.get("key") == "assignee":
                return log.get("userName") or log.get("user", "")
    return ""

def format_comment_date(raw_date):
    try:
        dt = datetime.strptime(raw_date[:19], "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw_date[:16] if raw_date else ""

def format_comments(comments, user_map):
    if not comments:
        return ""
    lines = []
    for i, c in enumerate(comments, start=1):
        login    = c.get("login", "")
        name     = user_map.get(login, login) if login else "Unknown"
        date_str = format_comment_date(c.get("createdAt", ""))
        text     = c.get("markdown", c.get("htmlText", c.get("text", ""))).replace("\n", " ").replace("\r", "").strip()
        lines.append(f"[{i}] {name} ({date_str}): {text}")
    return " | ".join(lines)

# =========================================================
# 7. CSV Field Definitions
# =========================================================
OPEN_FIELDS = [
    "project_name", "project_key", "issue_key", "type", "severity", "rule",
    "component", "line", "message", "committer", "assigner", "assignee",
    "created_at", "age_days", "effort",
]

CLOSED_FIELDS = [
    "project_name", "project_key", "issue_key", "type", "severity", "rule",
    "component", "line", "message", "committer", "assigner", "assignee",
    "resolution", "justification/comment", "created_at", "resolution_date",
    "days_to_resolve",
]

# =========================================================
# 8. Build Open Record (shared shape)
# =========================================================
def make_open_record(pname, pkey, issue_key, mapped_type, issue, committer,
                     assigner, assignee, created_at, effort):
    if HISTORICAL_MODE:
        age = age_days_at_cutoff(created_at, CUTOFF_DT)
    else:
        age = age_days_from(created_at)
    return {
        "project_name": pname,
        "project_key":  pkey,
        "issue_key":    issue_key,
        "type":         mapped_type,
        "severity":     resolve_severity(issue) if isinstance(issue, dict) else issue,
        "rule":         clean_text(issue.get("rule", "") if isinstance(issue, dict) else ""),
        "component":    clean_text(issue.get("component", "") if isinstance(issue, dict) else ""),
        "line":         (issue.get("textRange", {}).get("startLine", "N/A")
                         if isinstance(issue, dict) and issue.get("textRange")
                         else (issue.get("line", "N/A") if isinstance(issue, dict) else "N/A")),
        "message":      clean_text(issue.get("message", "") if isinstance(issue, dict) else ""),
        "committer":    committer,
        "assigner":     assigner,
        "assignee":     assignee,
        "created_at":   created_at,
        "age_days":     age,
        "effort":       effort,
    }

# =========================================================
# 9. Fetch Currently-Open Issues (live API)
# =========================================================
def fetch_currently_open(project_map, user_map, open_records):
    """
    Fetches issues with status=OPEN and hotspots with status=TO_REVIEW.
    In historical mode, injects createdBefore to skip issues that didn't
    exist yet on the cutoff date.

    NOTE: This returns issues still open TODAY. In historical mode the
    reclassification of "was open on cutoff date but fixed after" is handled
    separately by fetch_all_resolved().
    """
    createdBefore_param = CUTOFF_DT.strftime("%Y-%m-%dT%H:%M:%S%z") if HISTORICAL_MODE else None

    # --- Regular issues ---
    print("\nFetching currently-open issues...")
    for pkey, pname in project_map.items():
        page = 1
        project_count = 0
        while True:
            payload = {
                "componentKeys":    pkey,
                "resolved":         "false",
                "statuses":         "OPEN",
                "additionalFields": "users",
                "p":  page,
                "ps": 500,
            }
            if createdBefore_param:
                payload["createdBefore"] = createdBefore_param

            data = api_get("/api/issues/search", payload)
            if not data or not data.get("issues"):
                break

            for u in data.get("users", []):
                if "login" in u and "name" in u:
                    user_map[u["login"]] = u["name"]

            for issue in data["issues"]:
                issue_key    = issue.get("key", "")
                assignee_raw = issue.get("assignee", "")
                mapped_type  = ISSUE_TYPE_MAP.get(issue.get("type", ""), issue.get("type", ""))
                raw_assigner = get_assigner_from_issue(issue_key) if assignee_raw else ""
                created_at   = issue.get("creationDate", "")

                open_records.append(make_open_record(
                    pname, pkey, issue_key, mapped_type, issue,
                    issue.get("author", ""),
                    user_map.get(raw_assigner, raw_assigner),
                    user_map.get(assignee_raw, assignee_raw),
                    created_at,
                    issue.get("effort", "0min"),
                ))
                project_count += 1

            if page * 500 >= data.get("paging", {}).get("total", 0):
                break
            page += 1

        if project_count:
            print(f"  [{pname}] {project_count} open issue(s)")

    # --- Hotspots TO_REVIEW ---
    print("\nFetching currently-open hotspots...")
    for pkey, pname in project_map.items():
        h_page = 1
        hotspot_count = 0
        while True:
            h_payload = {
                "projectKey": pkey,
                "status":     "TO_REVIEW",
                "p":          h_page,
                "ps":         500,
            }
            if createdBefore_param:
                h_payload["createdBefore"] = createdBefore_param

            h_data = api_get("/api/hotspots/search", h_payload)
            if not h_data or not h_data.get("hotspots"):
                break

            for h in h_data["hotspots"]:
                h_key = h.get("key", "")
                raw_assigner_h, real_assignee_login = get_hotspot_users(h_key, user_map)
                created_at = h.get("creationDate", "")

                # Build a pseudo-issue dict so make_open_record can read it uniformly
                pseudo = {
                    "rule":      h.get("ruleKey", ""),
                    "component": h.get("component", ""),
                    "line":      h.get("line", "N/A"),
                    "message":   h.get("message", ""),
                    "impacts":   [],  # hotspots don't have impacts; severity handled below
                }

                rec = make_open_record(
                    pname, pkey, h_key, "SECURITY_HOTSPOT", pseudo,
                    h.get("author", ""),
                    user_map.get(raw_assigner_h, raw_assigner_h),
                    user_map.get(real_assignee_login, real_assignee_login),
                    created_at,
                    "N/A",
                )
                # Override severity with hotspot's vulnerabilityProbability
                rec["severity"] = display_severity(h.get("vulnerabilityProbability", "UNKNOWN"))
                open_records.append(rec)
                hotspot_count += 1

            if h_page * 500 >= h_data.get("paging", {}).get("total", 0):
                break
            h_page += 1

        if hotspot_count:
            print(f"  [{pname}] {hotspot_count} open hotspot(s)")

# =========================================================
# 10. Fetch ALL Resolved Issues (for historical reclassification)
# =========================================================
def fetch_all_resolved(project_map, user_map, open_records, closed_records):
    """
    Fetches every resolved issue (no date range bound).
    Applies date logic:
      - created_at <= cutoff AND resolution_date > cutoff  -> was still OPEN on cutoff date
      - created_at <= cutoff AND resolution_date <= cutoff -> CLOSED within the period
      - created_at > cutoff                                -> ignore (didn't exist yet)

    In live mode (no --date), all resolved issues go straight to closed_records
    using today as the implicit cutoff.
    """

    # --- Regular resolved issues ---
    print("\nFetching all resolved issues...")
    for pkey, pname in project_map.items():
        page = 1
        project_count = 0
        while True:
            data = api_get(
                "/api/issues/search",
                {
                    "componentKeys":    pkey,
                    "resolved":         "true",
                    "resolutions":      "FIXED,FALSE-POSITIVE,WONTFIX",
                    "additionalFields": "comments",
                    "p":  page,
                    "ps": 500,
                }
            )
            if not data or not data.get("issues"):
                break

            for issue in data["issues"]:
                created_at      = issue.get("creationDate", "")
                resolution_date = issue.get("updateDate", "")

                created_dt    = parse_sonar_dt(created_at)
                resolution_dt = parse_sonar_dt(resolution_date)

                if HISTORICAL_MODE:
                    # Ignore issues that didn't exist on the cutoff date
                    if not created_dt or created_dt > CUTOFF_DT:
                        continue

                    if resolution_dt and resolution_dt > CUTOFF_DT:
                        # Was resolved AFTER the cutoff -> it was still OPEN on that date
                        issue_key    = issue.get("key", "")
                        assignee_raw = issue.get("assignee", "")
                        mapped_type  = ISSUE_TYPE_MAP.get(issue.get("type", ""), issue.get("type", ""))
                        raw_assigner = get_assigner_from_issue(issue_key) if assignee_raw else ""

                        open_records.append(make_open_record(
                            pname, pkey, issue_key, mapped_type, issue,
                            issue.get("author", ""),
                            user_map.get(raw_assigner, raw_assigner),
                            user_map.get(assignee_raw, assignee_raw),
                            created_at,
                            issue.get("effort", "0min"),
                        ))
                    else:
                        # Resolved ON or BEFORE the cutoff -> genuinely closed
                        _append_closed_issue(pname, pkey, issue, user_map, closed_records)
                else:
                    # Live mode: everything resolved goes to closed
                    _append_closed_issue(pname, pkey, issue, user_map, closed_records)

                project_count += 1

            if page * 500 >= data.get("paging", {}).get("total", 0):
                break
            page += 1

        if project_count:
            print(f"  [{pname}] {project_count} resolved issue(s) processed")

    # --- Resolved hotspots ---
    print("\nFetching all resolved hotspots...")
    for pkey, pname in project_map.items():
        h_page = 1
        hotspot_count = 0
        while True:
            h_data = api_get(
                "/api/hotspots/search",
                {"projectKey": pkey, "status": "REVIEWED", "p": h_page, "ps": 500}
            )
            if not h_data or not h_data.get("hotspots"):
                break

            for h in h_data["hotspots"]:
                created_at      = h.get("creationDate", "")
                resolution_date = h.get("updateDate", "")

                created_dt    = parse_sonar_dt(created_at)
                resolution_dt = parse_sonar_dt(resolution_date)

                if HISTORICAL_MODE:
                    if not created_dt or created_dt > CUTOFF_DT:
                        continue

                    if resolution_dt and resolution_dt > CUTOFF_DT:
                        # Was still open on the cutoff date
                        h_key = h.get("key", "")
                        raw_assigner_h, real_assignee_login = get_hotspot_users(h_key, user_map)
                        pseudo = {
                            "rule":      h.get("ruleKey", ""),
                            "component": h.get("component", ""),
                            "line":      h.get("line", "N/A"),
                            "message":   h.get("message", ""),
                            "impacts":   [],
                        }
                        rec = make_open_record(
                            pname, pkey, h_key, "SECURITY_HOTSPOT", pseudo,
                            h.get("author", ""),
                            user_map.get(raw_assigner_h, raw_assigner_h),
                            user_map.get(real_assignee_login, real_assignee_login),
                            created_at,
                            "N/A",
                        )
                        rec["severity"] = display_severity(h.get("vulnerabilityProbability", "UNKNOWN"))
                        open_records.append(rec)
                    else:
                        _append_closed_hotspot(pname, pkey, h, user_map, closed_records)
                else:
                    _append_closed_hotspot(pname, pkey, h, user_map, closed_records)

                hotspot_count += 1

            if h_page * 500 >= h_data.get("paging", {}).get("total", 0):
                break
            h_page += 1

        if hotspot_count:
            print(f"  [{pname}] {hotspot_count} resolved hotspot(s) processed")

# =========================================================
# 11. Closed Record Builders
# =========================================================
def _append_closed_issue(pname, pkey, issue, user_map, closed_records):
    issue_key    = issue.get("key", "")
    assignee_raw = issue.get("assignee", "")
    mapped_type  = ISSUE_TYPE_MAP.get(issue.get("type", ""), issue.get("type", ""))
    raw_assigner = get_assigner_from_issue(issue_key) if assignee_raw else ""
    comments     = format_comments(issue.get("comments", []), user_map)

    closed_records.append({
        "project_name":          pname,
        "project_key":           pkey,
        "issue_key":             issue_key,
        "type":                  mapped_type,
        "severity":              resolve_severity(issue),
        "rule":                  clean_text(issue.get("rule", "")),
        "component":             clean_text(issue.get("component", "")),
        "line":                  (issue.get("textRange", {}).get("startLine", "N/A")
                                  if issue.get("textRange") else "N/A"),
        "message":               clean_text(issue.get("message", "")),
        "committer":             issue.get("author", ""),
        "assigner":              user_map.get(raw_assigner, raw_assigner),
        "assignee":              user_map.get(assignee_raw, assignee_raw),
        "resolution":            resolution_label(issue.get("resolution", "")),
        "justification/comment": clean_text(comments),
        "created_at":            issue.get("creationDate", ""),
        "resolution_date":       issue.get("updateDate", ""),
        "days_to_resolve":       days_between(issue.get("creationDate", ""), issue.get("updateDate", "")),
    })

def _append_closed_hotspot(pname, pkey, h, user_map, closed_records):
    h_key    = h.get("key", "")
    h_detail = api_get("/api/hotspots/show", {"hotspot": h_key}) or {}

    raw_resolution = h_detail.get("resolution") or h.get("resolution", "")
    raw_assigner_h = get_assigner_from_hotspot_detail(h_detail)
    raw_assignee_h = h_detail.get("assignee") or h.get("assignee", "")
    comments       = format_comments(h_detail.get("comment", []), user_map)

    closed_records.append({
        "project_name":          pname,
        "project_key":           pkey,
        "issue_key":             h_key,
        "type":                  "SECURITY_HOTSPOT",
        "severity":              display_severity(h.get("vulnerabilityProbability", "UNKNOWN")),
        "rule":                  clean_text(h.get("ruleKey", "")),
        "component":             clean_text(h.get("component", "")),
        "line":                  h.get("line", "N/A"),
        "message":               clean_text(h.get("message", "")),
        "committer":             h.get("author", ""),
        "assigner":              user_map.get(raw_assigner_h, raw_assigner_h),
        "assignee":              user_map.get(raw_assignee_h, raw_assignee_h),
        "resolution":            resolution_label(raw_resolution),
        "justification/comment": clean_text(comments),
        "created_at":            h.get("creationDate", ""),
        "resolution_date":       h.get("updateDate", ""),
        "days_to_resolve":       days_between(h.get("creationDate", ""), h.get("updateDate", "")),
    })

# =========================================================
# 12. Deduplicate (safety net — issue_key is the unique ID)
# =========================================================
def deduplicate(records):
    seen = set()
    out  = []
    for r in records:
        key = r.get("issue_key", "")
        if key and key not in seen:
            seen.add(key)
            out.append(r)
        elif not key:
            out.append(r)  # keep rows with no key (shouldn't happen, but be safe)
    return out

# =========================================================
# 13. Main
# =========================================================
def main():
    mode_str = f"HISTORICAL (cutoff: {date_label})" if HISTORICAL_MODE else f"LIVE (today: {date_label})"
    print("=" * 60)
    print(f"SonarQube Issues Report")
    print(f"Mode           : {mode_str}")
    print(f"SonarQube URL  : {SONAR_URL}")
    print(f"Open output    : {OPEN_ISSUES_CSV}")
    print(f"Closed output  : {CLOSED_ISSUES_CSV}")
    print("=" * 60)

    print("\nFetching project list...")
    project_map = fetch_all_project_keys()
    print(f"  Found {len(project_map)} project(s).")

    print("Fetching user list...")
    user_map = fetch_user_map()
    print(f"  Found {len(user_map)} user(s).")

    open_records   = []
    closed_records = []

    # Step 1: Currently-open issues (from the live API)
    fetch_currently_open(project_map, user_map, open_records)

    # Step 2: All resolved issues — reclassify if historical mode
    fetch_all_resolved(project_map, user_map, open_records, closed_records)

    # Step 3: Deduplicate (an issue shouldn't appear in both buckets)
    open_records   = deduplicate(open_records)
    closed_records = deduplicate(closed_records)

    # Step 4: Write open issues CSV
    print(f"\nWriting {len(open_records)} open record(s) to {OPEN_ISSUES_CSV}...")
    with open(OPEN_ISSUES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OPEN_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(open_records)

    # Step 5: Write closed issues CSV
    print(f"Writing {len(closed_records)} closed record(s) to {CLOSED_ISSUES_CSV}...")
    with open(CLOSED_ISSUES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CLOSED_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(closed_records)

    print("\n" + "=" * 60)
    print(f"Done.")
    print(f"  Open issues   : {len(open_records):,}")
    print(f"  Closed issues : {len(closed_records):,}")
    print("=" * 60)

if __name__ == "__main__":
    main()

