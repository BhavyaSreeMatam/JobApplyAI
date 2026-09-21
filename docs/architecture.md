# Architecture and tradeoffs

JobApplyAI is a local, single-user application. Its layers separate external-site extraction, candidate facts, document generation, and browser actions so each can be checked independently.

## Module map

| Area | Main files | Responsibility |
| --- | --- | --- |
| API lifecycle | `backend/app/main.py` | Register routes, initialize schema, health checks, browser shutdown |
| Candidate state | `api/candidate.py`, `services/profile_merge.py` | Revision checks, provenance, merge previews, confirmed profile data |
| Discovery | `services/source_sync.py`, `sources/` | Normalize postings from board feeds, browser searches, and individual URLs |
| Tailoring | `services/apply_pipeline.py`, `services/tailoring.py` | Coordinate generation and the shared document path |
| AI boundary | `services/claude_client.py` | API credentials, model options, structured-output validation, error translation |
| Evidence and layout | `services/evidence.py`, `document_check.py`, `master_resume.py`, `layout_*` | Preserve supported claims and validate rendered documents |
| Match reports | `services/job_match.py` | Deterministic posting-to-resume fit and requirement coverage |
| Browser | `services/browser_session.py`, `browser_actions.py`, `autofill.py`, `workday.py` | Session ownership, action guards, field mapping, uploads, repeating sections |
| Exports | `services/tracker_excel.py`, `spreadsheet_safety.py` | Value-only workbooks and safe web hyperlinks |
| UI | `frontend/src/` | Jobs dashboard, profiles, resumes, sources, application review, settings |

Paths after the first two rows are relative to `backend/app/` unless otherwise stated.

## Why these choices

- **SQLite:** simple local persistence and no database service to operate. It is suitable for one user's workflow, not a shared multi-user deployment.
- **Structured model output:** Pydantic schemas make generation results inspectable. A schema-rejection fallback requests JSON and validates it locally; it does not remove validation.
- **Deterministic validation:** model-generated rewrites are checked against candidate evidence and document constraints. These checks catch covered failure classes; they are not a proof that every sentence is correct.
- **One tailoring path:** application packages and library-built resumes share document validation, reducing inconsistent behavior between entry points.
- **Visible browser sessions:** users can sign in, inspect progress, and finish unsupported steps. Browser profiles are sensitive local state.
- **Human submission:** browser helpers classify supported actions and leave final submission to the candidate.

## Known engineering limits

- Long generation and browser operations run within API requests. There is no durable task queue, automatic job recovery after a process crash, or multi-worker browser coordination.
- Schema additions use `create_all` plus a small additive-column helper. There is no versioned migration history or general rollback mechanism.
- The API does not authenticate requests. Loopback binding and local use are deployment assumptions, not a substitute for authentication in a hosted service.
- Export paths are shared local artifacts. Concurrent export/download requests are not isolated snapshots.
- Scraping adapters depend on external page structure, login state, and site behavior. A successful local fixture test does not verify a live employer form.
- Model availability, latency, charges, and output quality depend on the configured account and model. The test suite mocks AI responses.

A hosted version would need authentication, per-user storage, job scheduling, browser isolation, migrations, and operational monitoring before public exposure. Those systems are intentionally outside the current local workflow.
