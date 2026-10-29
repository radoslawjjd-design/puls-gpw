"""Read-only destination runtime probe, executed using a job argument override."""

import json
import os

from google.cloud import bigquery
from src.gemini_client import GEMINI_MODEL, GEMINI_VISION_MODEL, get_client
from src.http_client import _get_http_client


def report(name: str, **fields: object) -> None:
    print(json.dumps({"migration_probe": name, **fields}), flush=True)


project = os.environ["GOOGLE_CLOUD_PROJECT"]
if project != "puls-gpw-kp-20261029":
    raise RuntimeError("Wrong probe project")
dataset = os.environ["BIGQUERY_DATASET"]
client = bigquery.Client(project=project)
row = next(iter(client.query(f"SELECT COUNT(*) AS n FROM `{project}.{dataset}.announcements`").result()))
report("bigquery", ok=row.n > 0, rows=row.n)

for model in (GEMINI_MODEL, GEMINI_VISION_MODEL):
    try:
        response = get_client().models.generate_content(model=model, contents='Return JSON with status set to "ok".', config={"max_output_tokens": 256, "temperature": 0, "response_mime_type": "application/json"})
        report("vertex", ok=json.loads(response.text).get("status") == "ok", model=model)
    except Exception as exc:
        report("vertex", ok=False, model=model, error_type=type(exc).__name__, code=getattr(exc, "code", None), status=getattr(exc, "status", None), message=str(getattr(exc, "message", ""))[:1000])

try:
    response = _get_http_client().get("https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek/1")
    report("bankier", ok=response.status_code == 200, status=response.status_code, mitigation=response.headers.get("cf-mitigated"))
except Exception as exc:
    report("bankier", ok=False, error_type=type(exc).__name__)
