import os
import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import base64

# =========================================================
# Environment Setup
# =========================================================

SONAR_URL = os.environ.get("SONAR_URL", "http://localhost:9000").rstrip("/")
SONAR_TOKEN = os.environ.get("SONAR_TOKEN")

if not SONAR_TOKEN:
    print("FATAL ERROR: Missing SONAR_TOKEN environment variable.")
    sys.exit(1)

# Clean the token once to prevent hidden whitespace/newline issues
CLEAN_TOKEN = SONAR_TOKEN.strip()

# =========================================================
# API Helpers
# =========================================================

def get_auth_header():
    """Returns the Basic Auth header value."""
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
    except Exception as e:
        print(f"  [GET] API Error at {url}: {e}")
        return None

def api_post(endpoint, params=None):
    url = f"{SONAR_URL}{endpoint}"
    
    # SonarQube expects POST parameters in the URL query string
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
        
    # We send an empty body (b"") because the parameters are in the URL
    req = urllib.request.Request(url, data=b"", method="POST")
    req.add_header("Authorization", get_auth_header())
    req.add_header("Accept", "application/json")
    
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"  [POST] HTTP Error {e.code} at {url}: {error_body}")
        return None
    except Exception as e:
        print(f"  [POST] API Error at {url}: {e}")
        return None

# =========================================================
# Archival Logic
# =========================================================

def archive_project(project_key):
    print(f"\n--- Starting Archival for Project: {project_key} ---")
    
    # 1. Trigger the Export
    print("Triggering project dump...")
    trigger_response = api_post("/api/project_dump/export", {"key": project_key})
    
    if not trigger_response or "taskId" not in trigger_response:
        print(f"FAILED to trigger export for {project_key}. Check the error message above.")
        return False
        
    task_id = trigger_response["taskId"]
    print(f"Export triggered successfully. Background Task ID: {task_id}")
    
    # 2. Poll the Compute Engine (CE) for Task Completion
    print("Polling task status...", end="", flush=True)
    
    while True:
        task_data = api_get("/api/ce/task", {"id": task_id})
        
        if not task_data or "task" not in task_data:
            print("\nError retrieving task status. Aborting poll.")
            return False
            
        status = task_data["task"].get("status")
        
        if status == "SUCCESS":
            print(f"\nSUCCESS: Project dump completed for {project_key}.")
            break
        elif status in ["FAILED", "CANCELED"]:
            print(f"\nFAILED: Project dump failed with status {status}.")
            return False
        else:
            # Task is PENDING or IN_PROGRESS
            print(".", end="", flush=True)
            time.sleep(5) # Wait 5 seconds before polling again
            
    # 3. Provide File Retrieval Instructions
    print("-" * 60)
    print("ARCHIVE READY FOR RETRIEVAL")
    print("The .zip file is stored locally on the SonarQube server filesystem.")
    print(f"Path: <SONARQUBE_HOME>/data/governance/project_dumps/export/{project_key}.zip")
    print("-" * 60)
    
    return True

# =========================================================
# Entrypoint
# =========================================================

if __name__ == "__main__":
    # Check if the user provided exactly one argument (the project key)
    if len(sys.argv) != 2:
        print("Error: Missing project key.")
        print("Usage: python archive.py <project_key>")
        sys.exit(1)
        
    # Read the first argument passed to the script
    target_project = sys.argv[1].strip()
    
    if not target_project:
        print("Error: Project key cannot be empty.")
        sys.exit(1)
        
    success = archive_project(target_project)
    
    if success:
        print("\nYou can now safely proceed to delete the project via the UI or /api/projects/delete.")
    else:
        print("\nArchival failed. DO NOT delete the project.")
