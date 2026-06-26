import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const pipeline = readFileSync(
  new URL("../../app/templates/pipeline.html", import.meta.url),
  "utf8",
);
const base = readFileSync(
  new URL("../../app/templates/base.html", import.meta.url),
  "utf8",
);
const panelUi = readFileSync(
  new URL("../../app/static/panel-ui.js", import.meta.url),
  "utf8",
);

test("pipeline destructive actions use the in-app confirmation contract", () => {
  assert.equal(pipeline.includes("onclick=\"return confirm("), false);
  assert.match(pipeline, /hx-confirm="Roll back to the previous model version\?"/);
  assert.match(pipeline, /data-confirm-title="Roll back live model\?"/);
  assert.match(pipeline, /data-confirm-kind="danger"/);
});

test("base template and panel UI provide an accessible confirmation modal", () => {
  assert.match(base, /role="dialog" aria-modal="true"/);
  assert.match(base, /data-confirm-submit/);
  assert.match(panelUi, /htmx:confirm/);
  assert.match(panelUi, /event\.key === "Escape"/);
  assert.match(panelUi, /issueRequest\(true\)/);
});
