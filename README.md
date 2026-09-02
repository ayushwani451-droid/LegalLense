# LegalLense (SIH26034)

AI-assisted Legal Metrology compliance inspection portal.

```
frontend/   Vite-built multi-page static site  -> deploy to Vercel
backend/    FastAPI + PaddleOCR service         -> deploy to Render/Railway/etc.
```

Current implemented pipeline: **image upload -> real PaddleOCR -> OCR result
shown in the browser.** Qwen3-VL extraction, the Legal Metrology rule
engine, and any real database are not implemented yet - the rest of the app
(dashboards, scan history, case management) runs on browser
`localStorage`/`sessionStorage` as a working prototype, clearly labeled as
such below.

---

## Project structure

```
stitch_legallense_compliance_portal/
├── frontend/
│   ├── index.html                      (redirects to legallense_home/code.html)
│   ├── legallense_home/code.html       Landing page
│   ├── login_legallense/code.html
│   ├── registration_manufacturer/code.html
│   ├── official_dashboard_legallense/code.html
│   ├── new_compliance_scan_legallense/code.html   <- real OCR upload flow lives here
│   ├── scan_history_legallense/code.html
│   ├── compliance_report_legallense/code.html
│   ├── case_details_legallense/code.html
│   ├── log_case_action_legallense/code.html
│   ├── manufacturer_dashboard_legallense/code.html
│   ├── new_self_check_legallense/code.html
│   ├── self_check_report_legallense/code.html
│   ├── revision_history_legallense/code.html
│   ├── compliance_summary_legallense/code.html
│   ├── public/assets/app.js            Mock data/session layer (localStorage)
│   ├── public/assets/ocr-client.js     Real fetch client for POST /api/ocr
│   ├── package.json / vite.config.js
│   ├── .env  (committed - no secret, just the local API URL default)
│   └── .env.example
├── backend/
│   ├── main.py                         FastAPI entrypoint
│   ├── api/ocr.py                      POST /api/ocr route (HTTP layer only)
│   ├── ocr/                            PaddleOCRService + preprocessing + schemas
│   ├── test_images/                    Real product photos used for regression (Parle-G, Good Day) + a synthetic label
│   ├── requirements.txt
│   └── .env.example
├── .gitignore
└── README.md
```

Each frontend page is a **real static file**, not an SPA route - e.g.
`/official_dashboard_legallense/code.html` is a literal file at that path
both in dev and in the Vercel build output. There is no client-side router
and therefore no rewrite rules are needed for deep links to work.

---

## Why Vite, given this was a static HTML export

The frontend is still exactly the same 14 hand-styled Tailwind pages - no
redesign, no page removed, no navigation changed. Vite was introduced only
so that:

- `VITE_API_BASE_URL` can be injected at build time (mandatory: the backend
  URL must never be hardcoded into application logic), and
- Vercel has a standard, zero-guesswork way to build and deploy the site
  (`Framework: Vite`, `npm run build`, output in `dist/`).

Every existing `.html` file is registered as its own entry in
`vite.config.js` (`build.rollupOptions.input`), so Vite treats this as the
true multi-page site it is and preserves every relative page-to-page link
(`../scan_history_legallense/code.html`, etc.) untouched.

**One real bug found and fixed while wiring this up:** Vite does not bundle
plain (non-`type="module"`) `<script src="...">` tags - it leaves the
reference alone and does *not* copy the referenced file into `dist/`. The
original `assets/app.js` and `assets/ocr-client.js` were referenced that
way, which meant the very first production build silently produced a
`dist/` with **no JS at all** (confirmed by inspecting the build output -
`dist/assets/` didn't exist). Fixed by moving both files into
`frontend/public/assets/` (Vite's "copy verbatim, don't process" directory)
and changing every page's script tag from `../assets/app.js` to the
root-relative `/assets/app.js`. Rebuilt and confirmed `dist/assets/app.js`
and `dist/assets/ocr-client.js` now exist and are byte-identical to source.

---

## API base URL strategy (`VITE_API_BASE_URL`)

Only `new_compliance_scan_legallense/code.html` currently calls the backend
(the OCR upload flow). It sets a small runtime global from the env var
before loading `ocr-client.js`:

```html
<script>window.__LL_API_BASE_URL__ = "%VITE_API_BASE_URL%";</script>
<script src="/assets/app.js"></script>
<script src="/assets/ocr-client.js"></script>
```

Vite replaces `%VITE_API_BASE_URL%` in any HTML file it processes, at both
`vite dev` and `vite build` time. `ocr-client.js` reads
`window.__LL_API_BASE_URL__`, and only falls back to
`http://localhost:8000` if that value is missing or is still the literal
unreplaced placeholder (i.e. the page was opened outside of Vite).

- **Local dev/build default:** `frontend/.env` (committed - contains only
  `VITE_API_BASE_URL=http://localhost:8000`, not a secret).
- **Production:** set `VITE_API_BASE_URL` in the Vercel Project Settings ->
  Environment Variables (Production scope) to the real deployed backend
  URL. Vite's env loading gives a real `process.env` value (which is how
  Vercel injects dashboard variables into the build) precedence over the
  committed `.env` file's default, so no code change is needed to point a
  production build at the real backend - only the Vercel dashboard setting.

No `.env.production` file is committed, since that would mean guessing/
hardcoding a production URL into the repo, which was explicitly avoided.

**No secrets are anywhere in the frontend.** There are no AI provider keys,
database keys, or private tokens in this project yet; the only `VITE_`
variable is a public URL the browser calls directly anyway.

---

## Backend configuration (environment variables)

See `backend/.env.example`. None of these are secrets:

| Variable | Default | Purpose |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | *(unset)* | Comma-separated exact origins allowed to call the API in production, e.g. `https://legallense.vercel.app`. Local `localhost`/`127.0.0.1` origins are always allowed regardless of this setting. |
| `MAX_UPLOAD_MB` | `15` | Upload size cap, enforced server-side (authoritative). Frontend copy must match. |
| `OCR_ENABLE_MKLDNN` | `false` | See "PaddleOCR cloud considerations" below. |
| `PORT` | *(set by host)* | Render/Railway set this automatically; the start command must read it, never hardcode a port. |

---

## Local development

**Backend** (from this directory):
```bash
cd backend
uv venv --python 3.11 .venv                 # first time only
uv pip install --python .venv -r requirements.txt   # first time only
.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --port 8000
```
(Run the uvicorn command from the **repo root**, not from inside `backend/`,
since it imports `backend.main` as a package - i.e. `cd` back up first, or
use `backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000` from the root.)

**Frontend** (separate terminal, from `frontend/`):
```bash
cd frontend
npm install       # first time only
npm run dev       # Vite dev server, prints its own local URL
```

Open the URL Vite prints, then navigate to `legallense_home/code.html`.

**Production build simulation (no Vercel account needed):**
```bash
cd frontend
npm run build
npm run preview   # serves the real dist/ output
```

---

## Deployment architecture

**Frontend -> Vercel. Backend (FastAPI + PaddleOCR) -> Render/Railway.**
Two separate services on two separate domains, connected only via
`VITE_API_BASE_URL` and CORS. No `vercel.json` is used or needed - Vercel
deploys `frontend/` as a plain standalone Vite project.

> **Note on a path not taken:** Vercel also offers a "Services" feature
> that can deploy a Python/FastAPI backend from the same repo as a second
> service on the same domain, via a root-level `vercel.json`. That was
> evaluated and deliberately **not** used here: Vercel Functions (which is
> what such a backend service runs as under the hood) impose a hard,
> non-configurable **4.5MB request body limit**, which routinely breaks
> real phone-camera photo uploads for this app's OCR feature, plus a
> duration ceiling that sits uncomfortably close to this backend's measured
> real-photo inference times (111-168s on CPU). Keeping the backend on a
> normal persistent host (Render/Railway) avoids all of that. If a
> `vercel.json` reappears at the repo root and Vercel starts asking for a
> multi-service config again, delete it - it is not part of this project's
> deployment.

### Deploying the frontend to Vercel

| Setting | Value |
|---|---|
| Framework Preset | **Vite** |
| Root Directory | **`frontend`** |
| Build Command | `npm run build` (Vercel default for Vite) |
| Output Directory | `dist` (Vercel default for Vite) |
| Install Command | `npm install` (default) |
| Environment Variable | `VITE_API_BASE_URL` = `https://<your-backend-domain>` (Production scope) |

Steps:
1. Push this repo to GitHub.
2. In Vercel: **Add New Project** -> import the repo -> set **Root
   Directory** to `frontend` -> Vercel auto-detects the Vite framework.
3. Add the `VITE_API_BASE_URL` environment variable pointing at the
   deployed backend (see below) before the first production build.
4. Deploy.

## Deploying the backend to Render/Railway

Example (Render "Web Service", or equivalent on Railway/any host that runs
an arbitrary start command and injects `$PORT`):

| Setting | Value |
|---|---|
| Root Directory | `backend` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Environment Variable | `CORS_ALLOWED_ORIGINS` = `https://<your-vercel-domain>` |

Notes:
- The start command above assumes the platform's working directory is
  `backend/` (Root Directory setting), so the module path is `main:app`,
  not `backend.main:app`. If your platform instead runs from the repo
  root, use `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.
- Never use the Windows-specific
  `backend\.venv\Scripts\python.exe -m uvicorn ...` form in production -
  that's a local-dev-only path. Production hosts provide their own Python
  and `uvicorn` on `PATH` after `pip install -r requirements.txt`.
- The first request after a cold start will be slower while PaddleOCR's
  models load; `main.py` warms the engine up at process startup
  (`@app.on_event("startup")`) specifically to move that cost to deploy
  time instead of the first real user's request.

---

## PaddleOCR cloud considerations

- CPU-only by default; no GPU is assumed or required.
- `enable_mkldnn` is disabled by default (`OCR_ENABLE_MKLDNN=false`)
  because MKL-DNN was found to crash PaddleOCR's PIR executor on the
  Windows CPU this project was developed on (see
  `backend/ocr/README.md` for the exact error and diagnosis). This safe
  default is preserved for cloud deployment rather than assuming a Linux
  host won't hit the same issue - it can be re-enabled per-deployment via
  the env var if verified safe on that specific host.
- Model weights are **not** bundled into the repo; PaddleOCR/PaddleX
  downloads them on first use and caches them in the process's home
  directory. On most PaaS hosts this cache does **not** persist across
  deploys (a fresh container each time), so expect a one-time model
  download on every new deploy/cold start - this is a real, known
  limitation to budget for, not something this pass fixes (would require
  baking models into a custom Docker image, out of scope here).
- The engine is a process-level singleton (`PaddleOCRService` caches it),
  so within a single running instance, models load once and are reused for
  every request - never redownloaded or reinitialized per-request.

---

## Database / storage - what's real vs. prototype

**There is no real database or cloud storage in this project yet.** The
non-OCR parts of the app (login, dashboards, scan history, case actions,
self-check revisions) run entirely on browser `localStorage` (mock data,
key `legallense_db_v1`) and `sessionStorage` (demo auth session). This is a
working prototype suitable for demoing the UI/UX end-to-end, but it is:

- per-browser, not shared between users or devices,
- cleared if the user clears site data,
- not an authentication system - do not present it as one.

No Supabase or other backend persistence exists to "preserve" - if/when
real auth and storage are added, they belong in the FastAPI backend, not in
frontend JavaScript.

---

## What actually got tested (see full report in the PR/session notes)

- `npm install` / `npm run build` / `npm run preview` all run successfully.
- `dist/` inspected directly - correct HTML files, `assets/app.js` and
  `assets/ocr-client.js` present, `%VITE_API_BASE_URL%` correctly resolved.
- Backend started locally, `GET /api/health` returns the documented shape.
- `POST /api/ocr` tested with: a real Parle-G product photo, a real
  Britannia Good Day product photo, PNG, WEBP, an unsupported file type, a
  corrupted file, and an empty file - all return the correct
  success/error shape.
- Full browser regression across all 14 pages plus a deep link opened
  directly (not via in-app navigation) via Playwright, with zero console
  errors.
- A real Vercel deployment was **not** performed - no Vercel account is
  authenticated in this environment (`vercel whoami` returns "Logged
  out"). Everything above was validated locally as far as possible instead
  of being assumed or fabricated.
- `vercel.json` (a multi-service config deploying the backend as a Vercel
  Function too) was tried, then deliberately removed after checking
  Vercel's current docs and finding a hard, non-configurable 4.5MB request
  body limit that breaks real-photo OCR uploads - see "Deployment
  architecture" above. Rebuilt and reverified after removal:
  `npm run build` succeeds with no warnings, `dist/assets/app.js` and
  `dist/assets/ocr-client.js` are present and byte-identical to source,
  `%VITE_API_BASE_URL%` still resolves correctly, and `npm run preview`
  serves the homepage and both JS assets with HTTP 200. `backend/main.py`
  and `backend/api/ocr.py` keep a small dual-import fallback (support
  being imported as either `backend.main`/`backend.api.ocr` or bare
  `main`/`api.ocr`) - harmless leftover robustness from that experiment,
  not something that needed reverting.
