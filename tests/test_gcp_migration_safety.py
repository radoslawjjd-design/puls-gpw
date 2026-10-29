"""Migration safety guards exercised without credentials or network access."""

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "context/changes/gcp-account-migration-kacha"


@pytest.fixture
def migration(tmp_path, monkeypatch):
    folder = tmp_path / "context/changes/gcp-account-migration-kacha"
    folder.mkdir(parents=True)
    local = tmp_path / ".cache/gcp-account-migration-kacha"
    local.mkdir(parents=True)
    (local / "scope.json").write_text(json.dumps({
        "source_project": "puls-gpw", "target_project": "puls-gpw-kp-20261029",
        "source_number": "139573454149", "target_number": "530001799240",
        "source_account": "source@example.invalid", "target_account": "target@example.invalid",
        "target_billing": "EXAMPLE-BILLING", "region": "europe-central2",
    }))
    (local / "source-20261029").mkdir()
    (local / "source-20261029/inventory.json").write_text('{"bq_metadata":{}}')
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def load(name):
        path = folder / (name + ".py")
        shutil.copyfile(SCRIPTS / path.name, path)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    cloud, deploy, cutover = load("cloud"), load("deploy"), load("cutover")

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected real cloud operation")

    for module in (cloud, deploy, cutover):
        monkeypatch.setattr(module, "api", forbidden)
    monkeypatch.setattr(cloud, "token", forbidden)
    monkeypatch.setattr(deploy, "gcloud", forbidden)
    monkeypatch.setattr(cutover, "gcloud", forbidden)
    return SimpleNamespace(cloud=cloud, deploy=deploy, cutover=cutover, load=load)


@pytest.mark.parametrize("flags", [{}, {"frozen": False}, {"frozen": True, "public": True}])
def test_final_sync_never_runs_before_freeze_or_after_publication(migration, flags):
    migration.cutover.save(**flags)
    with pytest.raises(RuntimeError, match="requires source frozen"):
        migration.cutover.copy_final()


def test_auth_import_cannot_overwrite_passwords_after_cutover(migration):
    migration.cutover.save(public=True)
    with pytest.raises(RuntimeError, match="refuse to overwrite"):
        migration.cloud.copy_firebase()


@pytest.mark.parametrize("action", ["promote", "publish", "finish_access"])
def test_incomplete_cutover_cannot_open_access_or_remove_copy_permissions(migration, action):
    with pytest.raises(RuntimeError):
        getattr(migration.cutover, action)()


def test_staging_manifest_preserves_job_limits_and_secret_references(migration):
    task = {"maxRetries": 1, "timeoutSeconds": "300", "serviceAccountName": "old",
            "containers": [{"image": "old", "resources": {"limits": {"memory": "512Mi"}}, "env": [
                {"name": "X_AUTO_PUBLISH", "value": "true"},
                {"name": "GOOGLE_CLOUD_PROJECT", "value": "puls-gpw"},
                {"name": "SMTP_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "smtp-password", "key": "latest"}}},
            ]}]}
    source = {"apiVersion": "run.googleapis.com/v1", "kind": "Job", "metadata": {"name": "puls-gpw-post"},
              "spec": {"template": {"spec": {"taskCount": 1, "template": {"spec": task}}}}}
    before = copy.deepcopy(source)
    result = migration.deploy.manifest(source)["spec"]["template"]["spec"]["template"]["spec"]
    env = {item["name"]: item for item in result["containers"][0]["env"]}
    assert env["X_AUTO_PUBLISH"]["value"] == "false"
    assert env["BIGQUERY_DATASET"]["value"] == "espi_ebi_preview"
    assert env["GOOGLE_CLOUD_PROJECT"]["value"] == migration.cloud.TARGET
    assert env["SMTP_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "smtp-password"
    assert result["maxRetries"] == 1 and result["timeoutSeconds"] == "300"
    assert result["containers"][0]["resources"]["limits"]["memory"] == "512Mi"
    assert source == before
    task["containers"][0]["env"].append({"name": "UNKNOWN", "value": "<redacted>"})
    with pytest.raises(RuntimeError, match="unresolved"):
        migration.deploy.manifest(source)


@pytest.mark.parametrize("ingestion,differences", [(False, 0), (True, 0), (False, 1)])
def test_copy_handles_streaming_rows_and_requires_full_multiset_equality(migration, monkeypatch, ingestion, differences):
    operator = migration.cutover
    operator.save(frozen=True, frozen_ms=123456789)
    table = "billing_history" if ingestion else "company_daily_stats"
    metadata = {"tableReference": {"projectId": "puls-gpw", "datasetId": "espi_ebi", "tableId": table},
                "schema": {"fields": [{"name": "value", "type": "STRING"}]}, "numRows": "2"}
    if ingestion:
        metadata["timePartitioning"] = {"type": "DAY"}
    else:
        metadata["streamingBuffer"] = {"estimatedRows": "1"}
    monkeypatch.setattr(operator, "INVENTORY", {"bq_metadata": {"espi_ebi": {"tables": [metadata]}}})
    jobs, auth_imports = [], []
    monkeypatch.setattr(operator, "bq_job", lambda job_id, config: jobs.append(copy.deepcopy(config)))
    monkeypatch.setattr(operator, "copy_firebase", lambda: auth_imports.append(True))

    def fake_api(side, method, url, body=None):
        if "/queries/" in url:
            return {"rows": [{"f": [{"v": str(differences)}]}]}
        if method == "GET" and "/tables/" in url:
            return copy.deepcopy(metadata)
        raise AssertionError((side, method, url))

    monkeypatch.setattr(operator, "api", fake_api)
    monkeypatch.setattr(operator, "exists", lambda *args: metadata)
    if differences:
        with pytest.raises(RuntimeError, match="Row multisets differ"):
            operator.copy_final()
        assert not operator.state().get("final_verified")
        assert not auth_imports
    else:
        operator.copy_final()
        assert operator.state()["final_verified"] and auth_imports == [True]
    if ingestion:
        assert jobs[0]["copy"]["sourceTable"]["tableId"].endswith("@123456789")
        assert jobs[0]["copy"]["writeDisposition"] == "WRITE_EMPTY"
        assert "FOR SYSTEM_TIME AS OF TIMESTAMP_MILLIS(123456789)" in jobs[1]["query"]["query"]
    else:
        assert "copy" not in jobs[0]  # Table-copy jobs omit streaming-buffer rows.
        assert jobs[0]["query"]["writeDisposition"] == "WRITE_EMPTY"
    comparison = jobs[1]["query"]["query"]
    assert "TO_JSON_STRING" in comparison and "COUNT(*) AS n" in comparison
    assert comparison.count("EXCEPT DISTINCT") == 2


def test_failed_copy_job_is_not_retried_with_an_overwrite(migration, monkeypatch):
    monkeypatch.setattr(migration.cutover, "exists", lambda *args: {"status": {"state": "DONE", "errorResult": {"message": "destination not empty"}}})
    with pytest.raises(RuntimeError, match="destination not empty"):
        migration.cutover.bq_job("existing", {"copy": {"writeDisposition": "WRITE_EMPTY"}})


def test_firebase_import_preserves_credentials_without_federating_password_provider(migration, monkeypatch, capsys):
    source = [{"localId": "stable-uid", "email": "user@example.invalid", "emailVerified": True,
               "passwordHash": "fake-hash", "salt": "fake-salt", "disabled": False,
               "customAttributes": '{"admin":true}', "providerUserInfo": [{"providerId": "password"}]}]
    target_calls = 0

    def users(side):
        nonlocal target_calls
        if side == "source":
            return source
        target_calls += 1
        return [] if target_calls == 1 else source

    monkeypatch.setattr(migration.cloud, "firebase_users", users)
    posted = []

    def fake_api(side, method, url, body=None):
        if method == "GET":
            return {"signIn": {"hashConfig": {"algorithm": "SCRYPT", "signerKey": "fake-key", "rounds": 8, "memoryCost": 14}}}
        posted.append(body)
        return {}

    monkeypatch.setattr(migration.cloud, "api", fake_api)
    migration.cloud.copy_firebase()
    user = posted[0]["users"][0]
    assert user["localId"] == "stable-uid"
    assert user["passwordHash"] == "fake-hash" and user["salt"] == "fake-salt"
    assert user["customAttributes"] == '{"admin":true}' and user["emailVerified"]
    assert not user.get("providerUserInfo")
    output = capsys.readouterr().out
    assert "fake-hash" not in output and "fake-key" not in output and "user@example.invalid" not in output


def test_missing_password_hash_aborts_before_import(migration, monkeypatch):
    monkeypatch.setattr(migration.cloud, "firebase_users", lambda side: [{"localId": "uid", "providerUserInfo": [{"providerId": "password"}]}] if side == "source" else [])

    def only_config(side, method, url, body=None):
        assert method == "GET"
        return {"signIn": {"hashConfig": {"algorithm": "SCRYPT", "signerKey": "fake-key"}}}

    monkeypatch.setattr(migration.cloud, "api", only_config)
    with pytest.raises(RuntimeError, match="lacks password hash"):
        migration.cloud.copy_firebase()


def test_inventory_redacts_values_but_keeps_secret_references(migration):
    inventory = migration.load("inventory")
    result = inventory.sanitize({"signerKey": "fake-key", "env": [
        {"name": "UNEXPECTED", "value": "fake-secret"},
        {"name": "GOOGLE_CLOUD_PROJECT", "value": "puls-gpw"},
        {"name": "SMTP_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "smtp-password"}}},
    ]})
    assert "fake-secret" not in json.dumps(result) and "fake-key" not in json.dumps(result)
    assert result["env"][1]["value"] == "puls-gpw"
    assert result["env"][2]["valueFrom"]["secretKeyRef"]["name"] == "smtp-password"
