(function () {
  "use strict";

  const root = document.querySelector("[data-toast-root]");
  if (!root) return;
  const confirmModal = document.querySelector("[data-confirm-modal]");
  const confirmTitle = confirmModal?.querySelector("[data-confirm-title]");
  const confirmMessage = confirmModal?.querySelector("[data-confirm-message]");
  const confirmSubmit = confirmModal?.querySelector("[data-confirm-submit]");
  const confirmIcon = confirmModal?.querySelector("[data-confirm-icon]");
  const confirmCancelButtons = confirmModal
    ? Array.from(confirmModal.querySelectorAll("[data-confirm-cancel]"))
    : [];
  let activeConfirmation = null;
  let previousFocus = null;

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
      <button class="toast-close" type="button" aria-label="Bildirimi kapat">
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

  function closeConfirmation(confirmed) {
    if (!confirmModal || confirmModal.hidden) return;
    const pending = activeConfirmation;
    activeConfirmation = null;
    confirmModal.dataset.state = "closing";
    document.body.classList.remove("modal-open");

    window.setTimeout(() => {
      confirmModal.hidden = true;
      confirmModal.dataset.state = "";
      if (previousFocus && typeof previousFocus.focus === "function") {
        previousFocus.focus();
      }
      previousFocus = null;
      if (confirmed && pending) pending.issueRequest(true);
    }, 140);
  }

  function openConfirmation(event) {
    if (!confirmModal || !confirmTitle || !confirmMessage || !confirmSubmit) {
      return false;
    }
    const detail = event.detail || {};
    const source = detail.elt;
    const question = String(detail.question || "").trim();
    if (!question || !source) return false;

    event.preventDefault();
    activeConfirmation = detail;
    previousFocus = document.activeElement;

    const kind = source.dataset.confirmKind === "danger" ? "danger" : "warning";
    confirmTitle.textContent = source.dataset.confirmTitle || "Confirm action";
    confirmMessage.textContent = question;
    confirmSubmit.textContent = source.dataset.confirmLabel || "Confirm";
    confirmSubmit.className = `btn ${kind === "danger" ? "btn-danger" : "btn-warning"}`;
    confirmIcon.dataset.kind = kind;

    confirmModal.hidden = false;
    confirmModal.dataset.state = "opening";
    document.body.classList.add("modal-open");
    window.requestAnimationFrame(() => {
      confirmModal.dataset.state = "open";
      confirmSubmit.focus();
    });
    return true;
  }

  document.addEventListener("DOMContentLoaded", showFlashToast);
  document.body.addEventListener("panel-toast", (event) => showToast(event.detail));

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-download-post]");
    if (!form) return;

    event.preventDefault();
    const submitButton = form.querySelector('button[type="submit"], input[type="submit"]');
    if (submitButton) submitButton.disabled = true;

    try {
      const response = await fetch(form.action, {
        method: form.method || "POST",
        body: new FormData(form),
        credentials: "same-origin",
      });

      if (!response.ok) {
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(String(payload.detail || payload.message || "").trim() || "İndirme başarısız");
        }
        const text = await response.text();
        throw new Error(text.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() || "İndirme başarısız");
      }

      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename=\"?([^\";]+)\"?/i);
      const filename = match ? match[1] : "download.bin";
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      showToast({ kind: "success", title: "Dışa aktarma hazır", message: `${filename} indirildi.` });
    } catch (error) {
      showToast({
        kind: "error",
        title: "Dışa aktarma başarısız",
        message: String(error && error.message ? error.message : error),
      });
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });

  document.body.addEventListener("htmx:afterSwap", (event) => {
    convertInlineAlerts(event.detail.target || event.target);
  });

  document.body.addEventListener("htmx:responseError", (event) => {
    const message = readErrorMessage(event.detail.xhr) || "The action could not be completed.";
    showToast({ kind: "error", message });
  });

  document.body.addEventListener("htmx:confirm", openConfirmation);
  confirmCancelButtons.forEach((button) => {
    button.addEventListener("click", () => closeConfirmation(false));
  });
  confirmSubmit?.addEventListener("click", () => closeConfirmation(true));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && confirmModal && !confirmModal.hidden) {
      event.preventDefault();
      closeConfirmation(false);
    }
  });

  // Voice launcher modal — session + customer name before starting a test session.
  const launcherModal = document.querySelector("[data-launcher-modal]");
  if (launcherModal) {
    let launcherPrevFocus = null;
    const openLauncher = () => {
      launcherPrevFocus = document.activeElement;
      launcherModal.hidden = false;
      launcherModal.dataset.state = "opening";
      document.body.classList.add("modal-open");
      window.requestAnimationFrame(() => {
        launcherModal.dataset.state = "open";
        launcherModal.querySelector("input[name='customer_name']")?.focus();
      });
    };
    const closeLauncher = () => {
      if (launcherModal.hidden) return;
      launcherModal.dataset.state = "closing";
      document.body.classList.remove("modal-open");
      window.setTimeout(() => {
        launcherModal.hidden = true;
        launcherModal.dataset.state = "";
        if (launcherPrevFocus && typeof launcherPrevFocus.focus === "function") {
          launcherPrevFocus.focus();
        }
        launcherPrevFocus = null;
      }, 140);
    };
    document.querySelectorAll("[data-open-launcher]").forEach((button) => {
      button.addEventListener("click", openLauncher);
    });
    launcherModal.querySelectorAll("[data-launcher-cancel]").forEach((button) => {
      button.addEventListener("click", closeLauncher);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !launcherModal.hidden) {
        event.preventDefault();
        closeLauncher();
      }
    });
  }

  window.agentUI = Object.assign(window.agentUI || {}, {
    showToast,
    convertInlineAlerts,
    closeConfirmation,
  });
})();
