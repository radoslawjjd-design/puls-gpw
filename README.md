# puls-gpw

ESPI/EBI report analyzer for GPW / NewConnect. The service scrapes issuer
disclosures, classifies them with Gemini, and surfaces the market-relevant
ones (with an X-post draft for the highest-impact items).

## Stack

- Python 3.13, FastAPI, managed with `uv`
- Gemini via Vertex AI for classification
- BigQuery for storage

See `AGENTS.md` for contributor and agent conventions.
