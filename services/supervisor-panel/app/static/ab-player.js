// CallShield özel ses çaları.
//
// Kullanım:
//   <div class="ab-player" data-audio-src="/recordings/1/audio" data-duration="12.4"></div>
//
// Neden özel: yayınlanan WAV'larda yerli <audio controls> metadata gelene
// kadar "00:00" gösteriyordu. Bu çalar sunucudan gelen süreyi anında basar,
// sesi ilk etkileşimde yükler ve tüm panelde tek görünüm sağlar.
(function () {
  "use strict";

  var players = [];

  function fmt(seconds) {
    if (!isFinite(seconds) || seconds < 0) return "--:--";
    var s = Math.round(seconds);
    var m = Math.floor(s / 60);
    return m + ":" + String(s % 60).padStart(2, "0");
  }

  function pauseOthers(current) {
    players.forEach(function (p) {
      if (p !== current && p.audio && !p.audio.paused) p.audio.pause();
    });
  }

  function build(el) {
    if (el.dataset.abPlayerReady === "1") return;
    el.dataset.abPlayerReady = "1";

    var src = el.dataset.audioSrc;
    var serverDuration = parseFloat(el.dataset.duration);
    if (!src) return;

    el.innerHTML =
      '<button class="ab-player-btn" type="button" aria-label="Oynat">' +
      '<i class="fa-solid fa-play"></i></button>' +
      '<div class="ab-player-track" role="slider" aria-label="Konum">' +
      '<div class="ab-player-fill"></div>' +
      '</div>' +
      '<span class="ab-player-time">0:00</span>' +
      '<span class="ab-player-sep">/</span>' +
      '<span class="ab-player-total">' + fmt(serverDuration) + "</span>";

    var state = {
      el: el,
      audio: null,
      duration: isFinite(serverDuration) && serverDuration > 0 ? serverDuration : NaN,
      pendingSeek: null,
    };
    players.push(state);

    var btn = el.querySelector(".ab-player-btn");
    var icon = btn.querySelector("i");
    var track = el.querySelector(".ab-player-track");
    var fill = el.querySelector(".ab-player-fill");
    var timeEl = el.querySelector(".ab-player-time");
    var totalEl = el.querySelector(".ab-player-total");

    function setIcon(playing) {
      icon.className = playing ? "fa-solid fa-pause" : "fa-solid fa-play";
      btn.setAttribute("aria-label", playing ? "Duraklat" : "Oynat");
      el.dataset.playing = playing ? "1" : "0";
    }

    function render() {
      var a = state.audio;
      var current = a ? a.currentTime : 0;
      timeEl.textContent = fmt(current);
      if (isFinite(state.duration) && state.duration > 0) {
        fill.style.width = Math.min(100, (current / state.duration) * 100) + "%";
      }
    }

    function ensureAudio() {
      if (state.audio) return state.audio;
      var a = new Audio();
      a.preload = "metadata";
      a.src = src;
      a.addEventListener("loadedmetadata", function () {
        if (isFinite(a.duration) && a.duration > 0) {
          state.duration = a.duration;
          totalEl.textContent = fmt(a.duration);
        }
        if (state.pendingSeek != null) {
          a.currentTime = state.pendingSeek * (state.duration || 0);
          state.pendingSeek = null;
        }
      });
      a.addEventListener("timeupdate", render);
      a.addEventListener("play", function () {
        pauseOthers(state);
        setIcon(true);
      });
      a.addEventListener("pause", function () { setIcon(false); });
      a.addEventListener("ended", function () {
        setIcon(false);
        a.currentTime = 0;
        render();
      });
      a.addEventListener("error", function () {
        timeEl.textContent = "hata";
        el.dataset.error = "1";
      });
      state.audio = a;
      return a;
    }

    btn.addEventListener("click", function () {
      var a = ensureAudio();
      if (a.paused) {
        a.play().catch(function () {});
      } else {
        a.pause();
      }
    });

    function seekTo(clientX) {
      var rect = track.getBoundingClientRect();
      var ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      var a = ensureAudio();
      fill.style.width = ratio * 100 + "%";
      if (isFinite(state.duration) && state.duration > 0 && a.readyState >= 1) {
        a.currentTime = ratio * state.duration;
        render();
      } else {
        // Metadata henüz yok — yüklenince uygula.
        state.pendingSeek = ratio;
        a.load();
      }
    }

    var dragging = false;
    track.addEventListener("pointerdown", function (e) {
      dragging = true;
      track.setPointerCapture(e.pointerId);
      seekTo(e.clientX);
    });
    track.addEventListener("pointermove", function (e) {
      if (dragging) seekTo(e.clientX);
    });
    track.addEventListener("pointerup", function () { dragging = false; });
    track.addEventListener("pointercancel", function () { dragging = false; });
  }

  function initAll(root) {
    (root || document).querySelectorAll(".ab-player").forEach(build);
  }

  document.addEventListener("DOMContentLoaded", function () { initAll(document); });
  document.addEventListener("htmx:afterSwap", function (event) { initAll(event.target); });

  window.abPlayer = { initAll: initAll };
})();
