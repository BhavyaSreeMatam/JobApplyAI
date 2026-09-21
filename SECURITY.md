# Security and data handling

JobApplyAI is a single-user local application. The API has no authentication or tenant isolation. Bind it to `127.0.0.1`; do not expose it through a public tunnel or deploy it as a shared service without adding authentication and access controls.

## Local data

- `backend/settings.json` stores preferences and an API key if saved through Settings. The key is redacted in API responses, but the file itself is not encrypted.
- `backend/data/` contains the SQLite database, imported resumes, and generated documents.
- `backend/browser-profiles/` contains the persistent browser session, including login cookies.
- `backend/exports/` contains generated Excel workbooks.

These paths are ignored by Git. Ignore rules do not remove files already committed or erase history. If a secret is published, revoke it and review repository history.

## External services

AI features send relevant resume/profile content and job descriptions to Anthropic. Source discovery contacts job boards and company sites. Assisted form filling writes candidate details and attaches documents to the selected employer page; sites may save draft fields before final submission.

Final submission stays manual. Agreement auto-acceptance is an explicit setting and is off by default. Review every completed form and generated document. Extraction, provenance checks, and submission guards reduce errors but cannot guarantee correctness on every third-party site.

## Reporting a vulnerability

Use the repository's private vulnerability reporting feature when available. Do not put credentials, resumes, session cookies, or personal records into public issues. If private reporting is unavailable, use a public issue only to request a private contact channel, without disclosing the vulnerability or sensitive data.
