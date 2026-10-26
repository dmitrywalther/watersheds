import assert from "node:assert/strict";
import test from "node:test";
import { INITIAL_ANALYSIS_SECTION, analysisSelectionReducer, getActiveThematicLayer } from "./analysisSelection.js";

const availableViews = Object.fromEntries(["soil", "landcover", "elevation", "slope"].map(kind => [kind, { status: "ok" }]));

test("new results and restored history start with overview and no thematic layer", () => {
  assert.equal(INITIAL_ANALYSIS_SECTION, "overview");
  for (const previous of Object.keys(availableViews)) {
    const section = analysisSelectionReducer(previous, { type: "reset" });
    assert.equal(section, "overview");
    assert.equal(getActiveThematicLayer(section, availableViews), null);
  }
});

test("explicit analysis selection replaces the previous map theme and overview or climate clears it", () => {
  let section = INITIAL_ANALYSIS_SECTION;
  for (const next of ["soil", "elevation", "slope", "landcover", "climate", "soil", "overview"]) {
    section = analysisSelectionReducer(section, { type: "select", section: next });
    assert.equal(section, next);
    assert.equal(getActiveThematicLayer(section, availableViews), availableViews[next] ? next : null);
  }
});

test("selecting a tab without map data keeps that tab readable while disabling the old theme", () => {
  for (const status of ["no_data", "unavailable", "invalid"]) {
    const section = analysisSelectionReducer("soil", { type: "select", section: "elevation" });
    assert.equal(section, "elevation");
    assert.equal(getActiveThematicLayer(section, { ...availableViews, elevation: { status } }), null);
  }
  assert.equal(getActiveThematicLayer("soil", {}), null);
  assert.equal(getActiveThematicLayer("soil"), null);
});

test("reopening analysis preserves its selection and invalid events cannot activate a theme", () => {
  const section = analysisSelectionReducer("slope", { type: "select", section: "terrain" });
  assert.equal(section, "slope");
  assert.equal(analysisSelectionReducer(section, { type: "reopen" }), "slope");
  assert.equal(getActiveThematicLayer("constructor", availableViews), null);
});
