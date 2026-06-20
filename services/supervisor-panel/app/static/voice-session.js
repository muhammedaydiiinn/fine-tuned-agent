(function () {
  "use strict";

  const consoleElement = document.querySelector("#voice-test-console");
  if (!consoleElement || !window.LivekitClient) return;

  const { Room, RoomEvent, Track } = window.LivekitClient;
  const sessionId = consoleElement.dataset.sessionId;
  const startButton = document.querySelector("#voice-start");
  const stopButton = document.querySelector("#voice-stop");
  const statusElement = document.querySelector("#voice-status");
  const audioContainer = document.querySelector("#voice-audio");
  const endSessionForm = document.querySelector("#end-session-form");

  let room = null;

  function setStatus(message, state) {
    statusElement.textContent = message;
    statusElement.dataset.state = state || "";
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

    try {
      const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
      const response = await fetch(`/sessions/${sessionId}/voice-token`, {
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
          setStatus("Ready — speak freely", "ready");
        } else if (event.event === "transcript_final") {
          setStatus(`Transcript: ${event.text}`, "working");
        } else if (event.event === "agent_response") {
          setStatus("Agent is speaking…", "speaking");
        } else if (event.event === "voice_turn_complete") {
          renderMetrics(event.metrics || {});
          setStatus("Ready for the next turn", "ready");
          if (window.htmx) {
            window.htmx.trigger(document.body, "voice-turn-complete");
          }
        }
      });
      room.on(RoomEvent.Disconnected, () => resetVoiceControls());

      await room.connect(credentials.server_url, credentials.token);
      await room.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      stopButton.disabled = false;
      setStatus("Waiting for voice agent…", "working");
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
    if (showStopped !== false) setStatus("Microphone is stopped", "");
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
