(function () {
  "use strict";

  const consoleElement = document.querySelector("#voice-test-console");
  if (!consoleElement || !window.LivekitClient) return;

  const { Room, RoomEvent, Track } = window.LivekitClient;
  const sessionId = consoleElement.dataset.sessionId;
  const startButton = document.querySelector("#voice-start");
  const stopButton = document.querySelector("#voice-stop");
  const statusElement = document.querySelector("#voice-status");
  const orbElement = document.querySelector("#voice-orb");
  const levelElement = document.querySelector("#voice-level");
  const levelBars = Array.from(levelElement?.querySelectorAll("span") || []);
  const audioContainer = document.querySelector("#voice-audio");
  const endSessionForm = document.querySelector("#end-session-form");

  let room = null;
  let audioContext = null;
  let levelFrame = null;
  const storageKey = `voice_connected_${sessionId}`;
  let hasConnectedBefore = localStorage.getItem(storageKey) === "1";

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
      listening: "Listening — you can speak",
      hearing: "I can hear you…",
      processing: "Processing your response…",
      speaking: "Agent is speaking — interrupt at any time",
      interrupted: "Interruption detected — switching turns",
      reconnecting: "Connection interrupted — reconnecting…",
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
      element.textContent = Number.isFinite(value) ? `${Math.round(value)} ms` : "—";
    });
  }

  async function startVoice() {
    startButton.disabled = true;
    setStatus("Connecting to voice runtime…", "working");

    const isResume = hasConnectedBefore;
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

      room = new Room({ adaptiveStream: true, dynacast: true });
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) {
          audioContainer.appendChild(track.attach());
        }
      });
      room.on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
        if (topic !== "voice.events") return;
        const event = JSON.parse(new TextDecoder().decode(payload));
        if (event.event === "voice_session_ready") {
          setVoiceState("listening");
        } else if (event.event === "speech_started") {
          setVoiceState("hearing");
        } else if (event.event === "speech_ended") {
          setVoiceState("processing");
        } else if (event.event === "transcript_final") {
          setVoiceState("processing", `Heard: “${event.text}”`);
        } else if (event.event === "agent_response") {
          setVoiceState("speaking");
        } else if (event.event === "interruption_detected") {
          setVoiceState("interrupted");
        } else if (event.event === "playback_cancelled") {
          setVoiceState("processing", "Agent stopped — processing your interruption");
        } else if (event.event === "backchannel_detected") {
          setVoiceState("listening", "Acknowledgement detected — continuing");
        } else if (event.event === "duplicate_transcript_ignored") {
          setVoiceState("listening", "Duplicate audio ignored");
        } else if (event.event === "voice_error") {
          setVoiceState("error", event.detail);
        } else if (event.event === "voice_turn_complete") {
          renderMetrics(event.metrics || {});
          setVoiceState("listening", "Listening — ready for the next turn");
          if (window.htmx) {
            window.htmx.trigger(document.body, "voice-turn-complete");
          }
        }
        if (window.htmx && !["speech_started", "speech_ended"].includes(event.event)) {
          window.htmx.trigger(document.body, "voice-event");
        }
      });
      room.on(RoomEvent.Reconnecting, () => setVoiceState("reconnecting"));
      room.on(RoomEvent.Reconnected, () => setVoiceState("listening", "Reconnected — listening"));
      room.on(RoomEvent.Disconnected, () => resetVoiceControls());

      await room.connect(credentials.server_url, credentials.token);
      await room.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      const microphone = room.localParticipant.getTrackPublication(Track.Source.Microphone);
      startLevelMeter(microphone?.track?.mediaStreamTrack);
      hasConnectedBefore = true;
      stopButton.disabled = false;
      setVoiceState(isResume ? "listening" : "processing", isResume ? "Resumed — listening" : "Waiting for voice agent…");
    } catch (error) {
      console.error(error);
      setStatus(error.message, "error");
      resetVoiceControls(false);
    }
  }

  async function stopVoice() {
    stopButton.disabled = true;
    if (room) await room.disconnect();
    resetVoiceControls();
  }

  function resetVoiceControls(showStopped) {
    room = null;
    startButton.disabled = false;
    stopButton.disabled = true;
    audioContainer.replaceChildren();
    stopLevelMeter();
    if (hasConnectedBefore) {
      localStorage.setItem(storageKey, "1");
      startButton.innerHTML = '<i class="fa-solid fa-microphone"></i> Resume';
    }
    if (showStopped !== false) setVoiceState("idle");
  }

  startButton.addEventListener("click", startVoice);
  stopButton.addEventListener("click", stopVoice);
  if (endSessionForm) {
    endSessionForm.addEventListener("submit", () => {
      if (room) room.disconnect();
    });
  }
  window.addEventListener("beforeunload", () => {
    if (room) room.disconnect();
  });
})();
