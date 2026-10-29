"""Read-only GCP inventory. Never exports Secret Manager values or auth records."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

GCLOUD = Path(os.environ["LOCALAPPDATA"]) / "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
SOURCE_PROJECT = "puls-gpw"
SOURCE_ACCOUNT = json.loads((Path(__file__).resolve().parents[3] / ".cache/gcp-account-migration-kacha/scope.json").read_text(encoding="utf-8-sig"))["source_account"]
REGION = "europe-central2"
SAFE_ENV = {
    "GOOGLE_CLOUD_PROJECT", "GCP_PROJECT_ID", "BIGQUERY_DATASET", "APP_BASE_URL",
    "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_REGION", "GOOGLE_GENAI_USE_VERTEXAI",
    "SCRAPE_WINDOW_MINUTES", "MAX_PAGES_BANKIER", "REQUEST_DELAY", "HTTP_TIMEOUT",
    "HTTP_MAX_RETRIES", "X_AUTO_PUBLISH", "COST_ANOMALY_FACTOR", "PORT",
    "GEMINI_MODEL", "ANALYZER_MODEL", "SUPERVISOR_MODEL", "LOG_LEVEL",
}
SENSITIVE_KEYS = {"private_key", "client_secret", "clientSecret", "signerKey", "hashConfig", "passwordHash", "salt", "access_token", "refresh_token"}


def sanitize(value: object) -> object:
    """Retain secret references, but never unknown literal environment values."""
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in SENSITIVE_KEYS:
            result[key] = "<redacted>"
        elif key == "env" and isinstance(item, list):
            result[key] = [
                {**env, "value": "<redacted>"}
                if "value" in env and env.get("name") not in SAFE_ENV else sanitize(env)
                for env in item
            ]
        else:
            result[key] = sanitize(item)
    return result


def gcloud(*args: str, json_output: bool = True) -> object:
    command = [str(GCLOUD), *args, f"--project={SOURCE_PROJECT}", f"--account={SOURCE_ACCOUNT}", "--quiet"]
    if json_output:
        command.append("--format=json")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    return json.loads(result.stdout) if json_output else result.stdout.strip()


def get_json(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "x-goog-user-project": SOURCE_PROJECT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GET {url}: HTTP {exc.code}: {exc.read().decode()[:600]}") from None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3] / ".cache" / "gcp-account-migration-kacha"
    destination = args.out_dir.resolve()
    if not destination.is_relative_to(root.resolve()):
        parser.error(f"Inventory output must be under {root}")
    destination.mkdir(parents=True, exist_ok=True)
    commands = {
        "jobs": ("run", "jobs", "list", f"--region={REGION}"),
        "services": ("run", "services", "list", f"--region={REGION}"),
        "schedulers": ("scheduler", "jobs", "list", f"--location={REGION}"),
        "secrets_metadata": ("secrets", "list"),
        "service_accounts": ("iam", "service-accounts", "list"),
        "iam": ("projects", "get-iam-policy", SOURCE_PROJECT),
        "enabled_apis": ("services", "list", "--enabled"),
        "repositories": ("artifacts", "repositories", "list", "--location=all"),
        "logging_sinks": ("logging", "sinks", "list"),
        "buckets": ("storage", "buckets", "list"),
    }
    inventory = {"captured_at": datetime.now(timezone.utc).isoformat(), "project": SOURCE_PROJECT, "errors": {}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(gcloud, *command): name for name, command in commands.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                inventory[name] = sanitize(future.result())
                print(json.dumps({"section": name, "count": len(inventory[name])}), flush=True)
            except Exception as exc:
                inventory["errors"][name] = str(exc)
                print(json.dumps({"section": name, "error": str(exc)}), flush=True)
    token = gcloud("auth", "print-access-token", json_output=False)
    endpoints = {
        "firebase_config": f"https://identitytoolkit.googleapis.com/admin/v2/projects/{SOURCE_PROJECT}/config",
        "firebase_project": f"https://firebase.googleapis.com/v1beta1/projects/{SOURCE_PROJECT}",
        "alert_policies": f"https://monitoring.googleapis.com/v3/projects/{SOURCE_PROJECT}/alertPolicies",
        "notification_channels": f"https://monitoring.googleapis.com/v3/projects/{SOURCE_PROJECT}/notificationChannels",
        "datasets": f"https://bigquery.googleapis.com/bigquery/v2/projects/{SOURCE_PROJECT}/datasets?all=true&maxResults=1000",
    }
    for name, url in endpoints.items():
        try:
            inventory[name] = sanitize(get_json(url, token))
            print(json.dumps({"section": name, "ok": True}), flush=True)
        except Exception as exc:
            inventory["errors"][name] = str(exc)
            print(json.dumps({"section": name, "error": str(exc)}), flush=True)
    inventory["bq_metadata"] = {}
    output = destination / "inventory.json"
    output.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")
    for dataset in inventory.get("datasets", {}).get("datasets", []):
        dataset_id = dataset["datasetReference"]["datasetId"]
        # Anonymous query-result datasets are temporary and owned by their query
        # principal; they are not application data and must not be migrated.
        if dataset_id.startswith("_"):
            continue
        base_url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{SOURCE_PROJECT}/datasets/{dataset_id}"
        metadata = get_json(base_url, token)
        tables = []
        page_token = None
        while True:
            url = base_url + "/tables?maxResults=1000" + (f"&pageToken={page_token}" if page_token else "")
            page = get_json(url, token)
            tables.extend(page.get("tables", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        details = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(get_json, base_url + "/tables/" + table["tableReference"]["tableId"], token) for table in tables]
            for future in as_completed(futures):
                details.append(sanitize(future.result()))
        inventory["bq_metadata"][dataset_id] = {"dataset": sanitize(metadata), "tables": details}
        output.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"section": "bq", "dataset": dataset_id, "location": metadata.get("location"), "tables": len(details), "rows": sum(int(table.get("numRows", 0)) for table in details)}), flush=True)
    output.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"saved": str(output), "errors": list(inventory["errors"])}))
    if inventory["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
