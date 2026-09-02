import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const __dirname = dirname(fileURLToPath(import.meta.url));

// LegalLense is a true multi-page static site (each "route" is a literal
// HTML file, e.g. /official_dashboard_legallense/code.html), not an SPA.
// Every existing page is listed here as its own Rollup input so Vite
// preserves the exact directory structure and relative navigation
// (../other_page/code.html) that the app already relies on - no page
// content, styling, or routing behavior changes.
export default defineConfig({
  root: __dirname,
  base: "/",
  build: {
    rollupOptions: {
      input: {
        index: resolve(__dirname, "index.html"),
        legallense_home: resolve(__dirname, "legallense_home/code.html"),
        login: resolve(__dirname, "login_legallense/code.html"),
        registration_manufacturer: resolve(__dirname, "registration_manufacturer/code.html"),
        official_dashboard: resolve(__dirname, "official_dashboard_legallense/code.html"),
        new_compliance_scan: resolve(__dirname, "new_compliance_scan_legallense/code.html"),
        scan_history: resolve(__dirname, "scan_history_legallense/code.html"),
        compliance_report: resolve(__dirname, "compliance_report_legallense/code.html"),
        case_details: resolve(__dirname, "case_details_legallense/code.html"),
        log_case_action: resolve(__dirname, "log_case_action_legallense/code.html"),
        manufacturer_dashboard: resolve(__dirname, "manufacturer_dashboard_legallense/code.html"),
        new_self_check: resolve(__dirname, "new_self_check_legallense/code.html"),
        self_check_report: resolve(__dirname, "self_check_report_legallense/code.html"),
        revision_history: resolve(__dirname, "revision_history_legallense/code.html"),
        compliance_summary: resolve(__dirname, "compliance_summary_legallense/code.html"),
      },
    },
  },
});
