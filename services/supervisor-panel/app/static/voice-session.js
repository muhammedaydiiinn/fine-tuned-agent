(function () {
  "use strict";

  const consoleElement = document.querySelector("#voice-test-console");
  if (!consoleElement || !window.LivekitClient) return;

  const { ConnectionState, Room, RoomEvent, Track } = window.LivekitClient;
  const sessionId = consoleElement.dataset.sessionId;
  const startButton = document.querySelector("#voice-start");
  const stopButton = document.querySelector("#voice-stop");
  const statusElement = document.querySelector("#voice-status");
  const orbElement = document.querySelector("#voice-orb");
  const levelElement = document.querySelector("#voice-level");
  const levelBars = Array.from(levelElement?.querySelectorAll("span") || []);
  const audioContainer = document.querySelector("#voice-audio");
  const endSessionForm = document.querySelector("#end-session-form");
  const stopAgentButton = document.querySelector("#live-stop-agent");
  const sendReplacementButton = document.querySelector("#live-send-replacement");
  const replacementField = document.querySelector("#quick-corrected-response");
  const replacementActionField = document.querySelector("#quick-corrected-action");
  const quickActionForm = document.querySelector(".supervisor-form");
  const recovery = window.VoiceSessionRecovery;

  let room = null;
  let audioContext = null;
  let levelFrame = null;
  let reconnectTimer = null;
  let manualDisconnect = false;
  let pageUnload = false;
  const storageKey = `voice_connected_${sessionId}`;
  let recoveryState = recovery
    ? recovery.readStorage(localStorage.getItem(storageKey))
    : { hasConnectedBefore: localStorage.getItem(storageKey) === "1", shouldResume: false, reconnectAttempts: 0 };
  let hasConnectedBefore = recoveryState.hasConnectedBefore;

  function persistRecoveryState() {
    if (!recovery) return;
    localStorage.setItem(storageKey, recovery.writeStorage(recoveryState));
  }

  function clearReconnectTimer() {
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function showToast(kind, message, title) {
    if (window.agentUI?.showToast) {
      window.agentUI.showToast({ kind, message, title });
    }
  }

  if (hasConnectedBefore) {
    startButton.innerHTML = '<i class="fa-solid fa-microphone"></i> Devam Et';
  }

  function setStatus(message, state) {
    statusElement.textContent = message;
    statusElement.dataset.state = state || "";
    orbElement.dataset.state = state || "idle";
  }

  function setVoiceState(state, detail) {
    const states = {
      idle: "Mikrofon kapalı",
      listening: "Dinliyor - konuşabilirsiniz",
      hearing: "Müşteri dinleniyor...",
      processing: "Son tur işleniyor...",
      speaking: "Ajan konuşuyor - araya girebilirsiniz",
      interrupted: "Araya girme algılandı - tur değişiyor",
      reconnecting: "Bağlantı koptu - yeniden bağlanılıyor...",
      error: detail || "Ses çalışma zamanı hatası",
    };
    const statusState = state === "listening" ? "ready" : state;
    setStatus(detail || states[state] || states.listening, statusState);
    orbElement.dataset.state = state;
  }

  function stopLevelMeter() {
    if (levelFrame) cancelAnimationFrame(levelFrame);
    levelFrame = null;
    levelElement.dataset.active = "false";
    levelBars.forEach((bar) => { bar.style.height = "3px"; });
    if (audioContext) audioContext.close().catch(() => {});
    audioContext = null;
  }

  function startLevelMeter(mediaTrack) {
    stopLevelMeter();
    if (!mediaTrack || !window.AudioContext) return;
    audioContext = new AudioContext();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.72;
    const source = audioContext.createMediaStreamSource(new MediaStream([mediaTrack]));
    source.connect(analyser);
    const samples = new Uint8Array(analyser.frequencyBinCount);
    levelElement.dataset.active = "true";
    const draw = () => {
      analyser.getByteFrequencyData(samples);
      const bucket = Math.max(1, Math.floor(samples.length / levelBars.length));
      levelBars.forEach((bar, index) => {
        let total = 0;
        const start = index * bucket;
        for (let offset = 0; offset < bucket; offset += 1) {
          total += samples[start + offset] || 0;
        }
        const level = total / bucket / 255;
        bar.style.height = `${Math.max(3, Math.round(level * 14))}px`;
      });
      levelFrame = requestAnimationFrame(draw);
    };
    draw();
  }

  function renderMetrics(metrics) {
    document.querySelectorAll("[data-voice-metric]").forEach((element) => {
      const value = metrics[element.dataset.voiceMetric];
      element.textContent = Number.isFinite(value) ? (Math.round(value) + " ms") : "-";
    });
  }

  function scheduleAutoResume() {
    if (!recovery || !recovery.shouldAutoResume(recoveryState)) return;
    clearReconnectTimer();
    const delayMs = recovery.nextRecoveryDelayMs(recoveryState.reconnectAttempts);
    setVoiceState("reconnecting", `Bağlantı koptu - ${Math.round(delayMs / 1000)}sn içinde yeniden denenecek`);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      startVoice({ forceResume: true, silentRecovery: true });
    }, delayMs);
  }

  function handleUnexpectedDisconnect() {
    if (!recovery || manualDisconnect || pageUnload || !hasConnectedBefore) {
      resetVoiceControls();
      return;
    }
    recoveryState = recovery.recordUnexpectedDisconnect(recoveryState);
    persistRecoveryState();
    resetVoiceControls(false);
    if (recovery.shouldAutoResume(recoveryState)) {
      showToast("warning", "Ses odası bağlantısı koptu. Otomatik kurtarma başlıyor.", "Ses kurtarma");
      scheduleAutoResume();
      return;
    }
    setVoiceState("error", "Ses kurtarma tükendi - elle devam edin");
    showToast("error", "Otomatik kurtarma deneme sınırına ulaştı. Elle devam edin.", "Ses kurtarma");
  }

  async function requestVoiceAction(actionName) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const form = new FormData();
    form.set("action", actionName);
    form.set("replacement_text", replacementField?.value || "");
    form.set("corrected_next_action", replacementActionField?.value || "");
    form.set("notes", actionName === "replace_answer" ? "Live supervisor replacement" : "Live supervisor stop");

    const response = await fetch(`/sessions/${sessionId}/voice-actions`, {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken, "Accept": "application/json" },
      body: form,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Voice action failed");
    }
    return payload;
  }

  async function startVoice(options) {
    const opts = options || {};

    const isLocalhost = location.hostname === "localhost" || location.hostname === "127.0.0.1";
    if (location.protocol !== "https:" && !isLocalhost) {
      const msg = "Mikrofon erişimi HTTPS gerektirir. Bu sayfayı https:// üzerinden açın ya da kaynağı Chrome'un güvensiz-kaynaklar listesine ekleyin (chrome://flags/#unsafely-treat-insecure-origin-as-secure).";
      setStatus(msg, "error");
      showToast("error", msg, "HTTPS gerekli");
      return;
    }

    clearReconnectTimer();
    manualDisconnect = false;
    startButton.disabled = true;
    setStatus("Ses çalışma zamanına bağlanılıyor...", "working");

    // Only the automatic silent-recovery path (forceResume) rejoins without
    // re-dispatching the agent — there the agent is still in the room after a brief
    // network blip. A user-initiated Start/Resume must always dispatch the agent,
    // otherwise reconnecting to a room the agent has already left (e.g. after a
    // worker restart) leaves no agent in the room and nothing is ever spoken.
    const isResume = opts.forceResume === true;
    const tokenPath = isResume
      ? `/sessions/${sessionId}/voice-token-resume`
      : `/sessions/${sessionId}/voice-token`;

    try {
      const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
      const response = await fetch(tokenPath, {
        method: "POST",
        headers: { "Accept": "application/json", "X-CSRF-Token": csrfToken },
      });
      const credentials = await response.json();
      if (!response.ok) {
        throw new Error(credentials.detail || "Ses jetonu oluşturulamadı");
      }

      const currentRoom = new Room({ adaptiveStream: true, dynacast: true });
      room = currentRoom;
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (room !== currentRoom) return;
        if (track.kind === Track.Kind.Audio) {
          audioContainer.appendChild(track.attach());
        }
      });
      room.on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
        if (room !== currentRoom) return;
        if (topic !== "voice.events") return;
        const event = JSON.parse(new TextDecoder().decode(payload));
        if (event.event === "voice_session_ready") {
          setVoiceState("listening");
        } else if (event.event === "speech_started") {
          setVoiceState("hearing");
        } else if (event.event === "speech_ended") {
          setVoiceState("processing");
        } else if (event.event === "partial_transcript") {
          setVoiceState("hearing", "Duyuluyor: " + (event.text || ""));
        } else if (event.event === "transcript_final") {
          setVoiceState("processing", "Anlaşılan: " + (event.text || ""));
        } else if (event.event === "agent_response") {
          setVoiceState("speaking");
        } else if (event.event === "agent_playback_started") {
          setVoiceState("speaking");
        } else if (event.event === "possible_barge_in") {
          setVoiceState("interrupted", "Olası araya girme — dinleniyor...");
        } else if (event.event === "interruption_detected") {
          setVoiceState("interrupted");
        } else if (event.event === "playback_cancelled") {
          setVoiceState("processing", "Ajan durdu - araya girmeniz işleniyor");
          showToast("warning", "Yeni müşteri konuşması algılandığı için aktif oynatma iptal edildi.", "Oynatma durdu");
        } else if (event.event === "backchannel_detected") {
          setVoiceState("listening", "Onay algılandı - devam ediliyor");
        } else if (event.event === "duplicate_transcript_ignored") {
          setVoiceState("listening", "Yinelenen ses yok sayıldı");
          showToast("info", "Konuşmayı kararlı tutmak için yinelenen bir transkript yok sayıldı.", "Yineleme yok sayıldı");
        } else if (event.event === "empty_transcript") {
          setVoiceState("listening", "Konuşma algılanmadı - hâlâ dinleniyor");
        } else if (event.event === "stt_unavailable") {
          setVoiceState("listening", "Konuşma tanıma kullanılamıyor - dinleniyor");
          showToast("warning", event.detail || "Konuşma tanıma geçici olarak kullanılamıyor.", "STT kullanılamıyor");
        } else if (event.event === "voice_error") {
          setVoiceState("error", event.detail);
          showToast("error", event.detail || "Ses çalışma zamanı bir hata bildirdi.", "Ses çalışma zamanı");
        } else if (event.event === "voice_turn_complete") {
          renderMetrics(event.metrics || {});
          setVoiceState("listening", "Dinliyor - sonraki tura hazır");
          if (window.htmx) {
            window.htmx.trigger(document.body, "voice-turn-complete");
          }
        } else if (event.event === "supervisor_stop_applied") {
          setVoiceState("processing", "Yönetici aktif cevabı durdurdu");
          showToast("warning", "Aktif ajan cevabı durduruldu.", "Yönetici kontrolü");
        } else if (event.event === "supervisor_replacement_started") {
          setVoiceState("speaking", "Yönetici cevabı oynatılıyor");
          showToast("info", "Değiştirilen cevap müşteriye oynatılıyor.", "Yönetici kontrolü");
        } else if (event.event === "supervisor_replacement_completed") {
          renderMetrics({ tts_first_audio_ms: event.tts_first_audio_ms });
          setVoiceState("listening", "Cevap iletildi - dinleniyor");
          showToast("success", "Değiştirilen cevap iletildi.", "Yönetici kontrolü");
        } else if (event.event === "supervisor_action_ignored") {
          showToast("warning", "Yönetici işlemi ses çalışma zamanı tarafından yok sayıldı.", "Yönetici kontrolü");
        } else if (event.event === "tts_fallback_activated") {
          showToast("warning", "Birincil TTS başarısız oldu, bu tur için yedek PCM kullanıldı.", "TTS yedeği");
        }
        if (window.htmx && !["speech_started", "speech_ended", "partial_transcript", "possible_barge_in", "agent_playback_started"].includes(event.event)) {
          window.htmx.trigger(document.body, "voice-event");
        }
      });
      room.on(RoomEvent.Reconnecting, () => {
        if (room !== currentRoom) return;
        setVoiceState("reconnecting");
      });
      room.on(RoomEvent.Reconnected, () => {
        if (room !== currentRoom) return;
        setVoiceState("listening", "Yeniden bağlandı - dinleniyor");
        showToast("success", "Canlı ses bağlantısı geri geldi.", "Yeniden bağlandı");
      });
      room.on(RoomEvent.Disconnected, () => {
        if (room !== currentRoom) return;
        handleUnexpectedDisconnect();
      });

      await room.connect(credentials.server_url, credentials.token);
      await room.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      const microphone = room.localParticipant.getTrackPublication(Track.Source.Microphone);
      startLevelMeter(microphone?.track?.mediaStreamTrack);
      recoveryState = recovery ? recovery.recordConnected() : recoveryState;
      persistRecoveryState();
      hasConnectedBefore = true;
      stopButton.disabled = false;
      setVoiceState(isResume ? "listening" : "processing", isResume ? "Devam edildi - dinleniyor" : "Ses ajanı bekleniyor...");
      if (opts.silentRecovery) {
        showToast("success", "Ses odası kurtarıldı ve yeniden katılındı.", "Ses kurtarma");
      }
    } catch (error) {
      const failedRoom = room;
      room = null;
      if (failedRoom) {
        try {
          await failedRoom.disconnect();
        } catch (_disconnectError) {
          // Ignore cleanup failures after a start error.
        }
      }
      console.error(error);
      const userMsg = (error.name === "NotAllowedError" || error.name === "PermissionDeniedError")
        ? "Mikrofon izni reddedildi. Tarayıcıda mikrofon erişimine izin verin ya da HTTPS kullanın."
        : (error.name === "NotSupportedError" || error.name === "NotFoundError")
          ? "Mikrofon bulunamadı ya da mikrofon API'si kullanılamıyor. Mikrofon erişimi için HTTPS gerekir."
          : (error.message || "Ses çalışma zamanı başlatılamadı.");
      setStatus(userMsg, "error");
      showToast("error", userMsg, "Ses başlatma başarısız");
      resetVoiceControls(false);
    }
  }

  async function stopVoice() {
    manualDisconnect = true;
    clearReconnectTimer();
    if (recovery) {
      recoveryState = recovery.recordExpectedDisconnect(recoveryState);
      persistRecoveryState();
    }
    stopButton.disabled = true;
    if (room) await room.disconnect();
    resetVoiceControls();
  }

  function resetVoiceControls(showStopped) {
    clearReconnectTimer();
    room = null;
    startButton.disabled = false;
    stopButton.disabled = true;
    audioContainer.replaceChildren();
    stopLevelMeter();
    if (hasConnectedBefore) {
      startButton.innerHTML = '<i class="fa-solid fa-microphone"></i> Devam Et';
    }
    if (showStopped !== false) setVoiceState("idle");
  }

  startButton.addEventListener("click", startVoice);
  stopButton.addEventListener("click", stopVoice);
  if (stopAgentButton) {
    stopAgentButton.addEventListener("click", async () => {
      stopAgentButton.disabled = true;
      try {
        // Server-side delivery — works even without joining the audio room.
        const response = await requestVoiceAction("stop_agent");
        showToast(
          response.delivered ? "success" : "warning",
          response.delivered ? "Ajanı durdurma komutu gönderildi." : "Komut gönderildi, teslim doğrulanamadı.",
          "Yönetici kontrolü"
        );
      } catch (error) {
        console.error(error);
        showToast("error", error.message || "Ajan durdurulamadı.", "Yönetici kontrolü");
      } finally {
        stopAgentButton.disabled = false;
      }
    });
  }
  if (sendReplacementButton) {
    sendReplacementButton.addEventListener("click", async () => {
      sendReplacementButton.disabled = true;
      try {
        // Server-side delivery — works even without joining the audio room.
        const response = await requestVoiceAction("replace_answer");
        showToast(
          response.delivered ? "success" : "warning",
          response.delivered ? "Cevap müşteriye gönderildi." : "Komut gönderildi, teslim doğrulanamadı.",
          "Yönetici kontrolü"
        );
      } catch (error) {
        console.error(error);
        showToast("error", error.message || "Değiştirilen cevap gönderilemedi.", "Yönetici kontrolü");
      } finally {
        sendReplacementButton.disabled = false;
      }
    });
  }
  if (endSessionForm) {
    endSessionForm.addEventListener("submit", () => {
      manualDisconnect = true;
      clearReconnectTimer();
      if (recovery) {
        recoveryState = recovery.recordExpectedDisconnect(recoveryState);
        persistRecoveryState();
      }
      if (room) room.disconnect();
    });
  }
  window.addEventListener("beforeunload", () => {
    pageUnload = true;
    if (room) room.disconnect();
  });
  if (recovery && recovery.shouldAutoResume(recoveryState)) {
    setVoiceState("reconnecting", "Önceki ses oturumu sürdürülüyor...");
    window.setTimeout(() => {
      startVoice({ forceResume: true, silentRecovery: true });
    }, 300);
  }
})();
