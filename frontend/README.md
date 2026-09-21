# JobApplyAI frontend

React dashboard for job discovery, resume management, candidate profiles, application review, and settings. See the [project README](../README.md) for the full setup and workflow.

```sh
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

The API defaults to `http://127.0.0.1:8000`. To change the API port, copy `.env.example` to `.env.local`, update `VITE_API_BASE_URL`, and restart Vite. Keep the UI on port 5173 unless you also update backend CORS configuration.

| Command | Purpose |
| --- | --- |
| `npm run lint` | Check JavaScript and React rules |
| `npm test` | Run Node regression tests |
| `npm run build` | Compile the production bundle |
| `npm run preview` | Inspect the compiled UI; the API still runs separately |

Never put API keys or candidate details in `VITE_*` variables: Vite exposes them in the client bundle.
