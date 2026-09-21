# Repository polish validation

Reviewed against GitHub `main` commit `68039560655d1fdd663575904f7e502161279224`. Its source tree matched the uploaded ZIP.

## Changes

- Replaced outdated setup instructions and inaccurate feature, scoring, privacy, pricing, and export descriptions with implementation-grounded documentation.
- Added architecture, contribution, and data-handling documentation, plus backend/frontend GitHub Actions checks.
- Removed stale repair reports, unused template artwork, duplicate ignore entries, and personal identifiers in test fixtures.
- Replaced template browser branding and added a configurable frontend API URL.
- Made Sources load failures visible and retryable; moved its notice component out of the render function.
- Replaced effect-driven imported-job selection with an explicit fetch; exposed application-package load errors.
- Clarified verified-skill settings and disabled unsupported effort controls for Haiku.
- Made settings writes atomic, handled non-object JSON files, and isolated mutable defaults between reads.
- Made the health endpoint check the database and return 503 when unavailable.
- Omitted unsupported adaptive-thinking options for Haiku and made truncated model responses actionable errors.
- Forced exported text cells to remain text, restricted clickable links to HTTP(S), and preserved zero-valued match scores.
- Added ten backend regression tests for these reliability changes.

## Local results

| Check | Result |
| --- | --- |
| Python 3.12 backend unittest discovery | 334 checks; passed with 4 browser-related skips |
| Node 24 frontend tests | 7 passed |
| Oxlint | Passed with no warnings |
| Vite production build | Passed |
| Git whitespace check | Passed |

Dependencies were installed from the repository requirements and npm lockfile in an isolated environment. Playwright's bundled Node executable could not run here; using the system Node allowed the suite to start, but Chromium download timed out. Consequently the four browser fixture groups were skipped. CI installs Chromium explicitly so those fixtures can run on GitHub.

No paid model calls, real job-site sign-ins, form submissions, or Windows launcher execution were performed. The frontend was compiled and linted but was not visually verified in a browser. GitHub Actions results are not claimed until the workflow runs remotely.

The model-specific request change follows [Anthropic's model capability documentation](https://platform.claude.com/docs/en/models/overview): Haiku 4.5 uses extended thinking and does not support the adaptive-thinking/effort options offered by the larger models. Real account access remains an integration check.
