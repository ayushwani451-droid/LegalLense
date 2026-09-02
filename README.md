# LegalLense (SIH26034)

AI-assisted Legal Metrology compliance inspection portal.

```
frontend/   Vite-built multi-page static site
backend/    FastAPI + PaddleOCR service
```

Two deployment topologies are supported - see "Deployment: two supported
topologies" below for the tradeoffs:
- **Option A (recommended):** frontend on Vercel, backend on Render/Railway.
- **Option B:** both on Vercel together via `vercel.json` (Vercel
  Services) - convenient, but the backend then runs under Vercel Functions'
  platform limits, which matter for this specific OCR-upload workload.

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

## Deployment: two supported topologies

**Option A - split (recommended):** frontend on Vercel, backend on
Render/Railway, on different domains. Simpler, no platform limits on the
OCR path, no `vercel.json` needed.

**Option B - full-stack on Vercel (Vercel Services):** frontend and backend
both deploy from one Vercel project via `vercel.json`, on one shared
domain, with `/api/*` rewritten to the backend service. Convenient (one
project, one domain, atomic deploys), but the backend then runs as a
**Vercel Function**, which has real, non-configurable platform limits that
matter for this specific OCR workload - see the callout below before
choosing this path.

### Option A: frontend on Vercel

There is no `vercel.json` needed for this option - routing is literal
static files, not an SPA requiring rewrites.

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

### Option B: full-stack on Vercel via Services (`vercel.json`)

`vercel.json` at the repo root defines both services and routes `/api/*` to
the backend:

```json
{
  "services": {
    "frontend": { "root": "frontend", "framework": "vite" },
    "backend": { "root": "backend" }
  },
  "rewrites": [
    { "source": "/api(/.*)?", "destination": { "type": "service", "service": "backend" } },
    { "source": "/(.*)", "destination": { "type": "service", "service": "frontend" } }
  ]
}
```

Because both services share one domain, the frontend should call the API
**same-origin** rather than an absolute URL. `ocr-client.js` already
handles this: if `VITE_API_BASE_URL` is unset (or your Vercel dashboard
won't accept an empty string value), it automatically falls back to
same-origin relative calls (`/api/ocr`) on any non-localhost domain, and to
`http://localhost:8000` only when the page itself is served from
`localhost`/`127.0.0.1`. So for this option, either:
- leave `VITE_API_BASE_URL` unset in the Vercel project (recommended - the
  fallback does the right thing automatically), or
- explicitly set it to an empty string if your dashboard allows saving one.

Import steps: **Add New Project** -> import the repo -> Vercel detects
`vercel.json` and both services automatically -> deploy.

> **⚠️ Read this before choosing Option B.** Backend code on Vercel runs as
> a Vercel Function (confirmed against Vercel's current docs, not assumed),
> which has hard limits that matter for this specific OCR workload:
>
> - **4.5MB request body limit - not configurable.** This is a platform
>   floor, enforced ahead of anything this app's own `MAX_UPLOAD_MB` checks.
>   A real phone-camera photo of a product label routinely exceeds this
>   (our own two real test photos happened to be ~2.4MB PNGs, comfortably
>   under it, but a full-resolution JPEG easily is not). Uploads over
>   4.5MB will fail with a Vercel-level `413`, before our own 15MB check
>   ever runs. Mitigate by compressing/resizing images client-side before
>   upload, or use Option A instead.
> - **Duration limits**: 300s on Hobby (hard cap), up to 800s/1800s(beta) on
>   Pro/Enterprise with configuration. Our own measured real-photo
>   inference times were **111s and 168s** on this CPU-only setup - well
>   within Hobby's 300s ceiling per request, but with little headroom, and
>   Pro/Enterprise billing is by active CPU time, so slow OCR requests cost
>   more there than a flat-rate host.
> - **~500MB (Python) uncompressed bundle limit** (5GB with "Large
>   functions" beta + Fluid compute), which `paddlepaddle` + `paddleocr` +
>   `paddlex` + `opencv-contrib-python` can plausibly approach - not
>   verified against an actual Vercel deployment in this pass (no Vercel
>   account was available to test with).
>
> None of this is a reason Option B is broken - Vercel's Python runtime and
> multi-service Fluid compute are real and current - it's a reason to
> genuinely weigh it against Option A for an OCR-uploads app specifically,
> rather than default to "one project is simpler."

## Deploying the backend to Render/Railway (Option A)

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

### Option B (`vercel.json` / Vercel Services) - what's verified vs. not

- **Verified:** `backend/main.py` and `backend/api/ocr.py` import correctly
  both as `backend.main`/`backend.api.ocr` (repo root as working
  directory) and as bare `main`/`api.ocr` (`backend/` itself as working
  directory, which is what Vercel's per-service `"root": "backend"` and
  Vercel's Python entrypoint detection require) - checked by explicitly
  importing both ways and confirming the FastAPI app loads with its routes
  intact each time.
- **Verified:** `ocr-client.js`'s same-origin fallback logic
  (`defaultBaseUrlForCurrentHost`) correctly returns `http://localhost:8000`
  when the page is served from `localhost` (re-ran the full browser
  regression + a real OCR upload after this change - still works, zero
  console errors).
- **Not verified:** the same fallback actually resolving to same-origin
  `""` on a real non-localhost Vercel domain, the `/api/*` rewrite actually
  routing to the backend service, and the backend actually cold-starting
  and running PaddleOCR inference under real Vercel Function constraints
  (4.5MB body limit, duration limit, bundle size) - none of this can be
  checked without an authenticated Vercel account and a real deployment,
  which was not available in this environment.
