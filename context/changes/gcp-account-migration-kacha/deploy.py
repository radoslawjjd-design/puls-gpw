"""Build reviewed application files and stage private destination resources."""

import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess

from cloud import GCLOUD, LOCAL, REGION, ROOT, SCOPE, SOURCE, TARGET, api, exists, log

IMAGE = f"{REGION}-docker.pkg.dev/{TARGET}/puls-gpw/puls-gpw:migration-20261029"
BASE_URL = f"https://puls-gpw-api-{SCOPE['target_number']}.{REGION}.run.app"


def gcloud(*args: str) -> None:
    subprocess.run([str(GCLOUD), *args, f"--project={TARGET}",
                    f"--account={SCOPE['target_account']}", "--quiet"], check=True)


def build() -> None:
    # Only tracked application paths; never submit the mixed working directory.
    staging = LOCAL / "build-source"
    if staging.exists():
        raise RuntimeError("Build staging exists; inspect/reuse it explicitly")
    staging.mkdir(parents=True)
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    roots = {"src", "db", "static", "data"}
    files = {"Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", "main.py",
             "api_main.py", "post_main.py", "company_stats_main.py", "etf_quotes_main.py", "cost_report_main.py"}
    count = 0
    for name in tracked:
        path = Path(name)
        if not name or (path.parts[0] not in roots and name not in files):
            continue
        if path.suffix in {".pyc", ".pyo"} or path.name.startswith(".env") or path.is_absolute() or ".." in path.parts:
            raise RuntimeError("Unexpected build path")
        destination = staging / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
        count += 1
    config = {"steps": [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "-t", IMAGE, "."]}],
              "images": [IMAGE], "serviceAccount": f"projects/{TARGET}/serviceAccounts/puls-gpw-build@{TARGET}.iam.gserviceaccount.com",
              "options": {"logging": "CLOUD_LOGGING_ONLY"}, "timeout": "1200s"}
    config_path = LOCAL / "cloudbuild.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    log("build_source_prepared", files=count)
    gcloud("builds", "submit", str(staging), "--config=" + str(config_path), "--region=" + REGION, "--async")


def manifest(item: dict) -> dict:
    is_job = item["kind"] == "Job"
    result = {"apiVersion": item["apiVersion"], "kind": item["kind"],
              "metadata": {"name": item["metadata"]["name"], "namespace": str(SCOPE["target_number"])},
              "spec": copy.deepcopy(item["spec"])}
    template = result["spec"]["template"]
    annotations = template.get("metadata", {}).get("annotations", {})
    keep = {key: value for key, value in annotations.items() if key in {
        "autoscaling.knative.dev/maxScale", "autoscaling.knative.dev/minScale",
        "run.googleapis.com/execution-environment", "run.googleapis.com/startup-cpu-boost"}}
    template["metadata"] = {"annotations": keep}
    task = template["spec"]["template"]["spec"] if is_job else template["spec"]
    task["serviceAccountName"] = f"puls-gpw-runner@{TARGET}.iam.gserviceaccount.com"
    for container in task["containers"]:
        container["image"] = IMAGE
        env = {entry["name"]: entry for entry in container.get("env", [])}
        values = {"GOOGLE_CLOUD_PROJECT": TARGET, "BIGQUERY_DATASET": "espi_ebi_preview", "APP_BASE_URL": BASE_URL,
                  "GEMINI_MODEL": "gemini-3.1-flash-lite", "GEMINI_VISION_MODEL": "gemini-3.5-flash"}
        if "X_AUTO_PUBLISH" in env:
            values["X_AUTO_PUBLISH"] = "false"
        if item["metadata"]["name"] == "puls-gpw-cost-report":
            values["GCP_BILLING_EXPORT_TABLE"] = "gcp_billing_export_v1_" + SCOPE["target_billing"].replace("-", "_")
        env.update({key: {"name": key, "value": value} for key, value in values.items()})
        container["env"] = list(env.values())
    if not is_job:
        result["metadata"]["annotations"] = {"run.googleapis.com/ingress": "all"}
        result["spec"]["traffic"] = [{"latestRevision": True, "percent": 100}]
    encoded = json.dumps(result)
    if "<redacted>" in encoded or f"@{SOURCE}.iam" in encoded:
        raise RuntimeError("Manifest contains unresolved source configuration")
    return result


def stage() -> None:
    inventory = json.loads((LOCAL / "source-20261029/inventory.json").read_text())
    items = inventory["jobs"] + [item for item in inventory["services"] if item["metadata"]["name"] == "puls-gpw-api"]
    for item in items:
        name = item["metadata"]["name"]
        kind = "jobs" if item["kind"] == "Job" else "services"
        url = f"https://{REGION}-run.googleapis.com/apis/{item['apiVersion']}/namespaces/{TARGET}/{kind}/{name}"
        if exists("target", url):
            log("stage_resource_already_exists", name=name)
            continue
        path = LOCAL / (name + ".json")
        path.write_text(json.dumps(manifest(item)), encoding="utf-8")
        gcloud("run", kind, "replace", str(path), "--region=" + REGION)
        log("destination_staged", name=name, dataset="espi_ebi_preview")
    # New Cloud Run services are IAM-private by default; explicitly verify.
    policy = api("target", "GET", f"https://run.googleapis.com/v2/projects/{TARGET}/locations/{REGION}/services/puls-gpw-api:getIamPolicy")
    if any("allUsers" in entry.get("members", []) or "allAuthenticatedUsers" in entry.get("members", []) for entry in policy.get("bindings", [])):
        raise RuntimeError("Staging API unexpectedly public")
    log("staging_api_private_verified", url=BASE_URL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "stage"])
    args = parser.parse_args()
    {"build": build, "stage": stage}[args.action]()
