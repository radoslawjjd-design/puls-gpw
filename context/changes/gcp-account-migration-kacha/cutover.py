"""Ordered migration cutover with durable guards and no destructive operations."""

import argparse
import copy
from datetime import datetime, timezone
import json
import subprocess
import time

from cloud import GCLOUD, LOCAL, REGION, SCOPE, SOURCE, TARGET, api, copy_firebase, exists, log
from deploy import BASE_URL, gcloud

INVENTORY = json.loads((LOCAL / "source-20261029/inventory.json").read_text())
RECORD = LOCAL / "cutover.json"


def state() -> dict:
    return json.loads(RECORD.read_text()) if RECORD.exists() else {}


def save(**fields: object) -> None:
    record = state()
    record.update(fields)
    RECORD.write_text(json.dumps(record, indent=2), encoding="utf-8")


def scheduler_url(side: str, name: str) -> str:
    return f"https://cloudscheduler.googleapis.com/v1/projects/{SCOPE[side + '_project']}/locations/{REGION}/jobs/{name}"


def prepare() -> None:
    # A far-future initial schedule prevents a create/pause race; actual cron is
    # installed only after the job is verifiably paused.
    for source in INVENTORY["schedulers"]:
        name = source["name"].split("/")[-1]
        url = scheduler_url("target", name)
        target = exists("target", url)
        if target is None:
            body = {key: copy.deepcopy(source[key]) for key in ("schedule", "timeZone", "httpTarget", "retryConfig", "attemptDeadline") if key in source}
            body["name"] = url.split("/v1/")[1]
            body["httpTarget"]["uri"] = body["httpTarget"]["uri"].replace(f"/namespaces/{SOURCE}/", f"/namespaces/{TARGET}/")
            body["httpTarget"]["oauthToken"]["serviceAccountEmail"] = f"puls-gpw-scheduler@{TARGET}.iam.gserviceaccount.com"
            body["schedule"] = "0 0 1 1 *"
            api("target", "POST", url.rsplit("/", 1)[0], body)
            target = api("target", "GET", url)
        if target["state"] != "PAUSED":
            api("target", "POST", url + ":pause", {})
        api("target", "PATCH", url + "?updateMask=schedule", {"schedule": source["schedule"]})
        if api("target", "GET", url)["state"] != "PAUSED":
            raise RuntimeError("Target scheduler was not paused")
        log("target_scheduler_paused", name=name)
    config_url = f"https://identitytoolkit.googleapis.com/admin/v2/projects/{TARGET}/config"
    current = api("target", "GET", config_url)
    service = api("target", "GET", f"https://run.googleapis.com/v2/projects/{TARGET}/locations/{REGION}/services/puls-gpw-api")
    domains = set(current.get("authorizedDomains", [])) | {BASE_URL.split("//")[1], service["uri"].split("//")[1]}
    api("target", "PATCH", config_url + "?updateMask=authorizedDomains", {"authorizedDomains": sorted(domains)})
    # Preserve runtime settings through subsequent CI deployments.
    model_env = "GEMINI_MODEL=gemini-3.1-flash-lite,GEMINI_VISION_MODEL=gemini-3.5-flash"
    for job in INVENTORY["jobs"]:
        gcloud("run", "jobs", "update", job["metadata"]["name"], "--region=" + REGION, "--update-env-vars=" + model_env)
    gcloud("run", "services", "update", "puls-gpw-api", "--region=" + REGION, "--update-env-vars=" + model_env)
    save(prepared=True)


def monitoring() -> None:
    channel_base = f"https://monitoring.googleapis.com/v3/projects/{TARGET}/notificationChannels"
    existing_channels = api("target", "GET", channel_base).get("notificationChannels", [])
    mapping = {}
    for source in INVENTORY["notification_channels"].get("notificationChannels", []):
        target = next((item for item in existing_channels if item.get("type") == source["type"] and item.get("labels") == source.get("labels")), None)
        if target is None:
            target = api("target", "POST", channel_base, {key: source[key] for key in ("type", "displayName", "description", "labels", "enabled") if key in source})
        mapping[source["name"]] = target["name"]
    policy_base = f"https://monitoring.googleapis.com/v3/projects/{TARGET}/alertPolicies"
    existing_policies = api("target", "GET", policy_base).get("alertPolicies", [])
    policy_names = []
    for source in INVENTORY["alert_policies"].get("alertPolicies", []):
        target = next((item for item in existing_policies if item["displayName"] == source["displayName"]), None)
        if target is None:
            body = {key: copy.deepcopy(source[key]) for key in ("displayName", "documentation", "combiner", "conditions", "alertStrategy", "userLabels") if key in source}
            for condition in body["conditions"]:
                condition.pop("name", None)
            body["notificationChannels"] = [mapping[name] for name in source.get("notificationChannels", [])]
            body["enabled"] = False
            target = api("target", "POST", policy_base, body)
        policy_names.append(target["name"])
    save(monitoring_policies=policy_names)
    log("monitoring_staged_disabled", policies=len(policy_names), channels=len(mapping))


def freeze() -> None:
    if not state().get("prepared"):
        raise RuntimeError("Destination not prepared")
    reports = json.loads((LOCAL / "probe-results.json").read_text())
    if not all(any(r.get("model") == model and r.get("ok") for r in reports) for model in ("gemini-3.1-flash-lite", "gemini-3.5-flash")):
        raise RuntimeError("Both target Vertex probes must pass before freeze")
    if state().get("public"):
        raise RuntimeError("Target already public; refuse new freeze")
    if state().get("frozen"):
        log("source_already_frozen")
        return
    if not state().get("original_schedulers"):
        states = {item["name"].split("/")[-1]: api("source", "GET", scheduler_url("source", item["name"].split("/")[-1]))["state"] for item in INVENTORY["schedulers"]}
        save(original_schedulers=states)
    for name in state()["original_schedulers"]:
        url = scheduler_url("source", name)
        if api("source", "GET", url)["state"] == "ENABLED":
            api("source", "POST", url + ":pause", {})
        log("source_scheduler_paused", name=name)
    subprocess.run([str(GCLOUD), "run", "services", "update", "puls-gpw-api", "--ingress=internal", f"--region={REGION}", f"--project={SOURCE}", f"--account={SCOPE['source_account']}", "--quiet"], check=True)
    # Drain requests already accepted by the old API (its configured timeout is 60s).
    deadline = time.monotonic() + 600
    not_before = time.monotonic() + 65
    while True:
        active = []
        for job in INVENTORY["jobs"]:
            name = job["metadata"]["name"]
            response = api("source", "GET", f"https://run.googleapis.com/v2/projects/{SOURCE}/locations/{REGION}/jobs/{name}/executions?pageSize=100")
            active.extend(item["name"] for item in response.get("executions", []) if not item.get("completionTime"))
        if not active and time.monotonic() >= not_before:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("Source still draining; do not start final copy")
        log("source_draining", executions=len(active))
        time.sleep(5)
    stamp = int(time.time() * 1000)
    save(frozen=True, frozen_ms=stamp, frozen_at=datetime.now(timezone.utc).isoformat())
    log("source_frozen", snapshot_ms=stamp)


def bq_job(job_id: str, configuration: dict) -> dict:
    base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{TARGET}/jobs"
    url = base + "/" + job_id + "?location=" + REGION
    job = exists("source", url)
    if job is None:
        job = api("source", "POST", base, {"jobReference": {"projectId": TARGET, "jobId": job_id, "location": REGION}, "configuration": configuration})
    deadline = time.monotonic() + 600
    while job.get("status", {}).get("state") != "DONE":
        if time.monotonic() > deadline:
            raise RuntimeError("BQ job still running: " + job_id)
        time.sleep(2)
        job = api("source", "GET", url)
    if "errorResult" in job["status"]:
        raise RuntimeError("BQ job failed: " + job_id + " " + job["status"]["errorResult"].get("message", "")[:600])
    return job


def copy_final() -> None:
    record = state()
    if not record.get("frozen") or record.get("public"):
        raise RuntimeError("Final sync requires source frozen and destination private")
    stamp = record["frozen_ms"]
    for dataset, details in INVENTORY["bq_metadata"].items():
        for snapshot in details["tables"]:
            table = snapshot["tableReference"]["tableId"]
            source_url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{SOURCE}/datasets/{dataset}/tables/{table}"
            metadata = api("source", "GET", source_url)
            destination = {"projectId": TARGET, "datasetId": dataset, "tableId": table}
            target_base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{TARGET}/datasets/{dataset}/tables"
            target_url = target_base + "/" + table
            ingestion_partition = bool(metadata.get("timePartitioning")) and not metadata["timePartitioning"].get("field")
            if ingestion_partition:
                if metadata.get("streamingBuffer"):
                    raise RuntimeError("Historical ingestion-partition table still has a streaming buffer")
                source_ref = {**metadata["tableReference"], "tableId": table + "@" + str(stamp)}
                configuration = {"copy": {"sourceTable": source_ref, "destinationTable": destination, "writeDisposition": "WRITE_EMPTY"}}
                source_query = f"`{SOURCE}.{dataset}.{table}` FOR SYSTEM_TIME AS OF TIMESTAMP_MILLIS({stamp})"
            else:
                if not exists("target", target_url):
                    body = {key: metadata[key] for key in ("schema", "timePartitioning", "rangePartitioning", "clustering", "description", "labels", "requirePartitionFilter") if key in metadata}
                    body["tableReference"] = destination
                    api("target", "POST", target_base, body)
                source_query = f"`{SOURCE}.{dataset}.{table}`"
                configuration = {"query": {"query": f"SELECT * FROM {source_query}", "useLegacySql": False, "destinationTable": destination, "writeDisposition": "WRITE_EMPTY", "maximumBytesBilled": "2147483648"}}
            bq_job("migration_final_20261029_" + table, configuration)
            target_metadata = api("target", "GET", target_url)
            for key in ("schema", "timePartitioning", "rangePartitioning", "clustering"):
                if target_metadata.get(key) != metadata.get(key):
                    raise RuntimeError("Final schema/layout differs: " + table + " " + key)
            # Compare complete row multisets, including duplicates, not only totals.
            query = f"""WITH s AS (SELECT TO_JSON_STRING(t) AS row_json, COUNT(*) AS n FROM (SELECT * FROM {source_query}) AS t GROUP BY 1),
                d AS (SELECT TO_JSON_STRING(t) AS row_json, COUNT(*) AS n FROM `{TARGET}.{dataset}.{table}` AS t GROUP BY 1)
                SELECT COUNT(*) AS differences FROM (
                    (SELECT * FROM s EXCEPT DISTINCT SELECT * FROM d)
                    UNION ALL (SELECT * FROM d EXCEPT DISTINCT SELECT * FROM s))"""
            job_id = "migration_compare_20261029_" + table
            if ingestion_partition:
                job_id += "_v2"
            bq_job(job_id, {"query": {"query": query, "useLegacySql": False, "maximumBytesBilled": "2147483648"}})
            result = api("source", "GET", f"https://bigquery.googleapis.com/bigquery/v2/projects/{TARGET}/queries/{job_id}?location={REGION}")
            if int(result["rows"][0]["f"][0]["v"]) != 0:
                raise RuntimeError("Row multisets differ: " + table)
            log("final_table_verified", table=table, rows=target_metadata.get("numRows"))
    copy_firebase()
    save(final_verified=True)
    log("final_data_and_identity_verified")


def promote() -> None:
    if not state().get("final_verified") or state().get("public"):
        raise RuntimeError("Promote requires verified final data and private API")
    for job in INVENTORY["jobs"]:
        name = job["metadata"]["name"]
        env = "BIGQUERY_DATASET=espi_ebi"
        if name == "puls-gpw-post":
            env += ",X_AUTO_PUBLISH=true"
        gcloud("run", "jobs", "update", name, "--region=" + REGION, "--update-env-vars=" + env)
    gcloud("run", "services", "update", "puls-gpw-api", "--region=" + REGION, "--update-env-vars=BIGQUERY_DATASET=espi_ebi")
    save(promoted=True)
    log("production_dataset_selected_api_still_private")


def publish() -> None:
    if not state().get("promoted"):
        raise RuntimeError("Production data not selected")
    # Source must still be stopped immediately before opening target writes.
    for name in state()["original_schedulers"]:
        if api("source", "GET", scheduler_url("source", name))["state"] != "PAUSED":
            raise RuntimeError("Source scheduler unexpectedly active")
    source_service = api("source", "GET", f"https://run.googleapis.com/v2/projects/{SOURCE}/locations/{REGION}/services/puls-gpw-api")
    if source_service.get("ingress") != "INGRESS_TRAFFIC_INTERNAL_ONLY":
        raise RuntimeError("Source API unexpectedly accepts external writes")
    service = f"https://run.googleapis.com/v2/projects/{TARGET}/locations/{REGION}/services/puls-gpw-api"
    policy = api("target", "GET", service + ":getIamPolicy")
    if not any(b["role"] == "roles/run.invoker" and "allUsers" in b["members"] for b in policy.get("bindings", [])):
        policy.setdefault("bindings", []).append({"role": "roles/run.invoker", "members": ["allUsers"]})
        api("target", "POST", service + ":setIamPolicy", {"policy": policy})
    save(public=True, published_at=datetime.now(timezone.utc).isoformat())
    for policy in INVENTORY["alert_policies"].get("alertPolicies", []):
        if policy.get("enabled"):
            api("source", "PATCH", "https://monitoring.googleapis.com/v3/" + policy["name"] + "?updateMask=enabled", {"name": policy["name"], "enabled": False})
    # Keep broken scraping and cost reporting without initial billing data paused.
    enabled, held = [], []
    for name, original in state()["original_schedulers"].items():
        if original != "ENABLED" or name in {"puls-gpw-trigger", "puls-gpw-cost-report-trigger"}:
            held.append(name)
            continue
        url = scheduler_url("target", name)
        if api("target", "GET", url)["state"] == "PAUSED":
            api("target", "POST", url + ":resume", {})
        enabled.append(name)
    save(enabled_schedulers=enabled, held_schedulers=held)
    log("cutover_published", url=BASE_URL, enabled_schedulers=enabled, paused_schedulers=held)


def finish_access() -> None:
    if not state().get("public") or not state().get("final_verified"):
        raise RuntimeError("Temporary access is needed until cutover verification completes")
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{TARGET}"
    policy = api("target", "POST", url + ":getIamPolicy", {})
    member = "user:" + SCOPE["source_account"]
    cleaned = []
    for binding in policy.get("bindings", []):
        if binding["role"] in {"roles/bigquery.dataEditor", "roles/bigquery.jobUser"} and "condition" not in binding:
            binding["members"] = [value for value in binding["members"] if value != member]
        if binding["members"]:
            cleaned.append(binding)
    policy["bindings"] = cleaned
    api("target", "POST", url + ":setIamPolicy", {"policy": policy})
    save(temporary_source_access_removed=True)
    log("temporary_migration_access_removed")


if __name__ == "__main__":
    actions = {"prepare": prepare, "monitoring": monitoring, "freeze": freeze, "copy-final": copy_final, "promote": promote, "publish": publish, "finish-access": finish_access}
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=actions)
    actions[parser.parse_args().action]()
