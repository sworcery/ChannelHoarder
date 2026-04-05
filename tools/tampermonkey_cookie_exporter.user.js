// ==UserScript==
// @name         ChannelHoarder Cookie Exporter
// @namespace    https://github.com/channelhoarder
// @version      1.2.1
// @description  Exports YouTube cookies to your ChannelHoarder server on page load/refresh
// @author       ChannelHoarder
// @match        https://www.youtube.com/*
// @match        https://youtube.com/*
// @match        https://m.youtube.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @grant        GM_notification
// @connect      *
// @run-at       document-idle
// @downloadURL  __DOWNLOAD_URL__
// @updateURL    __UPDATE_URL__
// ==/UserScript==

(function () {
  "use strict";

  // ── Configuration ──────────────────────────────────────────────────
  // When installed from ChannelHoarder, SERVER_URL is pre-filled.
  // You can override it via the Tampermonkey menu: "Configure Server URL"
  const PRECONFIGURED_SERVER_URL = "";
  const EXPORT_COOLDOWN_MS = 5 * 60 * 1000; // 5-minute cooldown between exports
  const API_PATH = "/api/v1/auth/cookies/push";

  // ── Server URL management ──────────────────────────────────────────

  function getServerUrl() {
    const stored = GM_getValue("server_url", "");
    return stored || PRECONFIGURED_SERVER_URL;
  }

  function setServerUrl(url) {
    GM_setValue("server_url", url.replace(/\/+$/, ""));
  }

  function promptServerUrl() {
    const current = getServerUrl();
    const url = prompt(
      "Enter your ChannelHoarder server URL (e.g., http://your-server:8587):",
      current || ""
    );
    if (url && url.trim()) {
      setServerUrl(url.trim());
      showToast("Server URL saved: " + url.trim(), "success");
      return url.trim();
    }
    return current;
  }

  // ── Toast notification ─────────────────────────────────────────────

  function showToast(message, type) {
    const colors = {
      success: { bg: "#166534", border: "#22c55e" },
      error: { bg: "#991b1b", border: "#ef4444" },
      info: { bg: "#1e40af", border: "#3b82f6" },
    };
    const c = colors[type] || colors.info;

    const toast = document.createElement("div");
    toast.textContent = message;
    Object.assign(toast.style, {
      position: "fixed",
      bottom: "20px",
      right: "20px",
      padding: "10px 16px",
      backgroundColor: c.bg,
      color: "#fff",
      borderLeft: `4px solid ${c.border}`,
      borderRadius: "6px",
      fontSize: "13px",
      fontFamily: "system-ui, sans-serif",
      zIndex: "99999",
      boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
      opacity: "0",
      transition: "opacity 0.3s ease",
      maxWidth: "350px",
    });

    document.body.appendChild(toast);
    requestAnimationFrame(() => (toast.style.opacity = "1"));
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  // ── Cookie extraction ──────────────────────────────────────────────

  function extractCookies() {
    const raw = document.cookie;
    if (!raw) return null;

    const lines = ["# Netscape HTTP Cookie File", "# Exported by ChannelHoarder Tampermonkey Script", ""];
    const hostname = window.location.hostname;
    const domain = hostname.startsWith("www.") ? hostname.slice(3) : "." + hostname;

    const pairs = raw.split(";");
    let count = 0;

    for (const pair of pairs) {
      const eq = pair.indexOf("=");
      if (eq < 0) continue;
      const name = pair.substring(0, eq).trim();
      const value = pair.substring(eq + 1).trim();
      if (!name) continue;

      // document.cookie doesn't expose domain/path/secure/expiry per cookie,
      // so we use sensible defaults for YouTube
      const cookieDomain = domain;
      const subdomainFlag = cookieDomain.startsWith(".") ? "TRUE" : "FALSE";
      const path = "/";
      const secure = window.location.protocol === "https:" ? "TRUE" : "FALSE";
      // Set expiry 1 year from now (cookies from document.cookie don't expose expiry)
      const expiry = Math.floor(Date.now() / 1000) + 365 * 24 * 60 * 60;

      lines.push(`${cookieDomain}\t${subdomainFlag}\t${path}\t${secure}\t${expiry}\t${name}\t${value}`);
      count++;
    }

    if (count === 0) return null;
    return lines.join("\n") + "\n";
  }

  // ── Push cookies to server ─────────────────────────────────────────

  function pushCookies(cookiesTxt, manual) {
    const serverUrl = getServerUrl();
    if (!serverUrl) {
      if (manual) {
        const url = promptServerUrl();
        if (!url) return;
        pushCookies(cookiesTxt, manual);
      }
      return;
    }

    GM_xmlhttpRequest({
      method: "POST",
      url: serverUrl + API_PATH,
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify({ cookies_txt: cookiesTxt }),
      timeout: 15000,
      onload: function (response) {
        if (response.status >= 200 && response.status < 300) {
          GM_setValue("last_export", Date.now());
          if (manual) {
            showToast("Cookies exported to ChannelHoarder", "success");
          }
          console.log("[ChannelHoarder] Cookies exported successfully");
        } else {
          console.error("[ChannelHoarder] Export failed:", response.status, response.responseText);
          if (manual) {
            showToast("Export failed: " + (response.statusText || response.status), "error");
          }
        }
      },
      onerror: function (err) {
        console.error("[ChannelHoarder] Export error:", err);
        if (manual) {
          showToast("Connection failed. Check server URL.", "error");
        }
      },
      ontimeout: function () {
        console.error("[ChannelHoarder] Export timed out");
        if (manual) {
          showToast("Request timed out. Check server URL.", "error");
        }
      },
    });
  }

  // ── Export logic ───────────────────────────────────────────────────

  function doExport(manual) {
    const cookies = extractCookies();
    if (!cookies) {
      if (manual) showToast("No cookies found on this page", "error");
      return;
    }
    pushCookies(cookies, manual);
  }

  function autoExport() {
    const serverUrl = getServerUrl();
    if (!serverUrl) return; // Not configured yet

    // Cooldown: avoid duplicate exports if the page reloads quickly
    const lastExport = GM_getValue("last_export", 0);
    const now = Date.now();
    if (now - lastExport < EXPORT_COOLDOWN_MS) {
      console.log("[ChannelHoarder] Skipping export (last export " +
        Math.round((now - lastExport) / 1000) + "s ago)");
      return;
    }

    doExport(false);
  }

  // ── Menu commands ──────────────────────────────────────────────────

  GM_registerMenuCommand("Export Cookies Now", () => doExport(true));
  GM_registerMenuCommand("Configure Server URL", promptServerUrl);
  GM_registerMenuCommand("View Export Status", () => {
    const lastExport = GM_getValue("last_export", 0);
    const serverUrl = getServerUrl();
    const msg = lastExport
      ? `Server: ${serverUrl || "(not set)"}\nLast export: ${new Date(lastExport).toLocaleString()}`
      : `Server: ${serverUrl || "(not set)"}\nNo exports yet`;
    alert(msg);
  });

  // ── Init ───────────────────────────────────────────────────────────

  // Only prompt for server URL on first run if not pre-configured
  if (!getServerUrl()) {
    setTimeout(() => {
      promptServerUrl();
    }, 2000);
  }

  // Auto-export on page load (rate-limited)
  setTimeout(autoExport, 3000);
})();
