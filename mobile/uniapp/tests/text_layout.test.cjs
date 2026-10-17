const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const context = {};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, "../utils/text_layout.js"), "utf8")
  .replace("export function buildTextLayout", "function buildTextLayout"), context);
const cases = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../../evaluation/fixtures/text_layout_cases.json"), "utf8")).cases;
for (const sample of cases) test("shared text layout: " + sample.name, () => {
  const before = JSON.stringify(sample.boxes);
  const result = context.buildTextLayout(sample.boxes);
  assert.deepEqual(JSON.parse(JSON.stringify(result.rows)), sample.expected_rows);
  assert.equal(result.text, sample.expected_text);
  assert.equal(result.mode, sample.expected_mode);
  assert.equal(JSON.stringify(sample.boxes), before);
});
test("nonfinite coordinates cannot corrupt the original text order", () => {
  for (const bad of [NaN, Infinity, -Infinity]) {
    const boxes = [{ text: "原文", box: [[0, 0], [bad, 0], [20, 20], [0, 20]] }];
    assert.equal(context.buildTextLayout(boxes).mode, "original_order");
    assert.equal(context.buildTextLayout(boxes).text, "原文");
  }
});
test("invalid entries and overflowing or vanishing geometry use the cross-runtime fallback", () => {
  for (const bad of [null,
    { text: "", box: [[0, 0], [1e308, 0], [1e308, 1e308], [0, 1e308]] },
    { text: "", box: [[0, 0], [1e-7, 0], [1e-7, 100], [0, 100]] }]) {
    const result = context.buildTextLayout([bad, { text: "保留原文" }]);
    assert.equal(result.mode, "original_order");
    assert.equal(result.text, "保留原文");
  }
});
