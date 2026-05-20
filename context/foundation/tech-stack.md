---
starter_id: fastapi
package_manager: uv
project_name: puls-gpw
hints:
  language_family: python
  team_size: solo
  deployment_target: google-cloud-run
  ci_provider: github-actions
  ci_default_flow: auto-deploy-on-merge
  bootstrapper_confidence: first-class
  path_taken: custom
  quality_override: false
  self_check_answers:
    typed: true
    from_official_starter: true
    conventions: true
    docs_current: true
    can_judge_agent: true
  has_auth: false
  has_payments: false
  has_realtime: false
  has_ai: true
  has_background_jobs: true
---

## Why this stack

Solo developer building a Python scheduled pipeline with a 3-week after-hours timeline. FastAPI is the only lightweight Python starter in the registry passing all four agent-friendly quality gates: typed via Pydantic, convention-based structure, popular in Python training data, well-documented. Django was excluded by explicit user preference for a minimal stack — the project uses BigQuery directly, making Django's ORM unnecessary. FastAPI's uv package manager and Pydantic models align naturally with the AI output validation layer (supervisor agent uses structured schemas to verify post quality). The HTTP server is optional for this use case — the pipeline runs as a Cloud Run Job triggered by Cloud Scheduler, so the entry point is a pure Python script rather than an HTTP handler; FastAPI's web layer can serve as a health check endpoint or be omitted entirely. Deployment targets Google Cloud Run Jobs with GitHub Actions auto-deploy on merge; this requires a Dockerfile and manual Cloud Run Job configuration beyond the FastAPI card's defaults (fly/railway/render). AI and background-jobs feature flags are set; auth, payments, and realtime are out of scope per PRD non-goals.
