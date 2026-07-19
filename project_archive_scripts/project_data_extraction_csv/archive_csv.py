import os
import sys
import json
import csv
import urllib.request
import urllib.parse
import urllib.error
import base64
import zipfile

# =========================================================
# Environment Setup
# =========================================================

SONAR_URL = os.environ.get("SONAR_URL", "http://localhost:9000").rstrip("/")
SONAR_TOKEN = os.environ.get("SONAR_TOKEN")

if not SONAR_TOKEN:
    print("FATAL ERROR: Missing SONAR_TOKEN environment variable.")
    sys.exit(1)

CLEAN_TOKEN = SONAR_TOKEN.strip()

# =========================================================
# API Helpers
# =========================================================

def get_auth_header():
    b64_auth = base64.b64encode(f"{CLEAN_TOKEN}:".encode("ascii")).decode("ascii")
    return f"Basic {b64_auth}"

def api_get(endpoint, params=None):
    url = f"{SONAR_URL}{endpoint}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", get_auth_header())
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"  [GET] HTTP Error {e.code} at {url}: {error_body}")
        return None
    except Exception as e:
        print(f"  [GET] API Error at {url}: {e}")
        return None

def fetch_paginated(endpoint, base_params, array_key):
    """Handles paginated endpoints up to SonarQube's 10,000 record limit."""
    results = []
    page = 1
    page_size = 500
    params = base_params.copy()
    params['ps'] = page_size

    while True:
        params['p'] = page
        data = api_get(endpoint, params)
        
        if not data or array_key not in data:
            break

        items = data[array_key]
        if not items:
            break

        results.extend(items)
        total = data.get("paging", {}).get("total", 0)
        
        if page * page_size >= total or page * page_size >= 10000:
            if total > 10000:
                print(f"  [Warning] Pagination limit reached. Capped at 10,000 {array_key}.")
            break
            
        page += 1

    return results

# =========================================================
# CSV Writing Helpers
# =========================================================

def write_csv(data_list, filepath, fieldnames):
    """Generic CSV writer that ignores extra nested dict fields."""
    if not data_list:
        return
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data_list)

# =========================================================
# Archival Logic
# =========================================================

def archive_project_local(project_key):
    print(f"\n--- Starting Local Archival for Project: {project_key} ---")
    
    # 1. Setup Local Directory
    local_dir = os.path.expanduser(f"~/sonarqube_project_archive/{project_key}/csv_project_data_archive")
    os.makedirs(local_dir, exist_ok=True)
    print(f"Created archive directory: {local_dir}")

    # 2. Metadata
    print("Fetching Project Metadata...")
    metadata = api_get("/api/components/show", {"component": project_key})
    if metadata:
        with open(os.path.join(local_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
            
        # Metadata CSV (Key-Value format)
        comp = metadata.get("component", {})
        with open(os.path.join(local_dir, "metadata.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Property", "Value"])
            for k, v in comp.items():
                if not isinstance(v, (dict, list)):
                    writer.writerow([k, v])
    else:
        print(f"FAILED: Project {project_key} not found or access denied.")
        return False

    # 3. Final Quality Gate Status
    print("Fetching Quality Gate Status...")
    qg_status = api_get("/api/qualitygates/project_status", {"projectKey": project_key})
    if qg_status:
        with open(os.path.join(local_dir, "quality_gate.json"), "w", encoding="utf-8") as f:
            json.dump(qg_status, f, indent=2)
            
        project_status = qg_status.get("projectStatus", {})
        with open(os.path.join(local_dir, "quality_gate.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Status", "Ignored Conditions"])
            writer.writerow([project_status.get("status", "UNKNOWN"), project_status.get("ignoredConditions", False)])

    # 4. Project Analyses (Traceability / Git Commit Hash)
    print("Fetching Project Analyses (Revisions)...")
    analyses = api_get("/api/project_analyses/search", {"project": project_key})
    if analyses:
        with open(os.path.join(local_dir, "analyses.json"), "w", encoding="utf-8") as f:
            json.dump(analyses, f, indent=2)
            
        write_csv(
            analyses.get("analyses", []), 
            os.path.join(local_dir, "analyses.csv"), 
            ["key", "date", "projectVersion", "revision"]
        )

    # 5. Component Tree (Files)
    print("Fetching File Tree...")
    files = fetch_paginated("/api/components/tree", {"component": project_key, "qualifiers": "FIL"}, "components")
    with open(os.path.join(local_dir, "files.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, indent=2)
        
    write_csv(
        files, 
        os.path.join(local_dir, "files.csv"), 
        ["id", "key", "name", "qualifier", "path", "language"]
    )
    print(f"  -> Saved {len(files)} files.")

    # 6. Historical Measures
    print("Fetching Historical Measures...")
    metrics_str = "ncloc,bugs,vulnerabilities,code_smells,sqale_index,reliability_rating,security_rating,sqale_rating,coverage,duplicated_lines_density"
    history = api_get("/api/measures/search_history", {"component": project_key, "metrics": metrics_str})
    
    if history:
        with open(os.path.join(local_dir, "measures_history.json"), "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
            
        # Pivot History into a Date-based CSV
        history_csv_data = {}
        metrics_list = []
        
        if "measures" in history:
            for measure in history["measures"]:
                m_key = measure["metric"]
                metrics_list.append(m_key)
                for entry in measure.get("history", []):
                    date = entry["date"][:10] # Keep only YYYY-MM-DD
                    val = entry.get("value", "")
                    
                    if date not in history_csv_data:
                        history_csv_data[date] = {"date": date}
                    history_csv_data[date][m_key] = val
                    
        if history_csv_data:
            fieldnames = ["date"] + metrics_list
            with open(os.path.join(local_dir, "measures_history.csv"), "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for d in sorted(history_csv_data.keys()):
                    writer.writerow(history_csv_data[d])

    # 7. Issues (with Comments)
    print("Fetching Complete Issue Ledger...")
    issues = fetch_paginated(
        "/api/issues/search", 
        {
            "componentKeys": project_key, 
            "statuses": "OPEN,CONFIRMED,REOPENED,RESOLVED,CLOSED",
            "additionalFields": "comments" # <-- Added comments
        }, 
        "issues"
    )
    
    # Pre-process comments into a flat string for the CSV
    for issue in issues:
        comments_list = issue.get("comments", [])
        if comments_list:
            formatted_comments = []
            for c in comments_list:
                user = c.get("login", "Unknown")
                text = c.get("markdown", c.get("htmlText", c.get("text", ""))).replace("\n", " ").replace("\r", "")
                formatted_comments.append(f"[{user}]: {text}")
            issue["comments_text"] = " | ".join(formatted_comments)
        else:
            issue["comments_text"] = ""

    with open(os.path.join(local_dir, "issues.json"), "w", encoding="utf-8") as f:
        json.dump(issues, f, indent=2)
        
    write_csv(
        issues, 
        os.path.join(local_dir, "issues.csv"), 
        ["key", "type", "rule", "severity", "status", "resolution", "component", "message", "creationDate", "updateDate", "comments_text"]
    )
    print(f"  -> Saved {len(issues)} issues.")

    # 8. Security Hotspots
    print("Fetching Security Hotspots...")
    hotspots = fetch_paginated("/api/hotspots/search", {"projectKey": project_key}, "hotspots")
    with open(os.path.join(local_dir, "hotspots.json"), "w", encoding="utf-8") as f:
        json.dump(hotspots, f, indent=2)
        
    write_csv(
        hotspots, 
        os.path.join(local_dir, "hotspots.csv"), 
        ["key", "ruleKey", "status", "resolution", "component", "message", "creationDate", "updateDate", "vulnerabilityProbability"]
    )
    print(f"  -> Saved {len(hotspots)} hotspots.")

    # 9. Create ZIP Archive
    print("Zipping output files...")
    zip_filename = f"{project_key}_data_archive.zip"
    zip_filepath = os.path.join(local_dir, zip_filename)
    
    with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in os.listdir(local_dir):
            if file.endswith(('.json', '.csv')):
                file_path = os.path.join(local_dir, file)
                # Add file to zip archive (storing only the filename, not full path)
                zipf.write(file_path, arcname=file)
                
    print(f"  -> Created zip file: {zip_filename}")

    print("\n" + "=" * 60)
    print("ARCHIVE COMPLETE")
    print(f"JSON, CSV, and ZIP files successfully generated in:\n{local_dir}/")
    print("=" * 60)
    
    return True

# =========================================================
# Entrypoint
# =========================================================

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Error: Missing project key.")
        print("Usage: python archive_local.py <project_key>")
        sys.exit(1)
        
    target_project = sys.argv[1].strip()
    
    if not target_project:
        print("Error: Project key cannot be empty.")
        sys.exit(1)
        
    success = archive_project_local(target_project)
    
    if success:
        print("\nYou can now safely proceed to delete the project via the UI or /api/projects/delete.")
    else:
        print("\nArchival failed. DO NOT delete the project.")
