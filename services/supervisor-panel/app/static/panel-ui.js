(function () {
  "use strict";

  const root = document.querySelector("[data-toast-root]");
  if (!root) return;

  const ICONS = {
    success: "fa-solid fa-circle-check",
    error: "fa-solid fa-circle-exclamation",
    warning: "fa-solid fa-triangle-exclamation",
    info: "fa-solid fa-circle-info",
  };

  function normalizeToast(payload) {
    if (!payload) return null;
    if (typeof payload === "string") {
      return { kind: "info", message: payload };
    }
    if (typeof payload !== "object") return null;

    const message = String(payload.message || payload.detail || "").trim();
    if (!message) return null;

    return {
      kind: ["success", "error", "warning", "info"].includes(payload.kind) ? payload.kind : "info",
      title: payload.title ? String(payload.title).trim() : "",
      message,
      durationMs: Number.isFinite(payload.durationMs) ? payload.durationMs : 4200,
    };
  }

  function removeToast(node) {
    if (!node || node.dataset.state === "leaving") return;
    node.dataset.state = "leaving";
    window.setTimeout(() => node.remove(), 180);
  }

  function showToast(payload) {
    const toast = normalizeToast(payload);
    if (!toast) return;

    const element = document.createElement("article");
    element.className = `toast toast-${toast.kind}`;
    element.dataset.state = "entering";
    element.innerHTML = `
      <div class="toast-icon"><i class="${ICONS[toast.kind]}"></i></div>
      <div class="toast-body">
        ${toast.title ? `<div class="toast-title">${toast.title}</div>` : ""}
        <div class="toast-message"></div>
      </div>
      <button class="toast-close" type="button" aria-label="Dismiss notification">
        <i class="fa-solid fa-xmark"></i>
      </button>
    `;
    element.querySelector(".toast-message").textContent = toast.message;
    element.querySelector(".toast-close").addEventListener("click", () => removeToast(element));
    root.appendChild(element);

    window.requestAnimationFrame(() => {
      element.dataset.state = "visible";
    });

    window.setTimeout(() => removeToast(element), toast.durationMs);
  }

  function toastFromAlert(node) {
    const text = node.textContent.replace(/\s+/g, " ").trim();
    if (!text) return;

    let kind = "info";
    if (node.classList.contains("alert-success")) kind = "success";
    else if (node.classList.contains("alert-error")) kind = "error";
    else if (node.classList.contains("alert-warning")) kind = "warning";

    showToast({ kind, message: text });
  }

  function convertInlineAlerts(scope) {
    if (!scope || typeof scope.querySelectorAll !== "function") return;
    const alerts = [];
    if (scope.matches && scope.matches(".alert")) alerts.push(scope);
    alerts.push(...scope.querySelectorAll(".alert"));
    const refreshHost = scope.closest ? scope.closest("[data-refresh-on-toast]") || scope.querySelector("[data-refresh-on-toast]") : null;

    alerts.forEach((alertNode) => {
      toastFromAlert(alertNode);
      if (
        alertNode.dataset.toastOnly === "true" ||
        alertNode.closest("[data-toast-only='true']")
      ) {
        alertNode.remove();
      }
    });
    if (alerts.length && refreshHost && refreshHost.dataset.refreshOnToast) {
      document.body.dispatchEvent(new CustomEvent(refreshHost.dataset.refreshOnToast, { bubbles: true }));
    }
  }

  function showFlashToast() {
    const node = document.querySelector("#panel-flash-toast");
    if (!node) return;
    const raw = node.textContent.trim();
    if (!raw || raw === "null") return;
    try {
      showToast(JSON.parse(raw));
    } catch (_error) {
      // Ignore invalid flash payloads.
    }
  }

  function readErrorMessage(xhr) {
    if (!xhr) return "";
    const contentType = xhr.getResponseHeader("content-type") || "";
    if (contentType.includes("application/json")) {
      try {
        const payload = JSON.parse(xhr.responseText || "{}");
        return String(payload.detail || payload.message || "").trim();
      } catch (_error) {
        return "";
      }
    }

    const text = String(xhr.responseText || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    return text.slice(0, 220);
  }

  document.addEventListener("DOMContentLoaded", showFlashToast);
  document.body.addEventListener("panel-toast", (event) => showToast(event.detail));

  document.body.addEventListener("htmx:afterSwap", (event) => {
    convertInlineAlerts(event.detail.target || event.target);
  });

  document.body.addEventListener("htmx:responseError", (event) => {
    const message = readErrorMessage(event.detail.xhr) || "The action could not be completed.";
    showToast({ kind: "error", message });
  });

  window.anrufUI = Object.assign(window.anrufUI || {}, {
    showToast,
    convertInlineAlerts,
  });
})();
