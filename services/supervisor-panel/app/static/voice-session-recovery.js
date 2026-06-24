(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.VoiceSessionRecovery = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const MAX_AUTO_RECOVERY_ATTEMPTS = 3;
  const BASE_RECOVERY_DELAY_MS = 1500;
  const MAX_RECOVERY_DELAY_MS = 10000;

  function defaultState() {
    return {
      hasConnectedBefore: false,
      shouldResume: false,
      reconnectAttempts: 0,
    };
  }

  function readStorage(rawValue) {
    if (rawValue === "1") {
      return {
        hasConnectedBefore: true,
        shouldResume: false,
        reconnectAttempts: 0,
      };
    }
    if (!rawValue) {
      return defaultState();
    }
    try {
      const parsed = JSON.parse(rawValue);
      return {
        hasConnectedBefore: Boolean(parsed.hasConnectedBefore),
        shouldResume: Boolean(parsed.shouldResume),
        reconnectAttempts: Number.isInteger(parsed.reconnectAttempts)
          ? Math.max(0, parsed.reconnectAttempts)
          : 0,
      };
    } catch (_error) {
      return defaultState();
    }
  }

  function writeStorage(state) {
    return JSON.stringify({
      hasConnectedBefore: Boolean(state.hasConnectedBefore),
      shouldResume: Boolean(state.shouldResume),
      reconnectAttempts: Number.isInteger(state.reconnectAttempts)
        ? Math.max(0, state.reconnectAttempts)
        : 0,
    });
  }

  function recordConnected() {
    return {
      hasConnectedBefore: true,
      shouldResume: true,
      reconnectAttempts: 0,
    };
  }

  function recordExpectedDisconnect(state) {
    return {
      hasConnectedBefore: Boolean(state?.hasConnectedBefore),
      shouldResume: false,
      reconnectAttempts: 0,
    };
  }

  function recordUnexpectedDisconnect(state) {
    const previous = readStorage(writeStorage(state || defaultState()));
    const reconnectAttempts = Math.max(0, previous.reconnectAttempts) + 1;
    return {
      hasConnectedBefore: previous.hasConnectedBefore,
      shouldResume: reconnectAttempts < MAX_AUTO_RECOVERY_ATTEMPTS,
      reconnectAttempts,
    };
  }

  function shouldAutoResume(state) {
    return Boolean(
      state &&
        state.hasConnectedBefore &&
        state.shouldResume &&
        state.reconnectAttempts < MAX_AUTO_RECOVERY_ATTEMPTS
    );
  }

  function nextRecoveryDelayMs(attemptNumber) {
    const attempt = Math.max(1, Number(attemptNumber) || 1);
    return Math.min(
      BASE_RECOVERY_DELAY_MS * Math.pow(2, attempt - 1),
      MAX_RECOVERY_DELAY_MS
    );
  }

  return {
    MAX_AUTO_RECOVERY_ATTEMPTS,
    nextRecoveryDelayMs,
    readStorage,
    recordConnected,
    recordExpectedDisconnect,
    recordUnexpectedDisconnect,
    shouldAutoResume,
    writeStorage,
  };
});
