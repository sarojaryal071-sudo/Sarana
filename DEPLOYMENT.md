# Deploying Sarana — Render (backend) + Vercel (frontend)

This document is specific to this repository's actual code — every command
and setting below was verified against the source, not assumed. See the
deployment-readiness audit and implementation reports (earlier in this
project's history) for the full reasoning; this file is the practical
"do this" reference.

No deployment config files (`render.yaml`, `vercel.json`) are included —
see **Why no render.yaml/vercel.json** at the end for why.

---

## A. GitHub preparation

This repository has never been a git repository before. Before the very
first `git init` / `git add` / commit:

1. Confirm the root `.gitignore` exists and is in place (it is — see
   `.gitignore`). It protects: `.venv/`, `__pycache__/`, `.env`/`.env.*`
   (except `.env.example`), `config/api_keys.json` (the real Gemini key),
   `config/certs/` (a real private key + self-signed cert), `*.key`/`*.crt`,
   `memory/long_term.json` (real personal data — names, saved facts,
   relationship notes), `uploads/`, and the frontend's `node_modules/`,
   `dist/`, `.env.local`.
2. Confirm `config/api_keys.json` contains your real local key and is
   **not** about to be committed (`git status` after `git add .` should
   never list it). `config/api_keys.example.json` (placeholders only) is
   the one that ships.
3. A secret scan of every tracked-file-type in the repo (excluding
   `.venv/`, `node_modules/`, `__pycache__/`) found **no** real API key,
   token, or private key outside `config/api_keys.json` and
   `config/certs/` — both already correctly ignored. Nothing needed
   scrubbing.
4. Optional cleanup (not required, not done automatically): `project-tree.txt`
   at the repo root is a large (100KB+) generated directory listing
   (including `.venv/` contents) with no real value once committed —
   consider excluding it, but this is not a blocker.

Once `.gitignore` is confirmed in place, `git init`, commit, and push are
safe to run.

---

## B. Render setup

- **Service type:** Web Service (not Background Worker — the frontend
  needs a public HTTPS/WSS endpoint, which only a Web Service provides).
- **Root directory:** repository root.
- **Build command:**
  ```bash
  pip install -r requirements-backend.txt
  ```
- **Start command:**
  ```bash
  python server_main.py
  ```
  Not `uvicorn dashboard.server:app` — `dashboard.server` has no
  module-level `app`; `DashboardServer` (and its `.app`) is created
  *inside* `JarvisLive.run()`, which `server_main.py` already calls
  correctly with `auto_start=False`.
- **Environment variables:** see the table below.

### Render Environment Variables

| Variable | Required? | Controls | Example | Secret? | Auto-supplied by Render? |
|---|---|---|---|---|---|
| `GEMINI_API_KEY` | **Required** | The Gemini API key `main.py`'s `_get_api_key()` uses (checked before the desktop's `config/api_keys.json` fallback) | *(enter your real key directly in Render's dashboard)* | **Yes — secret** | No |
| `PORT` | Not needed | Which port `dashboard/server.py` binds to | — | No | **Yes** — Render sets this automatically; don't configure it manually |
| `SARANA_ALLOWED_ORIGINS` | Required once Vercel is deployed | Additional CORS origin(s) `_cors_allowed_origins()` accepts, on top of the built-in localhost dev origins | `https://your-vercel-domain.vercel.app` | No — public URL | No |

---

## C. Vercel setup

- **Root directory:** `frontend`
- **Framework preset:** Vite (auto-detected)
- **Install command:** `npm install` (default)
- **Build command:** `npm run build` (default — maps to `vite build`)
- **Output directory:** `dist` (Vite's default; confirmed via a local build)
- **SPA rewrite:** not required — single route, no client-side router in
  this project.

### Vercel Environment Variables

| Variable | Required? | Purpose | Example |
|---|---|---|---|
| `VITE_JARVIS_BACKEND_URL` | **Required** | The only variable the frontend reads anywhere (`frontend/src/lib/api.js`) — base URL of the Render backend. `wss://` for WebSockets is derived from this automatically; there is no separate WS variable to set. | `https://your-backend.onrender.com` |

This is a `VITE_*` variable — it's bundled into the shipped JS and is
public by design (it's just a URL). No backend secret is ever read by, or
placed into, the frontend.

---

## D. Connection sequence

```text
GitHub
   ↓  (git init / commit / push — after confirming .gitignore, per section A)
Render backend
   ↓  create Web Service, set GEMINI_API_KEY, deploy
Get Render URL  (https://your-backend.onrender.com)
   ↓
Vercel frontend
   ↓  create project, root=frontend
Set VITE_JARVIS_BACKEND_URL = <Render URL from above>
   ↓  deploy
Get Vercel URL  (https://your-project.vercel.app)
   ↓
Set SARANA_ALLOWED_ORIGINS = <Vercel URL from above>  (in Render)
   ↓
Redeploy/restart the Render service so the new CORS origin takes effect
   ↓
Test (section E)
```

---

## E. Production test checklist

1. **Render service starts** — check the log stream for exactly:
   `[Sarana] Backend is running.` / `[Sarana] Waiting for frontend to start Jarvis.`
2. **`/api/session` responds:**
   ```bash
   curl https://your-backend.onrender.com/api/session
   ```
   Expect `{"assistant_name": "...", "tools": [...], "desktop_connected": false}`.
3. **Frontend loads** — open the Vercel URL, confirm the page renders
   with the real assistant name from step 2 (not a placeholder).
4. **Username login works** — enter a name, click LOGIN; main UI becomes
   interactive.
5. **Remote Access PIN works where applicable** — this needs a PIN
   source; on a pure-cloud deployment with no desktop client ever
   connecting, check the Render log stream for the console-printed PIN
   (added in an earlier phase) as the only available source.
6. **Sarana starts only when intended** — confirm no Gemini/mic/speaker
   activity appears in Render's logs until the username login (which
   auto-wakes, per an earlier phase) actually happens.
7. **Gemini connection succeeds** — after login, logs show
   `[JARVIS] Connecting...` then `Connected.`, not a repeating
   error/reconnect loop.
8. **Text command works** — send a message from the frontend, confirm a
   real response appears.
9. **WebSocket logs arrive** — the log panel updates live via `/ws`.
10. **Browser audio works** — confirm audio bytes/playback arrive via
    `/ws/audio-out` (devtools Network tab, or audible playback).
11. **Reconnect works** — restart the Render service, confirm the
    frontend's connection banner appears then recovers automatically.
12. **No laptop `sounddevice` dependency exists on Render** — confirmed
    by design: `_listen_audio()`/`_play_audio()` now degrade gracefully
    with no local audio hardware (deployment-readiness phase), verified
    by dedicated tests (`tests/test_deployment_readiness.py`).
13. **Desktop local application still works** —
    ```powershell
    python main.py
    ```
    starts exactly as before; none of the deployment changes alter the
    desktop code path (env-var checks are additive-before-fallback,
    `PORT` still defaults to 8000 locally, audio guards only change
    *failure* behavior which real hardware never triggers).

---

## Why no `render.yaml`/`vercel.json`

Both Render and Vercel's own dashboards can fully configure this project
(service type, root directory, build/start commands, environment
variables) without a declarative config file — this is a single backend
service and a single frontend project, not a multi-service setup where
infra-as-code pays for itself. Adding one now would be one more file to
keep in sync with the dashboard settings for no functional benefit at
this stage. Worth reconsidering later if reproducible/CI-driven deploys
become a priority — not needed for this first deployment.
