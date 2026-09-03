/* Thin fetch wrapper for the LegalLense OCR backend (FastAPI + PaddleOCR).
   Kept separate from app.js (the local mock-data/session layer) since this
   is the one module in the frontend that actually talks to a real server.

   Backend contract (async job pattern - see backend/api/ocr.py):
     POST /api/ocr                  -> 202 {"job_id": "...", "status": "pending"}
                                        (or a 4xx OCRResult-shaped error if the
                                        upload itself is rejected - bad/missing
                                        file, wrong type, too large)
     GET  /api/ocr/status/{job_id}  -> {"status": "pending"|"processing"|"done"|"error",
                                        "result": <OCRResult> | null, "error": string | null}
   runOcr() below hides all of this behind the SAME external contract the
   old synchronous version had (kept unchanged so
   new_compliance_scan_legallense/code.html needs no changes at all): it
   returns a promise that always RESOLVES with { httpOk, status, data },
   where `data` is either the final OCRResult (success or a well-formed
   OCRResult(success=false)) or an errorPayload() - never a rejection. The
   POST call's own network/timeout errors were already surfaced this way
   before this change; job-status "error" and the new polling timeout are
   now surfaced the same way, for consistency and so no `.catch()` needs to
   be added at any existing call site.

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

  // Guards only the initial POST /api/ocr call, which now just schedules the
  // job and responds in well under a second - this is generous on purpose
  // (a slow/loaded backend taking a few seconds to accept the upload should
  // not be treated as a failure), not a bound on OCR itself. The actual OCR
  // run is bounded by POLL_TIMEOUT_MS below instead.
  var REQUEST_TIMEOUT_MS = 120000;
  var POLL_INTERVAL_MS = 3000;
  var POLL_TIMEOUT_MS = 180000; // 3 minutes - PaddleOCR on a CPU-only host can legitimately take 40-90s, this leaves headroom.
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

  function delay(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  // Polls GET /api/ocr/status/{job_id} every POLL_INTERVAL_MS until the job
  // reaches "done" or "error", or until `deadline` (a Date.now()-comparable
  // timestamp) passes. Always resolves - see the module docstring above for
  // why this never rejects.
  function pollStatus(baseUrl, jobId, fileName, deadline) {
    var statusUrl = baseUrl + "/api/ocr/status/" + encodeURIComponent(jobId);

    return fetch(statusUrl)
      .then(function (response) {
        return response.json().then(function (data) {
          return { httpOk: response.ok, status: response.status, data: data };
        });
      })
      .then(function (result) {
        if (!result.httpOk) {
          // e.g. a 404 "Job not found" - a permanent failure, not worth
          // retrying, unlike a transient network error (handled below).
          var detail = result.data && result.data.detail;
          return {
            httpOk: false, status: result.status,
            data: errorPayload(fileName, "status_check_failed", detail || "Failed to check the OCR job status."),
          };
        }

        var jobStatus = result.data.status;
        if (jobStatus === "done") {
          return { httpOk: true, status: result.status, data: result.data.result };
        }
        if (jobStatus === "error") {
          return {
            httpOk: false, status: result.status,
            data: errorPayload(fileName, "ocr_job_failed", result.data.error || "OCR processing failed."),
          };
        }
        // "pending" or "processing" - keep polling until the deadline.
        if (Date.now() >= deadline) {
          return {
            httpOk: false, status: 0,
            data: errorPayload(fileName, "timeout", "The analysis is taking longer than expected (over 3 minutes). Please try again later."),
          };
        }
        return delay(POLL_INTERVAL_MS).then(function () {
          return pollStatus(baseUrl, jobId, fileName, deadline);
        });
      })
      .catch(function () {
        // A network blip on a single poll shouldn't fail the whole job -
        // retry on the same schedule until the overall deadline.
        if (Date.now() >= deadline) {
          return {
            httpOk: false, status: 0,
            data: errorPayload(fileName, "timeout", "The analysis is taking longer than expected (over 3 minutes). Please try again later."),
          };
        }
        return delay(POLL_INTERVAL_MS).then(function () {
          return pollStatus(baseUrl, jobId, fileName, deadline);
        });
      });
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
      .then(function (result) {
        // A non-ok response, or an ok response with no job_id, means the
        // upload itself was rejected (bad file, missing file, too large,
        // etc.) - that error is already shaped exactly like the final
        // OCRResult error shape (see backend/api/ocr.py's
        // _error_response()), so it's returned to the caller unchanged,
        // exactly as it was before the async job pattern existed.
        if (!result.httpOk || !result.data || !result.data.job_id) {
          return result;
        }
        var deadline = Date.now() + POLL_TIMEOUT_MS;
        // Wait one interval before the first status check - the job was
        // just created and is guaranteed to still be pending/processing.
        return delay(POLL_INTERVAL_MS).then(function () {
          return pollStatus(baseUrl, result.data.job_id, file.name, deadline);
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
