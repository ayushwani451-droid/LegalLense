/* LegalLense shared client-side app layer.
   No backend exists yet, so this module provides a mock/local data + auth
   layer (localStorage for data, sessionStorage for the logged-in session)
   that every page in the portal links against. Keeps IDs consistent across
   Dashboard -> History -> Report -> Case -> Action and
   Dashboard -> Self-Check -> Self-Check Report -> Revision History. */
(function (global) {
  "use strict";

  var DB_KEY = "legallense_db_v1";
  var SESSION_KEY = "legallense_session_v1";

  // ---------- Utilities ----------

  function nowISO() {
    return new Date().toISOString();
  }

  function fmtDateTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    var day = d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
    var time = d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: true });
    return day + ", " + time;
  }

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  }

  function qs(name) {
    return new URLSearchParams(global.location.search).get(name);
  }

  function escapeHtml(str) {
    return String(str == null ? "" : str).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function uid(prefix) {
    return prefix + "-" + Date.now().toString(36).toUpperCase() + Math.floor(Math.random() * 900 + 100);
  }

  function rand(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
  }

  // ---------- Toast (used for intentionally-unimplemented actions) ----------

  function toast(message) {
    var el = document.createElement("div");
    el.textContent = message;
    el.setAttribute("style", [
      "position:fixed", "left:50%", "bottom:32px", "transform:translateX(-50%)",
      "background:#191c1d", "color:#fff", "padding:10px 20px", "border-radius:8px",
      "font-family:Inter,sans-serif", "font-size:14px", "box-shadow:0 4px 14px rgba(0,0,0,0.25)",
      "z-index:99999", "opacity:0", "transition:opacity .2s ease"
    ].join(";"));
    document.body.appendChild(el);
    requestAnimationFrame(function () { el.style.opacity = "1"; });
    setTimeout(function () {
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 250);
    }, 2200);
  }

  function comingSoon(e, label) {
    if (e && e.preventDefault) e.preventDefault();
    toast((label || "This") + " isn't available in this preview yet.");
    return false;
  }

  // ---------- Seed data ----------

  function seedDb() {
    return {
      scans: [
        {
          id: "SCN-LM2024-089",
          productName: "Detergent Label Batch 88A",
          category: "Consumer Packaged Goods",
          manufacturer: "Hindustan Unilever Ltd.",
          date: "2023-10-24T14:30:00",
          status: "flagged",
          score: 60,
          caseId: "CASE-LM2024-089",
          findings: [
            { field: "Manufacturer Name", value: "Hindustan Unilever Ltd.", status: "compliant" },
            { field: "Net Quantity", value: "Not confidently detected", status: "missing", note: "Rule 6 violation" },
            { field: "MRP (Incl. of all taxes)", value: "₹ 145.00", status: "compliant" },
            { field: "Mfg Date", value: "08/2023?", status: "low_confidence" },
            { field: "Consumer Care Details", value: "1800-10-22-221, lever.care@unilever.com", status: "compliant" }
          ]
        },
        {
          id: "SCN-2023-8901", productName: "CardioMon Pro XR", category: "Medical Devices",
          manufacturer: "MedTech Industries Ltd.", date: "2023-10-24T09:15:00", status: "compliant", score: 96, caseId: null,
          findings: [
            { field: "Manufacturer Name", value: "MedTech Industries Ltd.", status: "compliant" },
            { field: "Net Quantity", value: "1 Unit", status: "compliant" },
            { field: "MRP (Incl. of all taxes)", value: "₹ 12,450.00", status: "compliant" },
            { field: "Mfg Date", value: "03/2023", status: "compliant" },
            { field: "Consumer Care Details", value: "1800-209-4030, care@medtech.in", status: "compliant" }
          ]
        },
        {
          id: "SCN-2023-8895", productName: "AeroPure Air Filter V2", category: "Consumer Goods",
          manufacturer: "CleanTech Solutions Corp.", date: "2023-10-22T09:15:00", status: "flagged", score: 58,
          caseId: "CASE-2023-8895",
          findings: [
            { field: "Manufacturer Name", value: "CleanTech Solutions Corp.", status: "compliant" },
            { field: "Net Quantity", value: "Not confidently detected", status: "missing", note: "Rule 6 violation" },
            { field: "MRP (Incl. of all taxes)", value: "₹ 899.00", status: "compliant" },
            { field: "Mfg Date", value: "Illegible", status: "missing" },
            { field: "Consumer Care Details", value: "support@cleantech.co.in", status: "compliant" }
          ]
        },
        {
          id: "SCN-2023-8890", productName: "VitaBoost Multivitamins", category: "Pharmaceuticals",
          manufacturer: "HealthNutra Corp (India)", date: "2023-10-20T16:45:00", status: "pending", score: null, caseId: null,
          findings: [
            { field: "Manufacturer Name", value: "HealthNutra Corp (India)", status: "compliant" },
            { field: "Net Quantity", value: "60 Tablets", status: "compliant" },
            { field: "MRP (Incl. of all taxes)", value: "₹ 399.00", status: "compliant" },
            { field: "Mfg Date", value: "Pending manual verification", status: "low_confidence" },
            { field: "Consumer Care Details", value: "Under review", status: "low_confidence" }
          ]
        },
        {
          id: "SCN-2023-8872", productName: "OptiVision Laser Scanner", category: "Industrial Tech",
          manufacturer: "Precision Instruments LLC", date: "2023-10-18T11:00:00", status: "compliant", score: 98, caseId: null,
          findings: [
            { field: "Manufacturer Name", value: "Precision Instruments LLC", status: "compliant" },
            { field: "Net Quantity", value: "1 Unit", status: "compliant" },
            { field: "MRP (Incl. of all taxes)", value: "₹ 45,000.00", status: "compliant" },
            { field: "Mfg Date", value: "06/2023", status: "compliant" },
            { field: "Consumer Care Details", value: "1800-419-2020, support@precision.com", status: "compliant" }
          ]
        }
      ],
      cases: [
        {
          id: "CASE-LM2024-089", scanId: "SCN-LM2024-089", entity: "Hindustan Unilever Ltd.",
          violationType: "Section 14(a) - Missing Net Quantity", confidence: 94, status: "under_review",
          timeline: [
            { actor: "System", title: "Scan uploaded", desc: "Image data ingested from batch scanning utility at regional sorting center.", date: "2023-10-20T08:02:00" },
            { actor: "Inspector Rajesh Kumar", title: "Logged as Violation", desc: "Initial AI flag confirmed. Violation ticket formally opened for investigation.", date: "2023-10-20T11:45:00" },
            { actor: "System", title: "Manufacturer notified", desc: "Automated notice issued via system gateway (Ref: NT-9921).", date: "2023-10-21T14:30:00" },
            { actor: "Manufacturer Portal Rep", title: "Manufacturer uploaded revised label", desc: "Awaiting official review of the submitted remedial documentation.", date: "2023-10-22T09:15:00", attachment: "revised_label_v2.pdf" }
          ]
        },
        {
          id: "CASE-2023-8895", scanId: "SCN-2023-8895", entity: "CleanTech Solutions Corp.",
          violationType: "Section 14(a) - Missing Net Quantity", confidence: 88, status: "open",
          timeline: [
            { actor: "System", title: "Scan uploaded", desc: "Image data ingested from field inspection upload.", date: "2023-10-22T09:15:00" },
            { actor: "Inspector Rajesh Kumar", title: "Logged as Violation", desc: "Initial AI flag confirmed. Violation ticket formally opened for investigation.", date: "2023-10-22T10:05:00" }
          ]
        }
      ],
      products: [
        { id: "PROD-SMARTHUB", name: "Acme SmartHub Pro", sku: "ACME-SH-001", category: "Electronics" },
        { id: "PROD-PUREAIR", name: "PureAir Purifier V2", sku: "ACME-PA-002", category: "Appliances" },
        { id: "PROD-ECOPACK", name: "EcoPack Bio-Container", sku: "ACME-EP-105", category: "Packaging" },
        { id: "PROD-DETERGENT", name: "Detergent Pro", sku: "CTS-DET-001", category: "Home Care" }
      ],
      selfChecks: [
        {
          id: "SC-SMARTHUB-1", productId: "PROD-SMARTHUB", productName: "Acme SmartHub Pro", category: "Electronics",
          version: 1, date: "2023-10-24T00:00:00", score: 96, status: "compliant",
          findings: [
            { field: "Net Quantity", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." },
            { field: "Manufacturer Address", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." },
            { field: "MRP Format", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." }
          ]
        },
        {
          id: "SC-ECOPACK-1", productId: "PROD-ECOPACK", productName: "EcoPack Bio-Container", category: "Packaging",
          version: 1, date: "2023-10-23T00:00:00", score: 94, status: "compliant",
          findings: [
            { field: "Net Quantity", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." },
            { field: "Manufacturer Address", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." },
            { field: "MRP Format", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." }
          ]
        },
        {
          id: "SC-1001", productId: "PROD-DETERGENT", productName: "Detergent Pro", category: "Home Care",
          version: 1, date: "2023-10-20T00:00:00", score: 60, status: "needs_fixes",
          findings: [
            { field: "Net Quantity", status: "warning", note: "Missing Net Weight declaration on the principal display panel." },
            { field: "Manufacturer Address", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." },
            { field: "MRP Format", status: "warning", note: "Ensure the phrase \"Inclusive of all taxes\" is clearly legible alongside the price." }
          ]
        },
        {
          id: "SC-1002", productId: "PROD-DETERGENT", productName: "Detergent Pro", category: "Home Care",
          version: 2, date: "2023-10-22T00:00:00", score: 85, status: "needs_fixes",
          findings: [
            { field: "Net Quantity", status: "warning", note: "Increase font size to at least 4mm for this package size to meet legibility requirements." },
            { field: "Manufacturer Address", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." },
            { field: "MRP Format", status: "warning", note: "Ensure the phrase \"Inclusive of all taxes\" is clearly legible alongside the price." }
          ]
        },
        {
          id: "SC-1003", productId: "PROD-DETERGENT", productName: "Detergent Pro", category: "Home Care",
          version: 3, date: "2023-10-24T00:00:00", score: 100, status: "compliant",
          findings: [
            { field: "Net Quantity", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." },
            { field: "Manufacturer Address", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." },
            { field: "MRP Format", status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." }
          ]
        }
      ]
    };
  }

  function loadDb() {
    var raw = localStorage.getItem(DB_KEY);
    if (!raw) {
      var seeded = seedDb();
      localStorage.setItem(DB_KEY, JSON.stringify(seeded));
      return seeded;
    }
    try {
      return JSON.parse(raw);
    } catch (e) {
      var seeded2 = seedDb();
      localStorage.setItem(DB_KEY, JSON.stringify(seeded2));
      return seeded2;
    }
  }

  function saveDb(db) {
    localStorage.setItem(DB_KEY, JSON.stringify(db));
  }

  function resetDb() {
    localStorage.removeItem(DB_KEY);
    return loadDb();
  }

  // ---------- Session / Auth ----------

  function getSession() {
    try {
      return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    } catch (e) {
      return null;
    }
  }

  function login(role, identifier) {
    var session = {
      role: role,
      name: role === "official" ? "Inspector Rajesh Kumar" : (identifier || "Acme Consumer Goods"),
      org: role === "official" ? "Enforcement Division" : (identifier || "Manufacturer Portal"),
      loggedInAt: nowISO()
    };
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    return session;
  }

  function registerManufacturer(companyName) {
    return login("manufacturer", companyName);
  }

  function logout() {
    sessionStorage.removeItem(SESSION_KEY);
  }

  function requireRole(role) {
    var session = getSession();
    if (!session || session.role !== role) {
      var target = role === "official" ? "../login_legallense/code.html?role=official" : "../login_legallense/code.html?role=manufacturer";
      global.location.replace(target);
      return false;
    }
    return true;
  }

  // ---------- Scans ----------

  function getScans() {
    return loadDb().scans.slice().sort(function (a, b) { return new Date(b.date) - new Date(a.date); });
  }

  function getScan(id) {
    return loadDb().scans.find(function (s) { return s.id === id; }) || null;
  }

  function statusMeta(status) {
    switch (status) {
      case "compliant": return { label: "Compliant", icon: "check_circle" };
      case "flagged": return { label: "Action Required", icon: "error" };
      case "pending": return { label: "In Review", icon: "pending" };
      default: return { label: "Unknown", icon: "help" };
    }
  }

  function createScan(data) {
    var db = loadDb();
    var score = typeof data.score === "number" ? data.score : rand(55, 99);
    var status = score >= 90 ? "compliant" : (score >= 75 ? "pending" : "flagged");
    var pool = [
      { field: "Net Quantity", status: "missing", note: "Rule 6 violation", value: "Not confidently detected" },
      { field: "Mfg Date", status: "low_confidence", value: "Low confidence OCR read" },
      { field: "Consumer Care Details", status: "missing", value: "Not detected on label" },
      { field: "MRP (Incl. of all taxes)", status: "low_confidence", value: "Partially legible" }
    ];
    var findings = [
      { field: "Manufacturer Name", value: data.manufacturer || "Self-Declared Manufacturer", status: "compliant" }
    ];
    var issues = status === "compliant" ? 0 : (status === "pending" ? 1 : rand(1, 2));
    var shuffled = pool.slice().sort(function () { return Math.random() - 0.5; });
    for (var i = 0; i < pool.length; i++) {
      if (i < issues) findings.push(shuffled[i]);
      else findings.push({ field: shuffled[i].field, value: "Detected, meets requirements", status: "compliant" });
    }
    var scan = {
      id: uid("SCN"),
      productName: data.productName || "Untitled Scan",
      category: data.category || "General",
      manufacturer: data.manufacturer || "Unspecified Manufacturer",
      date: nowISO(),
      status: status,
      score: score,
      caseId: null,
      fileName: data.fileName || null,
      findings: findings
    };
    if (status === "flagged") {
      var caseId = uid("CASE");
      scan.caseId = caseId;
      db.cases.push({
        id: caseId,
        scanId: scan.id,
        entity: scan.manufacturer,
        violationType: "Section 14(a) - " + findings.filter(function (f) { return f.status !== "compliant"; }).map(function (f) { return f.field; }).join(", "),
        confidence: rand(80, 97),
        status: "open",
        timeline: [
          { actor: "System", title: "Scan uploaded", desc: "Image data ingested from new compliance scan submission.", date: nowISO() },
          { actor: getSession() ? getSession().name : "Inspector", title: "Logged as Violation", desc: "Automated flag confirmed. Violation ticket formally opened for investigation.", date: nowISO() }
        ]
      });
    }
    db.scans.push(scan);
    saveDb(db);
    return scan;
  }

  // ---------- Cases ----------

  function getCases() {
    return loadDb().cases.slice();
  }

  function getCase(id) {
    return loadDb().cases.find(function (c) { return c.id === id; }) || null;
  }

  function addCaseAction(caseId, action) {
    var db = loadDb();
    var caseObj = db.cases.find(function (c) { return c.id === caseId; });
    if (!caseObj) return null;
    var session = getSession();
    var titles = { open: "Case reopened", under_review: "Case marked Under Review", escalated: "Case escalated", closed: "Case closed" };
    caseObj.status = action.status;
    caseObj.timeline.push({
      actor: session ? session.name : "Inspector",
      title: titles[action.status] || "Enforcement action logged",
      desc: action.notes || "",
      date: nowISO(),
      attachment: action.fileName || null
    });
    saveDb(db);
    return caseObj;
  }

  // ---------- Products & Self-Checks (Manufacturer) ----------

  function getProducts() {
    return loadDb().products.slice();
  }

  function getProduct(id) {
    return loadDb().products.find(function (p) { return p.id === id; }) || null;
  }

  function getSelfChecks() {
    return loadDb().selfChecks.slice();
  }

  function getSelfCheck(id) {
    return loadDb().selfChecks.find(function (s) { return s.id === id; }) || null;
  }

  function getSelfChecksByProduct(productId) {
    return loadDb().selfChecks
      .filter(function (s) { return s.productId === productId; })
      .sort(function (a, b) { return b.version - a.version; });
  }

  function latestSelfCheckForProduct(productId) {
    var all = getSelfChecksByProduct(productId);
    return all.length ? all[0] : null;
  }

  function createSelfCheck(data) {
    var db = loadDb();
    var productId = data.productId;
    if (!productId) {
      productId = uid("PROD");
      db.products.push({ id: productId, name: data.productName, sku: "SELF-" + productId, category: data.category || "General" });
    } else if (!db.products.find(function (p) { return p.id === productId; })) {
      db.products.push({ id: productId, name: data.productName, sku: "SELF-" + productId, category: data.category || "General" });
    }
    var prior = db.selfChecks.filter(function (s) { return s.productId === productId; }).sort(function (a, b) { return b.version - a.version; });
    var version = prior.length ? prior[0].version + 1 : 1;
    var priorScore = prior.length ? prior[0].score : rand(45, 75);
    var score = Math.min(100, priorScore + rand(8, 30));
    var pool = ["Net Quantity", "Manufacturer Address", "MRP Format", "Consumer Care Details", "Generic Name Legibility"];
    var issues = score >= 95 ? 0 : (score >= 80 ? 1 : 2);
    var findings = pool.slice(0, 3).map(function (field, idx) {
      if (idx < issues) {
        return { field: field, status: "warning", note: "Adjust formatting/legibility for " + field + " to meet current Legal Metrology guidelines." };
      }
      return { field: field, status: "compliant", note: "Fully compliant. Format and legibility meet current guidelines." };
    });
    var record = {
      id: uid("SC"),
      productId: productId,
      productName: data.productName || (getProduct(productId) || {}).name || "Untitled Product",
      category: data.category || "General",
      version: version,
      date: nowISO(),
      score: score,
      status: score === 100 ? "compliant" : "needs_fixes",
      fileName: data.fileName || null,
      findings: findings
    };
    db.selfChecks.push(record);
    saveDb(db);
    return record;
  }

  // ---------- CSV export (real browser download, not an artifact sandbox) ----------

  function exportCSV(filename, rows) {
    var csv = rows.map(function (row) {
      return row.map(function (cell) {
        var v = String(cell == null ? "" : cell).replace(/"/g, '""');
        return /[",\n]/.test(v) ? '"' + v + '"' : v;
      }).join(",");
    }).join("\r\n");
    var blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  global.LL = {
    // utils
    qs: qs, escapeHtml: escapeHtml, fmtDateTime: fmtDateTime, fmtDate: fmtDate, toast: toast, comingSoon: comingSoon,
    // auth
    getSession: getSession, login: login, registerManufacturer: registerManufacturer, logout: logout, requireRole: requireRole,
    // data
    getScans: getScans, getScan: getScan, createScan: createScan, statusMeta: statusMeta,
    getCases: getCases, getCase: getCase, addCaseAction: addCaseAction,
    getProducts: getProducts, getProduct: getProduct,
    getSelfChecks: getSelfChecks, getSelfCheck: getSelfCheck, getSelfChecksByProduct: getSelfChecksByProduct,
    latestSelfCheckForProduct: latestSelfCheckForProduct, createSelfCheck: createSelfCheck,
    exportCSV: exportCSV, resetDb: resetDb
  };
})(window);
