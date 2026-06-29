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

  function assertLiveRoomConnected() {
    if (!room || room.state !== ConnectionState.Connected) {
      throw new Error("Connect the voice room before sending supervisor actions.");
    }
  }

  if (hasConnectedBefore) {
    startButton.innerHTML = '<i class="fa-solid fa-microphone"></i> Resume';
  }

  function setStatus(message, state) {
    statusElement.textContent = message;
    statusElement.dataset.state = state || "";
    orbElement.dataset.state = state || "idle";
  }

  function setVoiceState(state, detail) {
    const states = {
      idle: "Microphone is stopped",
      listening: "Listening - you can speak",
      hearing: "Listening to the customer...",
      processing: "Processing the latest turn...",
      speaking: "Agent is speaking - interruption is available",
      interrupted: "Interruption detected - switching turns",
      reconnecting: "Connection interrupted - reconnecting...",
      error: detail || "Voice runtime error",
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
    setVoiceState("reconnecting", `Connection lost - retrying in ${Math.round(delayMs / 1000)}s`);
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
      showToast("warning", "The voice room disconnected. Automatic recovery is starting.", "Voice recovery");
      scheduleAutoResume();
      return;
    }
    setVoiceState("error", "Voice recovery exhausted - resume manually");
    showToast("error", "Automatic recovery reached its retry limit. Resume manually.", "Voice recovery");
  }

  async function publishControl(command) {
    if (!room) {
      throw new Error("Voice room is not connected");
    }
    const payload = new TextEncoder().encode(JSON.stringify(command));
    await room.localParticipant.publishData(payload, {
      reliable: true,
      topic: "voice.control",
    });
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
      const msg = "Microphone access requires HTTPS. Open this page over https:// or add the origin to Chrome's insecure-origins allowlist (chrome://flags/#unsafely-treat-insecure-origin-as-secure).";
      setStatus(msg, "error");
      showToast("error", msg, "HTTPS required");
      return;
    }

    clearReconnectTimer();
    manualDisconnect = false;
    startButton.disabled = true;
    setStatus("Connecting to voice runtime...", "working");

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
        throw new Error(credentials.detail || "Could not create voice token");
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
          setVoiceState("hearing", "Hearing: " + (event.text || ""));
        } else if (event.event === "transcript_final") {
          setVoiceState("processing", "Heard: " + (event.text || ""));
        } else if (event.event === "agent_response") {
          setVoiceState("speaking");
        } else if (event.event === "agent_playback_started") {
          setVoiceState("speaking");
        } else if (event.event === "possible_barge_in") {
          setVoiceState("interrupted", "Possible interruption — listening...");
        } else if (event.event === "interruption_detected") {
          setVoiceState("interrupted");
        } else if (event.event === "playback_cancelled") {
          setVoiceState("processing", "Agent stopped - processing your interruption");
          showToast("warning", "The active playback was cancelled because new customer speech was detected.", "Playback stopped");
        } else if (event.event === "backchannel_detected") {
          setVoiceState("listening", "Acknowledgement detected - continuing");
        } else if (event.event === "duplicate_transcript_ignored") {
          setVoiceState("listening", "Duplicate audio ignored");
          showToast("info", "A duplicate transcript was ignored to keep the conversation stable.", "Duplicate ignored");
        } else if (event.event === "empty_transcript") {
          setVoiceState("listening", "No speech detected - still listening");
        } else if (event.event === "stt_unavailable") {
          setVoiceState("listening", "Speech recognition unavailable - listening");
          showToast("warning", event.detail || "Speech recognition is temporarily unavailable.", "STT unavailable");
        } else if (event.event === "voice_error") {
          setVoiceState("error", event.detail);
          showToast("error", event.detail || "The voice runtime reported an error.", "Voice runtime");
        } else if (event.event === "voice_turn_complete") {
          renderMetrics(event.metrics || {});
          setVoiceState("listening", "Listening - ready for the next turn");
          if (window.htmx) {
            window.htmx.trigger(document.body, "voice-turn-complete");
          }
        } else if (event.event === "supervisor_stop_applied") {
          setVoiceState("processing", "Supervisor stopped the active answer");
          showToast("warning", "The active agent response was stopped.", "Supervisor control");
        } else if (event.event === "supervisor_replacement_started") {
          setVoiceState("speaking", "Supervisor replacement is playing");
          showToast("info", "The replacement answer is being played to the customer.", "Supervisor control");
        } else if (event.event === "supervisor_replacement_completed") {
          renderMetrics({ tts_first_audio_ms: event.tts_first_audio_ms });
          setVoiceState("listening", "Replacement completed - listening");
          showToast("success", "The replacement answer was delivered.", "Supervisor control");
        } else if (event.event === "supervisor_action_ignored") {
          showToast("warning", "The supervisor action was ignored by the voice runtime.", "Supervisor control");
        } else if (event.event === "tts_fallback_activated") {
          showToast("warning", "Primary TTS failed, mock PCM fallback was used for this turn.", "TTS fallback");
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
        setVoiceState("listening", "Reconnected - listening");
        showToast("success", "The live voice connection was restored.", "Voice reconnected");
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
      setVoiceState(isResume ? "listening" : "processing", isResume ? "Resumed - listening" : "Waiting for voice agent...");
      if (opts.silentRecovery) {
        showToast("success", "The voice room was recovered and rejoined.", "Voice recovery");
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
        ? "Microphone permission denied. Allow microphone access in the browser, or use HTTPS."
        : (error.name === "NotSupportedError" || error.name === "NotFoundError")
          ? "No microphone found or microphone API unavailable. HTTPS is required for microphone access."
          : (error.message || "Could not start the voice runtime.");
      setStatus(userMsg, "error");
      showToast("error", userMsg, "Voice start failed");
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
      startButton.innerHTML = '<i class="fa-solid fa-microphone"></i> Resume';
    }
    if (showStopped !== false) setVoiceState("idle");
  }

  startButton.addEventListener("click", startVoice);
  stopButton.addEventListener("click", stopVoice);
  if (stopAgentButton) {
    stopAgentButton.addEventListener("click", async () => {
      stopAgentButton.disabled = true;
      try {
        assertLiveRoomConnected();
        const response = await requestVoiceAction("stop_agent");
        await publishControl(response.command);
      } catch (error) {
        console.error(error);
        showToast("error", error.message || "Could not stop the agent.", "Supervisor control");
      } finally {
        stopAgentButton.disabled = false;
      }
    });
  }
  if (sendReplacementButton) {
    sendReplacementButton.addEventListener("click", async () => {
      sendReplacementButton.disabled = true;
      try {
        assertLiveRoomConnected();
        const response = await requestVoiceAction("replace_answer");
        await publishControl(response.command);
      } catch (error) {
        console.error(error);
        showToast("error", error.message || "Could not send the replacement answer.", "Supervisor control");
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
    setVoiceState("reconnecting", "Resuming the previous voice session...");
    window.setTimeout(() => {
      startVoice({ forceResume: true, silentRecovery: true });
    }, 300);
  }
})();
