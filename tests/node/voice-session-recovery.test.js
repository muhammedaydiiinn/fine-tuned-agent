const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const recovery = require(path.resolve(
  __dirname,
  "../../services/supervisor-panel/app/static/voice-session-recovery.js"
));

test("readStorage supports legacy connected marker", () => {
  const state = recovery.readStorage("1");
  assert.equal(state.hasConnectedBefore, true);
  assert.equal(state.shouldResume, false);
  assert.equal(state.reconnectAttempts, 0);
});

test("connected state enables future auto resume", () => {
  const state = recovery.recordConnected();
  assert.equal(recovery.shouldAutoResume(state), true);
  assert.equal(state.reconnectAttempts, 0);
});

test("expected disconnect clears auto resume", () => {
  const state = recovery.recordExpectedDisconnect(recovery.recordConnected());
  assert.equal(recovery.shouldAutoResume(state), false);
  assert.equal(state.hasConnectedBefore, true);
});

test("unexpected disconnect increments attempts and eventually stops retrying", () => {
  let state = recovery.recordConnected();
  state = recovery.recordUnexpectedDisconnect(state);
  assert.equal(state.reconnectAttempts, 1);
  assert.equal(recovery.shouldAutoResume(state), true);

  state = recovery.recordUnexpectedDisconnect(state);
  assert.equal(state.reconnectAttempts, 2);
  assert.equal(recovery.shouldAutoResume(state), true);

  state = recovery.recordUnexpectedDisconnect(state);
  assert.equal(state.reconnectAttempts, 3);
  assert.equal(recovery.shouldAutoResume(state), false);
});

test("recovery delay grows with a cap", () => {
  assert.equal(recovery.nextRecoveryDelayMs(1), 1500);
  assert.equal(recovery.nextRecoveryDelayMs(2), 3000);
  assert.equal(recovery.nextRecoveryDelayMs(3), 6000);
  assert.equal(recovery.nextRecoveryDelayMs(10), 10000);
});
