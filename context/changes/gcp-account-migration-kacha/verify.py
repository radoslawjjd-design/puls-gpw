"""Read-only migration probes. Never print credentials or user data."""

import argparse
import base64
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

from cloud import GCLOUD, LOCAL, REGION, SCOPE, TARGET, api, firebase_users, log
from deploy import BASE_URL


def runtime(filename: str = "probe_runtime.py") -> None:
    code = (Path(__file__).parent / filename).read_text()
    wrapper = "import base64;exec(base64.b64decode(" + repr(base64.b64encode(code.encode()).decode()) + "))"
    result = api("target", "POST", f"https://run.googleapis.com/v2/projects/{TARGET}/locations/{REGION}/jobs/puls-gpw:run", {
        "overrides": {"containerOverrides": [{"args": ["run", "--no-dev", "python", "-c", wrapper], "env": [{"name": "GEMINI_MODEL", "value": "gemini-3.1-flash-lite"}, {"name": "GEMINI_VISION_MODEL", "value": "gemini-3.5-flash"}]}], "taskCount": 1, "timeout": "180s"}})
    (LOCAL / "probe-operation.json").write_text(json.dumps({"name": result["name"]}))
    log("runtime_probe_started", operation=result["name"])


def runtime_result() -> None:
    name = json.loads((LOCAL / "probe-operation.json").read_text())["name"]
    operation = api("target", "GET", "https://run.googleapis.com/v2/" + name)
    execution = operation.get("response", operation.get("metadata", {})).get("name", "")
    log("runtime_probe_status", done=operation.get("done", False), execution=execution, failed="error" in operation)
    if "/executions/" in execution:
        status = api("target", "GET", "https://run.googleapis.com/v2/" + execution)
        log("probe_execution", succeeded=status.get("succeededCount"), failed=status.get("failedCount"), conditions=status.get("conditions"))
        result = api("target", "POST", "https://logging.googleapis.com/v2/entries:list", {
            "resourceNames": [f"projects/{TARGET}"], "filter": 'resource.type="cloud_run_job" AND resource.labels.job_name="puls-gpw"', "orderBy": "timestamp desc", "pageSize": 100})
        reports = []
        for entry in result.get("entries", []):
            label_values = set(entry.get("labels", {}).values())
            if execution.split("/")[-1] not in label_values:
                continue
            payload = entry.get("jsonPayload", {})
            if not payload and entry.get("textPayload", "").startswith("{"):
                try:
                    payload = json.loads(entry["textPayload"])
                except ValueError:
                    pass
            if "migration_probe" in payload:
                reports.append(payload)
                log("probe_result", **payload)
            elif entry.get("severity") in {"ERROR", "CRITICAL"}:
                log("runtime_error_present", severity=entry["severity"])
        log("probe_logs_read", entries=len(result.get("entries", [])), reports=len(reports))
        (LOCAL / "probe-results.json").write_text(json.dumps(reports))


def secret(name: str) -> bytes:
    result = api("target", "GET", f"https://secretmanager.googleapis.com/v1/projects/{TARGET}/secrets/{name}/versions/latest:access")
    return base64.b64decode(result["payload"]["data"])


def check_api() -> None:
    result = subprocess.run([str(GCLOUD), "auth", "print-identity-token", f"--account={SCOPE['target_account']}"], capture_output=True, text=True, check=True)
    headers = {"X-Serverless-Authorization": "Bearer " + result.stdout.strip(), "X-API-Key": secret("admin-api-key").decode()}
    for path in ("/health", "/", "/auth/role", "/announcements?page_size=1", "/autocomplete/tickers?q=PKN"):
        request = urllib.request.Request(BASE_URL + path, headers=headers)
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = response.read()
            if path == "/health" and json.loads(payload).get("status") != "ok":
                raise RuntimeError("Health response invalid")
            if path == "/auth/role" and json.loads(payload).get("role") != "admin":
                raise RuntimeError("App admin authorization failed")
            log("api_route_verified", path=path, status=response.status, bytes=len(payload))
    import firebase_admin
    from firebase_admin import auth, credentials
    credential = json.loads(secret("firebase-service-account"))
    if credential.get("project_id") != TARGET:
        raise RuntimeError("Firebase credential project mismatch")
    app = firebase_admin.initialize_app(credentials.Certificate(credential), name="migration-verification")
    users = firebase_users("target")
    for user in users:
        if auth.get_user(user["localId"], app=app).uid != user["localId"]:
            raise RuntimeError("Target Firebase SDK UID mismatch")
    log("firebase_runtime_credential_verified", users=len(users))


def audit() -> None:
    summary = {}
    for side in ("source", "target"):
        project = SCOPE[side + "_project"]
        schedulers = api(side, "GET", f"https://cloudscheduler.googleapis.com/v1/projects/{project}/locations/{REGION}/jobs").get("jobs", [])
        states = {item["name"].split("/")[-1]: item["state"] for item in schedulers}
        service = api(side, "GET", f"https://run.googleapis.com/v2/projects/{project}/locations/{REGION}/services/puls-gpw-api")
        summary[side] = {"schedulers": states, "ingress": service.get("ingress"), "uri": service.get("uri")}
        log("environment_audit", side=side, **summary[side])
    billing_table = "gcp_billing_export_v1_" + SCOPE["target_billing"].replace("-", "_")
    tables = api("target", "GET", f"https://bigquery.googleapis.com/bigquery/v2/projects/{TARGET}/datasets/espi_ebi/tables").get("tables", [])
    present = any(t["tableReference"]["tableId"] == billing_table for t in tables)
    log("billing_export_table", present=present)
    summary["billing_export_table_present"] = present
    if present:
        result = api("target", "POST", f"https://bigquery.googleapis.com/bigquery/v2/projects/{TARGET}/queries", {
            "query": f"SELECT COUNT(*) AS n, MIN(DATE(usage_start_time, 'Europe/Warsaw')) AS first_day, MAX(DATE(usage_start_time, 'Europe/Warsaw')) AS last_day FROM `{TARGET}.espi_ebi.{billing_table}`",
            "useLegacySql": False, "location": REGION, "timeoutMs": 20000, "maximumBytesBilled": "1073741824"})
        if result.get("jobComplete"):
            values = [field.get("v") for field in result["rows"][0]["f"]]
            summary["billing_export_data"] = dict(zip(("rows", "first_day", "last_day"), values))
            log("billing_export_data", **summary["billing_export_data"])
    (LOCAL / "final-audit.json").write_text(json.dumps(summary, indent=2))


def public() -> None:
    for path in ("/health", "/", "/api/public/top-announcements"):
        with urllib.request.urlopen(BASE_URL + path, timeout=90) as response:
            response.read()
            log("public_route_verified", path=path, status=response.status)
    old_url = "https://puls-gpw-api-5zlombicra-lm.a.run.app/health"
    try:
        with urllib.request.urlopen(old_url, timeout=30):
            raise RuntimeError("Old API still publicly reachable")
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 404}:
            raise
        log("source_public_access_disabled", status=exc.code)


if __name__ == "__main__":
    actions = {"runtime": runtime, "quotes": lambda: runtime("probe_quotes.py"), "runtime-result": runtime_result, "api": check_api, "audit": audit, "public": public}
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=actions)
    actions[parser.parse_args().action]()
