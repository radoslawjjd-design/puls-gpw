"""Scoped migration operations. Credentials and secret payloads stay in memory."""

import argparse
import base64
import json
import os
import re
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
LOCAL = ROOT / ".cache/gcp-account-migration-kacha"
GCLOUD = Path(os.environ["LOCALAPPDATA"]) / "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
SCOPE = json.loads((LOCAL / "scope.json").read_text(encoding="utf-8-sig"))
SOURCE = SCOPE["source_project"]
TARGET = SCOPE["target_project"]
REGION = SCOPE["region"]
if SOURCE != "puls-gpw" or TARGET != "puls-gpw-kp-20261029" or SOURCE == TARGET:
    raise RuntimeError("Migration scope does not match the reviewed source/destination")
_TOKENS: dict[str, tuple[float, str]] = {}


class ApiError(RuntimeError):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(message)


def log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=True), flush=True)


def token(side: str) -> str:
    cached = _TOKENS.get(side)
    if cached and time.monotonic() - cached[0] < 2400:
        return cached[1]
    result = subprocess.run([str(GCLOUD), "auth", "print-access-token", f"--account={SCOPE[side + '_account']}", "--quiet"], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise RuntimeError(f"Cannot obtain {side} credentials")
    value = result.stdout.strip()
    _TOKENS[side] = (time.monotonic(), value)
    return value


def api(side: str, method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token(side)}",
        "Content-Type": "application/json",
        "x-goog-user-project": SCOPE[side + "_project"],
    })
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        # Do not print API payloads: some operations handle passwords/keys.
        payload = json.loads(exc.read() or b"{}")
        reason = payload.get("error", {}).get("status", "request failed")
        message = payload.get("error", {}).get("message", "")
        if re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", message):
            reason = message
        if "accounts" in url:
            codes = re.findall(r"(?:^|\n)([A-Z][A-Z0-9_]{2,80})(?:\s*:|$)", message)
            unknown = re.findall(r'Unknown name "([A-Za-z0-9_]+)"', message)
            fields = re.findall(r"Invalid value at '([a-zA-Z0-9_.\[\]]+)'", message)
            reason += f" codes={codes} unknown_fields={unknown} invalid_fields={fields}"
        if "accounts" not in url and "secrets" not in url:
            reason += ": " + payload.get("error", {}).get("message", "")[:300]
        raise ApiError(exc.code, f"{method} {url.split('?')[0]}: HTTP {exc.code} {reason}") from None


def exists(side: str, url: str) -> dict | None:
    try:
        return api(side, "GET", url)
    except ApiError as exc:
        if exc.code == 404:
            return None
        raise


def wait_operation(side: str, endpoint: str, operation: dict, timeout: int = 180) -> dict:
    deadline = time.monotonic() + timeout
    while not operation.get("done"):
        if time.monotonic() > deadline:
            raise RuntimeError(f"Operation still running: {operation['name']}")
        time.sleep(3)
        operation = api(side, "GET", endpoint + operation["name"])
    if "error" in operation:
        raise RuntimeError(f"Operation failed: {operation['error'].get('code')}")
    return operation.get("response", {})


def add_bindings(bindings: dict[str, set[str]]) -> None:
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{TARGET}"
    policy = api("target", "POST", url + ":getIamPolicy", {})
    for role, members in bindings.items():
        entry = next((item for item in policy.get("bindings", []) if item["role"] == role and "condition" not in item), None)
        if entry is None:
            entry = {"role": role, "members": []}
            policy.setdefault("bindings", []).append(entry)
        entry["members"] = sorted(set(entry["members"]) | members)
    api("target", "POST", url + ":setIamPolicy", {"policy": policy})
    log("target_iam_updated", roles=sorted(bindings))


def bootstrap() -> None:
    inventory = json.loads((LOCAL / "source-20261029/inventory.json").read_text(encoding="utf-8"))
    names = ["puls-gpw-runner", "puls-gpw-scheduler", "puls-gpw-ci", "puls-gpw-firebase", "puls-gpw-build"]
    for name in names:
        email = f"{name}@{TARGET}.iam.gserviceaccount.com"
        if not exists("target", f"https://iam.googleapis.com/v1/projects/{TARGET}/serviceAccounts/{email}"):
            api("target", "POST", f"https://iam.googleapis.com/v1/projects/{TARGET}/serviceAccounts", {"accountId": name, "serviceAccount": {"displayName": name}})
        log("service_account_ready", name=name)
    bindings: dict[str, set[str]] = {}
    for entry in inventory["iam"]["bindings"]:
        for member in entry["members"]:
            if any(member == f"serviceAccount:{name}@{SOURCE}.iam.gserviceaccount.com" for name in names[:3]):
                bindings.setdefault(entry["role"], set()).add(member.replace(f"@{SOURCE}.", f"@{TARGET}."))
    for role, member in [
        ("roles/firebaseauth.admin", f"serviceAccount:puls-gpw-firebase@{TARGET}.iam.gserviceaccount.com"),
        ("roles/cloudbuild.builds.builder", f"serviceAccount:puls-gpw-build@{TARGET}.iam.gserviceaccount.com"),
        ("roles/artifactregistry.writer", f"serviceAccount:puls-gpw-build@{TARGET}.iam.gserviceaccount.com"),
        ("roles/bigquery.jobUser", "user:" + SCOPE["source_account"]),
        ("roles/bigquery.dataEditor", "user:" + SCOPE["source_account"]),
    ]:
        bindings.setdefault(role, set()).add(member)
    add_bindings(bindings)
    runner = f"puls-gpw-runner@{TARGET}.iam.gserviceaccount.com"
    runner_url = f"https://iam.googleapis.com/v1/projects/{TARGET}/serviceAccounts/{runner}"
    runner_policy = api("target", "POST", runner_url + ":getIamPolicy", {})
    binding = next((item for item in runner_policy.get("bindings", []) if item["role"] == "roles/iam.serviceAccountUser" and "condition" not in item), None)
    if binding is None:
        binding = {"role": "roles/iam.serviceAccountUser", "members": []}
        runner_policy.setdefault("bindings", []).append(binding)
    binding["members"] = sorted(set(binding["members"]) | {f"serviceAccount:puls-gpw-ci@{TARGET}.iam.gserviceaccount.com"})
    api("target", "POST", runner_url + ":setIamPolicy", {"policy": runner_policy})
    repo_url = f"https://artifactregistry.googleapis.com/v1/projects/{TARGET}/locations/{REGION}/repositories"
    if not exists("target", repo_url + "/puls-gpw"):
        source_repo = next(item for item in inventory["repositories"] if item["name"].endswith("/repositories/puls-gpw"))
        body = {"format": "DOCKER", "description": "puls-gpw production images"}
        for key in ("cleanupPolicies", "cleanupPolicyDryRun", "dockerConfig"):
            if key in source_repo:
                body[key] = source_repo[key]
        operation = api("target", "POST", repo_url + "?repositoryId=puls-gpw", body)
        wait_operation("target", "https://artifactregistry.googleapis.com/v1/", operation)
    log("repository_ready")
    for name, details in inventory["bq_metadata"].items():
        url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{TARGET}/datasets"
        if not exists("target", url + "/" + name):
            source = details["dataset"]
            body = {"datasetReference": {"projectId": TARGET, "datasetId": name}, "location": source["location"]}
            for key in ("description", "labels", "defaultTableExpirationMs", "defaultPartitionExpirationMs", "maxTimeTravelHours"):
                if key in source:
                    body[key] = source[key]
            api("target", "POST", url, body)
        log("dataset_ready", dataset=name)


def firebase_enable() -> None:
    url = f"https://firebase.googleapis.com/v1beta1/projects/{TARGET}"
    if not exists("target", url):
        operation = api("target", "POST", url + ":addFirebase", {})
        wait_operation("target", "https://firebase.googleapis.com/v1beta1/", operation)
    log("firebase_project_ready")


def copy_secrets() -> None:
    inventory = json.loads((LOCAL / "source-20261029/inventory.json").read_text(encoding="utf-8"))
    for item in inventory["secrets_metadata"]:
        name = item["name"].split("/")[-1]
        if name in {"firebase-service-account", "firebase-web-api-key"}:
            continue  # These must be generated for the destination, never copied.
        source = api("source", "GET", f"https://secretmanager.googleapis.com/v1/projects/{SOURCE}/secrets/{name}/versions/latest:access")
        encoded = source["payload"]["data"]
        url = f"https://secretmanager.googleapis.com/v1/projects/{TARGET}/secrets/{name}"
        if not exists("target", url):
            api("target", "POST", f"https://secretmanager.googleapis.com/v1/projects/{TARGET}/secrets?secretId={name}", {"replication": item["replication"], "labels": {"migration": "gcp-kacha-20261029"}})
        versions = api("target", "GET", url + "/versions?pageSize=100")
        if versions.get("versions"):
            current = api("target", "GET", url + "/versions/latest:access")
            if base64.b64decode(current["payload"]["data"]) != base64.b64decode(encoded):
                raise RuntimeError(f"Destination secret {name} differs; refusing to rotate it")
            log("secret_verified", name=name)
        else:
            api("target", "POST", url + ":addVersion", {"payload": {"data": encoded}})
            current = api("target", "GET", url + "/versions/latest:access")
            if base64.b64decode(current["payload"]["data"]) != base64.b64decode(encoded):
                raise RuntimeError(f"Secret verification failed: {name}")
            log("secret_copied_verified", name=name)


def copy_bq_preview() -> None:
    """Populate a separate staging dataset; never overwrite destination tables."""
    inventory = json.loads((LOCAL / "source-20261029/inventory.json").read_text(encoding="utf-8"))
    for dataset_name, details in inventory["bq_metadata"].items():
        destination_dataset = dataset_name + "_preview"
        dataset_url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{TARGET}/datasets"
        if not exists("target", dataset_url + "/" + destination_dataset):
            api("target", "POST", dataset_url, {"datasetReference": {"projectId": TARGET, "datasetId": destination_dataset}, "location": details["dataset"]["location"], "description": "Migration preview; not production"})
        for table in details["tables"]:
            table_name = table["tableReference"]["tableId"]
            if table.get("type") != "TABLE":
                raise RuntimeError(f"Manual handling required for {table_name}: {table.get('type')}")
            destination = {"projectId": TARGET, "datasetId": destination_dataset, "tableId": table_name}
            destination_url = dataset_url + "/" + destination_dataset + "/tables/" + table_name
            existing = exists("target", destination_url)
            if existing is None:
                job_id = "migration_preview_20261029_" + table_name
                job_base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{TARGET}/jobs"
                location = details["dataset"]["location"]
                job_url = job_base + "/" + job_id + "?location=" + location
                job = exists("source", job_url)
                if job is None:
                    job = api("source", "POST", job_base, {"jobReference": {"projectId": TARGET, "jobId": job_id, "location": location}, "configuration": {"copy": {"sourceTable": table["tableReference"], "destinationTable": destination, "createDisposition": "CREATE_IF_NEEDED", "writeDisposition": "WRITE_EMPTY"}}})
                deadline = time.monotonic() + 300
                while job.get("status", {}).get("state") != "DONE":
                    if time.monotonic() > deadline:
                        raise RuntimeError(f"BQ copy still running: {job_id}")
                    time.sleep(3)
                    job = api("source", "GET", job_url)
                if job.get("status", {}).get("errorResult"):
                    raise RuntimeError(f"BQ copy failed for {table_name}: {job['status']['errorResult'].get('reason')}")
                existing = api("target", "GET", destination_url)
            for key in ("schema", "timePartitioning", "rangePartitioning", "clustering"):
                if existing.get(key) != table.get(key):
                    raise RuntimeError(f"Copied {table_name} differs in {key}")
            log("bq_preview_verified", table=table_name, rows=existing.get("numRows"), snapshot_rows=table.get("numRows"))


def new_secret(name: str, value: bytes) -> None:
    url = f"https://secretmanager.googleapis.com/v1/projects/{TARGET}/secrets/{name}"
    if not exists("target", url):
        api("target", "POST", f"https://secretmanager.googleapis.com/v1/projects/{TARGET}/secrets?secretId={name}", {"replication": {"userManaged": {"replicas": [{"location": REGION}]}}, "labels": {"migration": "gcp-kacha-20261029"}})
    versions = api("target", "GET", url + "/versions?pageSize=100")
    if versions.get("versions"):
        current = api("target", "GET", url + "/versions/latest:access")
        if base64.b64decode(current["payload"]["data"]) != value:
            raise RuntimeError(f"Refusing to rotate existing destination secret {name}")
    else:
        api("target", "POST", url + ":addVersion", {"payload": {"data": base64.b64encode(value).decode()}})
    log("destination_secret_ready", name=name)


def firebase_setup() -> None:
    config_url = f"https://identitytoolkit.googleapis.com/admin/v2/projects/{TARGET}/config"
    current = api("target", "GET", config_url)
    domains = set(current.get("authorizedDomains", [])) | {"localhost", f"{TARGET}.firebaseapp.com", f"{TARGET}.web.app"}
    api("target", "PATCH", config_url + "?updateMask=signIn.email,authorizedDomains", {"signIn": {"email": {"enabled": True, "passwordRequired": True}}, "authorizedDomains": sorted(domains)})
    apps_url = f"https://firebase.googleapis.com/v1beta1/projects/{TARGET}/webApps"
    apps = api("target", "GET", apps_url).get("apps", [])
    if not apps:
        operation = api("target", "POST", apps_url, {"displayName": "Faro"})
        app = wait_operation("target", "https://firebase.googleapis.com/v1beta1/", operation)
    else:
        if len(apps) != 1:
            raise RuntimeError("Multiple target Firebase web apps; explicit selection required")
        app = apps[0]
    web_config = api("target", "GET", "https://firebase.googleapis.com/v1beta1/" + app["name"] + "/config")
    new_secret("firebase-web-api-key", web_config["apiKey"].encode())
    secret_url = f"https://secretmanager.googleapis.com/v1/projects/{TARGET}/secrets/firebase-service-account/versions/latest:access"
    current_secret = exists("target", secret_url)
    if current_secret:
        credential = json.loads(base64.b64decode(current_secret["payload"]["data"]))
        if credential.get("project_id") != TARGET:
            raise RuntimeError("Destination Firebase credential references another project")
        log("firebase_credential_verified")
    else:
        email = f"puls-gpw-firebase@{TARGET}.iam.gserviceaccount.com"
        key = api("target", "POST", f"https://iam.googleapis.com/v1/projects/{TARGET}/serviceAccounts/{email}/keys", {"privateKeyType": "TYPE_GOOGLE_CREDENTIALS_FILE", "keyAlgorithm": "KEY_ALG_RSA_2048"})
        new_secret("firebase-service-account", base64.b64decode(key["privateKeyData"]))
    log("firebase_setup_complete")


def firebase_users(side: str) -> list[dict]:
    users = []
    next_page = None
    while True:
        url = f"https://identitytoolkit.googleapis.com/v1/projects/{SCOPE[side + '_project']}/accounts:batchGet?maxResults=1000"
        if next_page:
            url += "&nextPageToken=" + urllib.parse.quote(next_page, safe="")
        response = api(side, "GET", url)
        users.extend(response.get("users", []))
        next_page = response.get("nextPageToken")
        if not next_page:
            return users


def copy_firebase() -> None:
    cutover_record = LOCAL / "cutover.json"
    if cutover_record.exists() and json.loads(cutover_record.read_text()).get("public"):
        raise RuntimeError("Target accepts user writes; refuse to overwrite accounts from the frozen source")
    source_users = firebase_users("source")
    existing_users = firebase_users("target")
    source_ids = {user["localId"] for user in source_users}
    if any(user["localId"] not in source_ids for user in existing_users):
        raise RuntimeError("Target has users absent from source; refuse automatic reconciliation")
    source_config = api("source", "GET", f"https://identitytoolkit.googleapis.com/admin/v2/projects/{SOURCE}/config")
    hashing = source_config["signIn"]["hashConfig"]
    if hashing.get("algorithm") != "SCRYPT" or not hashing.get("signerKey"):
        raise RuntimeError("Source SCRYPT settings unavailable or unsupported")
    for user in source_users:
        if any(provider.get("providerId") == "password" for provider in user.get("providerUserInfo", [])) and not user.get("passwordHash"):
            raise RuntimeError("Source password provider record lacks password hash; refusing incomplete migration")
    imported = []
    for user in source_users:
        record = {key: value for key, value in user.items() if key in {
            "localId", "email", "emailVerified", "disabled", "displayName", "photoUrl",
            "phoneNumber", "customAttributes", "passwordHash", "salt", "createdAt", "lastLoginAt"}}
        # Password/phone are top-level credentials, not federated providers.
        providers = [provider for provider in user.get("providerUserInfo", []) if provider.get("providerId") not in {"password", "phone"}]
        if providers:
            record["providerUserInfo"] = providers
        imported.append(record)
    for offset in range(0, len(imported), 1000):
        body = {"users": imported[offset:offset + 1000], "hashAlgorithm": "SCRYPT", "signerKey": hashing["signerKey"], "saltSeparator": hashing.get("saltSeparator", ""), "rounds": hashing["rounds"], "memoryCost": hashing["memoryCost"], "allowOverwrite": True, "sanityCheck": True}
        result = api("target", "POST", f"https://identitytoolkit.googleapis.com/v1/projects/{TARGET}/accounts:batchCreate", body)
        if result.get("error"):
            raise RuntimeError(f"Firebase import returned {len(result['error'])} account errors")
    target_users = {user["localId"]: user for user in firebase_users("target")}
    if set(target_users) != source_ids:
        raise RuntimeError("Firebase UID sets differ")
    for user in source_users:
        target = target_users[user["localId"]]
        for field in ("email", "emailVerified", "disabled", "displayName", "phoneNumber", "customAttributes", "passwordHash", "salt"):
            default = False if field in {"emailVerified", "disabled"} else None
            if user.get(field, default) != target.get(field, default):
                raise RuntimeError(f"Firebase account verification failed in {field}")
    log("firebase_users_verified", users=len(source_users), password_hashes=sum(bool(user.get("passwordHash")) for user in source_users))


def setup_wif() -> None:
    """Trust only this repository's immutable GitHub IDs; no exported CI key."""
    parent = f"projects/{TARGET}/locations/global/workloadIdentityPools"
    pool = parent + "/github-actions"
    base = "https://iam.googleapis.com/v1/"
    if not exists("target", base + pool):
        operation = api("target", "POST", base + parent + "?workloadIdentityPoolId=github-actions", {"displayName": "GitHub Actions"})
        wait_operation("target", base, operation)
    provider = pool + "/providers/puls-gpw"
    condition = "assertion.repository_id == '1244345905' && assertion.repository_owner_id == '259179571'"
    current = exists("target", base + provider)
    if not current:
        operation = api("target", "POST", base + pool + "/providers?workloadIdentityPoolProviderId=puls-gpw", {
            "displayName": "puls-gpw GitHub", "attributeMapping": {"google.subject": "assertion.sub", "attribute.repository_id": "assertion.repository_id", "attribute.repository_owner_id": "assertion.repository_owner_id"},
            "attributeCondition": condition, "oidc": {"issuerUri": "https://token.actions.githubusercontent.com"}})
        wait_operation("target", base, operation)
    elif current.get("attributeCondition") != condition:
        raise RuntimeError("Existing WIF condition differs; review before changing")
    email = f"puls-gpw-ci@{TARGET}.iam.gserviceaccount.com"
    url = f"https://iam.googleapis.com/v1/projects/{TARGET}/serviceAccounts/{email}"
    policy = api("target", "POST", url + ":getIamPolicy", {})
    member = f"principalSet://iam.googleapis.com/projects/{SCOPE['target_number']}/locations/global/workloadIdentityPools/github-actions/attribute.repository_id/1244345905"
    binding = next((b for b in policy.get("bindings", []) if b["role"] == "roles/iam.workloadIdentityUser" and "condition" not in b), None)
    if binding is None:
        binding = {"role": "roles/iam.workloadIdentityUser", "members": []}
        policy.setdefault("bindings", []).append(binding)
    binding["members"] = sorted(set(binding["members"]) | {member})
    api("target", "POST", url + ":setIamPolicy", {"policy": policy})
    log("github_wif_ready", provider=provider.replace(TARGET, str(SCOPE["target_number"])))


def main() -> None:
    parser = argparse.ArgumentParser()
    actions = {"bootstrap": bootstrap, "firebase-enable": firebase_enable, "copy-secrets": copy_secrets, "copy-bq-preview": copy_bq_preview, "firebase-setup": firebase_setup, "copy-firebase": copy_firebase, "setup-wif": setup_wif}
    parser.add_argument("action", choices=actions)
    args = parser.parse_args()
    actions[args.action]()


if __name__ == "__main__":
    main()
