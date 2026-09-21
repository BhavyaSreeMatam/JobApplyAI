# Contributing

Follow the [README](README.md) to install Python and frontend dependencies. Keep changes focused and include regression tests for behavior changes.

## Before opening a pull request

```sh
cd backend
python -m unittest discover -s tests -v
cd ../frontend
npm run lint
npm test
npm run build
```

Install Chromium with `python -m playwright install chromium` to run browser fixtures. Tests use synthetic data, temporary databases, and mocked AI responses; they do not need API keys or job-site logins. CI installs Chromium too.

## Design boundaries

- Keep API validation in `backend/app/api`, business logic in `services`, and source-specific extraction in `sources`.
- Route resume generation through `apply_pipeline.tailor_document`; preserve evidence and provenance checks.
- Keep final application submission manual. Test browser changes against local HTML fixtures before trying them against a real site.
- Use synthetic profiles and `example.com` addresses in tests. Never commit resumes, personal databases, API keys, browser sessions, or generated exports.
- Explain behavior changes and any platform limitations in the pull request. Update documentation when the setup or workflow changes.
