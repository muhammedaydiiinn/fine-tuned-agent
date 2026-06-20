/**
 * datatable.js — Modüler DataTable bileşeni
 *
 * İki mod, otomatik seçim:
 *   table.dt-table          → client-side (mevcut server-render tbody'ler değişmez)
 *   table[data-dt-url]      → AJAX — kolonlar <th data-dt-col-field> ile deklaratif
 *
 * <th> attribute'ları:
 *   data-dt-col-field="fieldName"      columns[].data
 *   data-dt-col-render="rendererName"  kayıtlı renderer (bkz. RENDERERS)
 *   data-dt-col-orderable="false"      sıralama kapalı
 *   data-dt-col-class="td-mono"        className
 *   data-dt-col-width="60px"           genişlik
 *   data-dt-col-link-base="/path/"     link renderer'ı için base URL (hücre değeri eklenir)
 *
 * Not: data-render / data-orderable gibi DataTables'ın kendi HTML5 ayar
 * isimlerini kullanmayın. Kütüphane bu değerlerle JS kolon ayarlarını ezer.
 *
 * <table> attribute'ları (sadece AJAX mod):
 *   data-dt-url="..."              AJAX kaynak URL, dataSrc:'data'
 *   data-dt-order="0,desc"         başlangıç sıralaması (varsayılan: ilk kolon desc)
 *   data-dt-poll="4000"            ms cinsinden tablo yenileme aralığı
 *   data-dt-poll-field="status"    polling'in devam etmesi için izlenecek alan
 *   data-dt-poll-values="running,pending"  hangi değerlerde polling sürer
 *   data-dt-reload-on="/path"      bu HTMX path'ine POST sonrası ajax.reload
 */
(function () {
  "use strict";

  // ── Ortak config — TEK yerde ─────────────────────────────────────────────
  var COMMON = {
    pageLength: 10,
    layout: {
      topStart: "pageLength",
      topEnd: "search",
      bottomStart: "info",
      bottomEnd: "paging",
    },
    language: {
      search: "",
      searchPlaceholder: "Search…",
      lengthMenu: "Show _MENU_",
      info: "_START_–_END_ of _TOTAL_",
      infoEmpty: "No records",
      infoFiltered: "(filtered from _MAX_)",
      emptyTable: "No data",
      paginate: { previous: "‹", next: "›", first: "«", last: "»" },
    },
  };

  // ── Status → badge class map ──────────────────────────────────────────────
  var STATUS_CLS = {
    active: "badge-active",
    closed: "badge-closed",
    pending: "badge-pending",
    running: "badge-running",
    completed: "badge-approved",
    failed: "badge-error",
    low: "badge-risk-low",
    medium: "badge-risk-medium",
    high: "badge-risk-high",
    true: "badge-approved",
    false: "badge-rejected",
  };

  // ── HTML escaping ─────────────────────────────────────────────────────────
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── Renderer kayıt defteri ────────────────────────────────────────────────
  var RENDERERS = {
    /** <code> tag — sayısal/kısa metin için */
    code: function (d) {
      if (d == null || d === "" || d === "—")
        return '<span style="opacity:.4;">—</span>';
      return '<code style="font-size:.8rem;">' + esc(d) + "</code>";
    },

    /** status → renkli badge */
    badge: function (d, type) {
      if (type !== "display") return d;
      if (!d) return "";
      var cls = STATUS_CLS[String(d)] || "badge-info";
      return '<span class="badge ' + cls + '">' + esc(d) + "</span>";
    },

    /** status badge + progress bar (training_jobs için) */
    statusProgress: function (d, type, row) {
      if (type !== "display") return d;
      var cls = STATUS_CLS[String(d)] || "badge-pending";
      var pct = row.progress_pct || 0;
      return (
        '<div class="job-status-cell">' +
        '<span class="badge ' +
        cls +
        '">' +
        esc(d) +
        "</span>" +
        '<div class="progress-bar"><div class="progress-fill" style="width:' +
        pct +
        '%;"></div></div>' +
        "</div>"
      );
    },

    /** uzun metin — title ile kırpar */
    truncate: function (d, type) {
      if (type !== "display") return d;
      if (!d) return "";
      var s = String(d);
      if (s.length <= 80) return esc(s);
      return (
        '<span title="' + esc(s) + '">' + esc(s.substring(0, 80)) + "…</span>"
      );
    },

    /** tarih/saat */
    datetime: function (d, type) {
      if (type !== "display") return d;
      if (!d) return '<span style="opacity:.4;">—</span>';
      return (
        '<span class="td-mono" style="font-size:.8rem;opacity:.7;">' +
        esc(d) +
        "</span>"
      );
    },

    /**
     * training job çıktısı:
     *   completed → yeşil versiyon adı
     *   failed    → kırmızı hata özeti
     */
    jobOutput: function (d, type, row) {
      if (type !== "display") return "";
      if (d === "completed" && row.version_name) {
        return (
          '<span style="color:var(--green);font-size:.85rem;">' +
          '<i class="fa-solid fa-check"></i> ' +
          esc(row.version_name) +
          "</span>"
        );
      }
      if (d === "failed" && row.error_message) {
        var msg = String(row.error_message);
        return (
          '<span style="color:var(--red);font-size:.78rem;" title="' +
          esc(msg) +
          '">' +
          '<i class="fa-solid fa-triangle-exclamation"></i> ' +
          esc(msg.substring(0, 60)) +
          (msg.length > 60 ? "…" : "") +
          "</span>"
        );
      }
      return '<span style="opacity:.4;">—</span>';
    },

    /** evaluation quality score (0..1) */
    evalScore: function (d, type) {
      if (type !== "display") return d == null ? -1 : d;
      if (d == null || d === "") return '<span style="opacity:.4;">—</span>';
      var pct = Math.round(Number(d) * 100);
      var cls =
        pct >= 80
          ? "badge-approved"
          : pct >= 60
            ? "badge-pending"
            : "badge-error";
      return '<span class="badge ' + cls + '">' + pct + "%</span>";
    },

    /** sessions: external_session_id + live dot + link */
    sessionId: function (d, type, row) {
      if (type !== "display") return d;
      var dot =
        row.status === "active"
          ? '<span class="live-dot" style="margin-right:6px;"></span>'
          : "";
      return (
        '<div class="flex-center">' +
        dot +
        '<a href="/sessions/' +
        row.id +
        '" class="td-link td-mono">' +
        esc(d) +
        "</a>" +
        "</div>"
      );
    },

    /** hard_decline_count → badge */
    hardDecline: function (d, type) {
      if (type !== "display") return d;
      var n = parseInt(d, 10) || 0;
      if (n >= 2) return '<span class="badge badge-risk-high">' + n + "</span>";
      if (n === 1)
        return '<span class="badge badge-risk-medium">' + n + "</span>";
      return '<span class="text-muted">0</span>';
    },

    /** boolean → green check / dash */
    boolCheck: function (d, type) {
      if (type !== "display") return d;
      return d
        ? '<span class="text-success" style="font-size:13px;">&#10003;</span>'
        : '<span class="text-muted">—</span>';
    },

    /** corrections: link to session/turn detail */
    correctionLink: function (d, type, row) {
      if (type !== "display") return "";
      if (!row.session_id || !row.turn_id) return "";
      return (
        '<a href="/sessions/' +
        row.session_id +
        "/turns/" +
        row.turn_id +
        '" class="btn btn-sm btn-outline">' +
        'Turn <i class="fa-solid fa-arrow-right" style="font-size:10px;"></i></a>'
      );
    },

    /** training_candidates: approve / reject buttons (uses .dt-action-btn event delegation) */
    approveReject: function (d, type, row) {
      if (type !== "display") return "";
      var appDis = row.approved ? "disabled" : "";
      var rejDis = !row.approved ? "disabled" : "";
      return (
        '<div style="display:flex;gap:4px;">' +
        '<button class="btn btn-sm btn-success dt-action-btn" ' +
        'data-url="/training-candidates/' +
        row.id +
        '/approve" ' +
        appDis +
        ">Approve</button>" +
        '<button class="btn btn-sm btn-danger dt-action-btn" ' +
        'data-url="/training-candidates/' +
        row.id +
        '/reject" ' +
        rejDis +
        ">Reject</button>" +
        "</div>"
      );
    },

    /** approved boolean → "Approved" / "Rejected" badge */
    approvedBadge: function (d, type) {
      if (type !== "display") return d;
      return d
        ? '<span class="badge badge-approved">Approved</span>'
        : '<span class="badge badge-rejected">Rejected</span>';
    },

    /** session open button */
    sessionLink: function (d, type, row) {
      if (type !== "display") return "";
      return (
        '<a href="/sessions/' +
        row.id +
        '" class="btn btn-sm btn-outline">' +
        'Open <i class="fa-solid fa-arrow-right" style="font-size:10px;"></i></a>'
      );
    },

    // Yeni renderer eklemek için:
    //   RENDERERS.myRenderer = function(d, type, row) { ... };
    // ve <th data-dt-col-render="myRenderer"> ile kullan.
  };

  // ── Kolonu <th> attribute'larından üret ─────────────────────────────────
  function buildColumn(th) {
    // defaultContent, API satırında alan eksik/null olduğunda DataTables'ın
    // "Requested unknown parameter" uyarısı üretmesini engeller.
    var col = { defaultContent: "" };

    var field = th.getAttribute("data-dt-col-field");
    if (field) col.data = field;

    var renderName = th.getAttribute("data-dt-col-render");
    if (renderName === "link") {
      // link renderer: closure — base URL <th>'den alınır
      var base = th.getAttribute("data-dt-col-link-base") || "#";
      col.render = function (d, type) {
        if (type !== "display") return d;
        return (
          '<a href="' +
          esc(base) +
          esc(d) +
          '" class="td-link">' +
          esc(String(d)) +
          "</a>"
        );
      };
    } else if (renderName && RENDERERS[renderName]) {
      col.render = RENDERERS[renderName];
    }

    if (th.getAttribute("data-dt-col-orderable") === "false")
      col.orderable = false;
    var cls = th.getAttribute("data-dt-col-class");
    if (cls) col.className = cls;
    var w = th.getAttribute("data-dt-col-width");
    if (w) col.width = w;

    return col;
  }

  // ── "col,dir" → [[col, dir]] ─────────────────────────────────────────────
  function parseOrder(str) {
    if (!str) return [[0, "desc"]];
    var p = str.split(",");
    return [[parseInt(p[0], 10) || 0, (p[1] || "desc").trim()]];
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    // ── Client-side tabloları (mevcut pattern — değişmez) ─────────────────
    document.querySelectorAll("table.dt-table").forEach(function (table) {
      var cfg = Object.assign({}, COMMON, { order: [] });
      var dt = new DataTable(table, cfg);
      if (window.htmx) htmx.process(dt.table().container());
    });

    // ── AJAX tabloları ─────────────────────────────────────────────────────
    document.querySelectorAll("table[data-dt-url]").forEach(function (table) {
      var url = table.getAttribute("data-dt-url");
      var pollMs = parseInt(table.getAttribute("data-dt-poll") || "0", 10);
      var pollField = table.getAttribute("data-dt-poll-field") || "status";
      var pollVals = (
        table.getAttribute("data-dt-poll-values") || "running,pending"
      ).split(",");
      var reloadOn = table.getAttribute("data-dt-reload-on") || "";
      var orderStr = table.getAttribute("data-dt-order") || "0,desc";

      var ths = table.querySelectorAll("thead th");
      var columns = Array.prototype.map.call(ths, buildColumn);

      var cfg = Object.assign({}, COMMON, {
        ajax: {
          url: url,
          // Fonksiyon tabanlı dataSrc: string 'data' yerine explicit, güvenli
          dataSrc: function (json) {
            if (json && Array.isArray(json.data)) return json.data;
            console.warn("[dt] unexpected response from " + url, json);
            return [];
          },
          error: function (xhr, err) {
            console.error(
              "[dt] ajax error " + url + ":",
              err,
              xhr.status,
              xhr.responseText.slice(0, 300),
            );
          },
        },
        columns: columns,
        order: parseOrder(orderStr),
        drawCallback: function () {
          // HTMX'in yeni render edilen satırları işlemesi için
          if (window.htmx) htmx.process(this.api().table().node());
        },
      });

      var dt = new DataTable(table, cfg);
      table._dt = dt; // .dt-action-btn event delegation için

      // Auto-poll — aktif (running/pending) satır varken yeniler
      if (pollMs > 0) {
        var _timer = setInterval(function () {
          dt.ajax.reload(function (json) {
            var rows = json.data || [];
            var active = rows.some(function (r) {
              return pollVals.indexOf(String(r[pollField])) !== -1;
            });
            if (!active) clearInterval(_timer);
          }, false); // false = mevcut sayfa pozisyonunu koru
        }, pollMs);
      }

      // HTMX reload-on — belirtilen path'e istek sonrası tablo yenilenir
      if (reloadOn) {
        document.body.addEventListener("htmx:afterRequest", function (e) {
          var info = e.detail && e.detail.pathInfo;
          if (info && info.requestPath === reloadOn) {
            dt.ajax.reload(null, false);
          }
        });
      }
    });
  });

  // ── .dt-action-btn event delegation (approve/reject gibi aksiyonlar) ─────
  // Herhangi bir AJAX tablodaki buton tıklandığında POST atar, sonra tabloyu yeniler.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".dt-action-btn");
    if (!btn || btn.disabled) return;
    var url = btn.getAttribute("data-url");
    if (!url) return;

    btn.disabled = true;
    fetch(url, { method: "POST" })
      .then(function () {
        var tbl = btn.closest("table[data-dt-url]");
        if (tbl && tbl._dt) tbl._dt.ajax.reload(null, false);
      })
      .catch(function (err) {
        console.error("dt-action-btn error:", err);
        btn.disabled = false;
      });
  });

  // Dışarıya renderer kayıt defteri aç — sayfa kodu custom renderer ekleyebilir
  window.DT = { renderers: RENDERERS, statusCls: STATUS_CLS };
})();
