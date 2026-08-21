/* Background + bulk recording upload.
 *
 * Intercepts the upload form: every selected file is queued and sent with XHR
 * (for upload progress events) while the page stays usable. Two uploads run
 * concurrently; each file gets a progress row. When every file has finished
 * and at least one succeeded, the page reloads so the table shows the new
 * rows (transcription continues server-side either way).
 */
(function () {
  "use strict";

  var CONCURRENCY = 2;

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  function fmtBytes(n) {
    if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(0) + " KB";
    return n + " B";
  }

  function createRow(list, file) {
    var row = document.createElement("div");
    row.className = "upload-row";
    row.innerHTML =
      '<div class="upload-row-head">' +
      '<span class="upload-name"></span>' +
      '<span class="upload-size text-muted"></span>' +
      '<span class="upload-state text-muted">sırada</span>' +
      "</div>" +
      '<div class="upload-bar"><div class="upload-bar-fill"></div></div>';
    row.querySelector(".upload-name").textContent = file.name;
    row.querySelector(".upload-size").textContent = fmtBytes(file.size);
    list.appendChild(row);
    return {
      setProgress: function (frac) {
        row.querySelector(".upload-bar-fill").style.width = Math.round(frac * 100) + "%";
        row.querySelector(".upload-state").textContent = "yükleniyor " + Math.round(frac * 100) + "%";
      },
      done: function (recordingId) {
        row.querySelector(".upload-bar-fill").style.width = "100%";
        row.classList.add("upload-ok");
        var state = row.querySelector(".upload-state");
        state.innerHTML = "";
        var link = document.createElement("a");
        link.href = "/recordings/" + recordingId;
        link.textContent = "yüklendi ✓ (#" + recordingId + ")";
        state.appendChild(link);
      },
      fail: function (message) {
        row.classList.add("upload-failed");
        var state = row.querySelector(".upload-state");
        state.textContent = "hata: " + (message || "yükleme başarısız");
      },
    };
  }

  function uploadOne(form, file, ui) {
    return new Promise(function (resolve) {
      var data = new FormData();
      var csrf = form.querySelector('input[name="_csrf"]');
      if (csrf) data.append("_csrf", csrf.value);
      data.append("kind", form.querySelector('[name="kind"]').value);
      data.append("notes", form.querySelector('[name="notes"]').value || "");
      data.append("file", file, file.name);

      var xhr = new XMLHttpRequest();
      xhr.open("POST", form.getAttribute("action"), true);
      xhr.setRequestHeader("X-Panel-Upload", "async");
      xhr.upload.onprogress = function (event) {
        if (event.lengthComputable) ui.setProgress(event.loaded / event.total);
      };
      xhr.onload = function () {
        var body = {};
        try { body = JSON.parse(xhr.responseText || "{}"); } catch (e) { /* not json */ }
        if (xhr.status >= 200 && xhr.status < 300 && body.id) {
          ui.done(body.id);
          resolve(true);
        } else {
          ui.fail(body.error || ("HTTP " + xhr.status));
          resolve(false);
        }
      };
      xhr.onerror = function () {
        ui.fail("ağ hatası");
        resolve(false);
      };
      xhr.send(data);
    });
  }

  ready(function () {
    var form = document.querySelector('form[action="/recordings/upload"]');
    if (!form) return;
    var input = form.querySelector('input[type="file"]');
    if (!input) return;
    input.setAttribute("multiple", "multiple");

    var list = document.createElement("div");
    list.className = "upload-progress-list";
    form.appendChild(list);

    form.addEventListener("submit", function (event) {
      var files = Array.prototype.slice.call(input.files || []);
      if (!files.length) return; // let native validation speak
      event.preventDefault();

      var button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;

      var queue = files.map(function (file) {
        return { file: file, ui: createRow(list, file) };
      });
      input.value = "";

      var okCount = 0;
      function next() {
        var item = queue.shift();
        if (!item) return Promise.resolve();
        return uploadOne(form, item.file, item.ui).then(function (ok) {
          if (ok) okCount += 1;
          return next();
        });
      }
      var workers = [];
      for (var i = 0; i < Math.min(CONCURRENCY, queue.length); i++) workers.push(next());

      Promise.all(workers).then(function () {
        if (button) button.disabled = false;
        if (okCount > 0) {
          var note = document.createElement("p");
          note.className = "text-muted";
          note.textContent = okCount + " kayıt yüklendi — transkripsiyon arka planda sürüyor. Liste 3 saniye içinde yenilenecek…";
          list.appendChild(note);
          window.setTimeout(function () { window.location.reload(); }, 3000);
        }
      });
    });
  });
})();
