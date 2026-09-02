/* Thin fetch wrapper for the LegalLense OCR backend (FastAPI + PaddleOCR).
   Kept separate from app.js (the local mock-data/session layer) since this
   is the one module in the frontend that actually talks to a real server.

   API base URL resolution (no secrets involved - this is a public endpoint
   the browser calls directly):
     1. window.__LL_API_BASE_URL__, injected per-page from the Vite-managed
        VITE_API_BASE_URL env var via an inline script (see e.g.
        new_compliance_scan_legallense/code.html). Vite replaces the
        %VITE_API_BASE_URL% placeholder in HTML at build/dev time.
        - A real URL (e.g. https://api.example.com) is used as-is: the
          frontend and backend are on different origins/hosts.
        - An EMPTY STRING is a deliberate, valid value: it means "call the
          API on this same origin" (e.g. requests just go to `/api/ocr`).
          This is the correct setting when frontend and backend are
          deployed together as Vercel Services behind one domain with a
          `/api/*` rewrite - do not treat "" as "unset".
     2. If that value is missing entirely, or is still the literal
        unreplaced "%VITE_API_BASE_URL%" placeholder (i.e. the page was
        opened without going through Vite, such as a plain static file
        server, or VITE_API_BASE_URL simply wasn't set), fall back based on
        where the page itself is actually running:
        - localhost/127.0.0.1 -> http://localhost:8000 (local dev default,
          matches the separately-run backend on that port).
        - anywhere else (any deployed domain) -> same-origin ("").
          This makes the Vercel-Services same-domain deployment work even
          if VITE_API_BASE_URL is never explicitly set to "" in the
          dashboard (some env var UIs don't accept an empty value), and
          fails in an easy-to-diagnose way (a clean 404 on /api/ocr) rather
          than a deployed page silently trying to reach a viewer's own
          localhost if the separate-backend URL was simply forgotten. */
(function (global) {
  "use strict";

  var REQUEST_TIMEOUT_MS = 120000; // PaddleOCR on a CPU-only host can legitimately take 40-90s.
  var ALLOWED_TYPES = ["image/jpeg", "image/jpg", "image/png", "image/webp"];
  var ALLOWED_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"];

  function defaultBaseUrlForCurrentHost() {
    var hostname = (global.location && global.location.hostname) || "";
    var isLocalhost = hostname === "localhost" || hostname === "127.0.0.1";
    return isLocalhost ? "http://localhost:8000" : "";
  }

  function resolveBaseUrl() {
    var injected = global.__LL_API_BASE_URL__;
    if (typeof injected !== "string") return defaultBaseUrlForCurrentHost();
    var looksUnreplaced = injected.indexOf("%") === 0 || injected.indexOf("VITE_") !== -1;
    if (looksUnreplaced) return defaultBaseUrlForCurrentHost();
    return injected === "" ? "" : injected.replace(/\/+$/, "");
  }

  function getExtension(filename) {
    var idx = filename.lastIndexOf(".");
    return idx === -1 ? "" : filename.slice(idx).toLowerCase();
  }

  function validateFile(file) {
    if (!file) return "No file selected.";
    var ext = getExtension(file.name || "");
    var typeOk = !file.type || ALLOWED_TYPES.indexOf(file.type.toLowerCase()) !== -1;
    var extOk = ALLOWED_EXTENSIONS.indexOf(ext) !== -1;
    if (!typeOk && !extOk) {
      return "Unsupported file type. Please upload a JPG, JPEG, PNG, or WEBP image.";
    }
    return null;
  }

  function errorPayload(image, code, message) {
    return { success: false, image: image, error: { code: code, message: message } };
  }

  function runOcr(file, baseUrlOverride) {
    var baseUrl = baseUrlOverride || resolveBaseUrl();
    var url = baseUrl + "/api/ocr";
    var formData = new FormData();
    formData.append("file", file, file.name);

    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timedOut = false;
    var timer = controller
      ? setTimeout(function () {
          timedOut = true;
          controller.abort();
        }, REQUEST_TIMEOUT_MS)
      : null;

    return fetch(url, { method: "POST", body: formData, signal: controller ? controller.signal : undefined })
      .then(function (response) {
        if (timer) clearTimeout(timer);
        return response.json().then(function (data) {
          return { httpOk: response.ok, status: response.status, data: data };
        });
      })
      .catch(function (err) {
        if (timer) clearTimeout(timer);
        if (timedOut) {
          return {
            httpOk: false, status: 0,
            data: errorPayload(file.name, "timeout", "The analysis server took too long to respond. It may be under heavy load - please try again."),
          };
        }
        return {
          httpOk: false, status: 0,
          data: errorPayload(
            file.name, "backend_unavailable",
            "Unable to reach the LegalLense analysis server" + (baseUrl ? " at " + baseUrl : "") + ". Please confirm it is running and try again."
          ),
        };
      });
  }

  global.LLOcr = {
    runOcr: runOcr,
    validateFile: validateFile,
    resolveBaseUrl: resolveBaseUrl,
  };
})(window);
