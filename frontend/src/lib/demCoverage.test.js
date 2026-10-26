import test from "node:test";
import assert from "node:assert/strict";
import { parseDemCoverage, startCoverageRequest, updateDemCoverage, coverageFitBounds } from "./demCoverage.js";

const polygon = { type: "Polygon", coordinates: [[[88, 58], [109, 58], [109, 68], [88, 68], [88, 58]], [[95, 60], [96, 60], [96, 61], [95, 61], [95, 60]]] };
const payload = { source_id: "ALOS_local", geometry: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: polygon }] }, bounds: [88, 58, 109, 68], approximate: true, sample_resolution_m: 2500, note: "Приблизительное покрытие" };

test("coverage retains polygon holes and approximation metadata", () => {
  assert.deepEqual(parseDemCoverage(payload, "ALOS_local"), payload);
  assert.deepEqual(coverageFitBounds(payload), [[88, 58], [109, 68]]);
});

test("coverage rejects stale source IDs, invalid bounds and malformed geometry", () => {
  for (const data of [null, { ...payload, source_id: "MERIT_3sec" }, { ...payload, bounds: [109, 58, 88, 68] }, { ...payload, bounds: [88, 58, 109, Infinity] }, { ...payload, geometry: { type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 1]]] } }] } }]) {
    assert.throws(() => parseDemCoverage(data, "ALOS_local"), /покрыти/i);
  }
});

test("empty coverage has no fit target", () => {
  const empty = { ...payload, geometry: { type: "FeatureCollection", features: [] }, bounds: null };
  assert.deepEqual(parseDemCoverage(empty, "ALOS_local"), empty);
  assert.equal(coverageFitBounds(empty), null);
});

test("camera bounds preserve a short arc across the dateline", () => {
  const dateline = { ...payload, bounds: [20, 40, 190, 82] };
  assert.deepEqual(coverageFitBounds(parseDemCoverage(dateline, "ALOS_local")), [[20, 40], [190, 82]]);
});

test("a cancelled previous source cannot replace the current coverage even if its request resolves", async () => {
  const pending = [];
  const states = [];
  const fetchCoverage = (_source, options) => new Promise(resolve => pending.push({ resolve, signal: options.signal }));
  const old = startCoverageRequest("ALOS_local", fetchCoverage, state => states.push(state));
  old.cancel();
  const current = startCoverageRequest("MERIT_3sec", fetchCoverage, state => states.push(state));
  pending[1].resolve({ ...payload, source_id: "MERIT_3sec" });
  await current.done;
  pending[0].resolve(payload);
  await old.done;
  assert.equal(pending[0].signal.aborted, true);
  assert.deepEqual(states.map(state => [state.sourceId, state.status]), [["ALOS_local", "loading"], ["MERIT_3sec", "loading"], ["MERIT_3sec", "ready"]]);
});

test("request failures are recoverable without retaining old geometry", async () => {
  const states = [];
  const request = startCoverageRequest("ALOS_local", async () => { throw new Error("network"); }, state => states.push(state));
  await request.done;
  assert.equal(states.at(-1).status, "error");
  assert.equal(states.at(-1).coverage, null);
  const retry = startCoverageRequest("ALOS_local", async () => payload, state => states.push(state));
  await retry.done;
  assert.equal(states.at(-1).status, "ready");
});

function fakeMap() {
  const sources = new Map();
  const layers = new Map([["watershed-fill", { id: "watershed-fill" }]]);
  const insertions = [];
  return {
    sources, layers, insertions,
    getSource: id => sources.get(id),
    addSource(id, source) { sources.set(id, { ...source, setData(data) { this.data = data; } }); },
    getLayer: id => layers.get(id),
    addLayer(layer, before) { layers.set(layer.id, { ...layer }); insertions.push({ id: layer.id, before }); },
    setLayoutProperty(id, property, value) { layers.get(id).layout = { [property]: value }; },
  };
}

test("coverage stays below basin layers, toggles together, and clears for a different pending source", () => {
  const map = fakeMap();
  updateDemCoverage(map, payload.geometry, true);
  assert.deepEqual(map.insertions.map(entry => entry.before), ["watershed-fill", "watershed-fill", "watershed-fill"]);
  assert.deepEqual(map.insertions.map(entry => entry.id), ["dem-coverage-fill", "dem-coverage-halo", "dem-coverage-outline"]);
  assert.deepEqual(map.layers.get("dem-coverage-halo").paint["line-dasharray"], [1.5, 1]);
  assert.equal(map.layers.get("dem-coverage-halo").paint["line-width"], 4);
  assert.deepEqual(map.layers.get("dem-coverage-outline").paint["line-dasharray"], [3, 2]);
  updateDemCoverage(map, null, false);
  assert.deepEqual(map.sources.get("dem-coverage").data.features, []);
  assert.equal(map.layers.get("dem-coverage-fill").layout.visibility, "none");
  assert.equal(map.layers.get("dem-coverage-halo").layout.visibility, "none");
  assert.equal(map.layers.get("dem-coverage-outline").layout.visibility, "none");
  assert.equal(map.insertions.length, 3);
});

test("a fresh basemap style recreates the selected coverage with the current visibility", () => {
  const map = fakeMap();
  map.layers.clear();
  updateDemCoverage(map, payload.geometry, false);
  assert.equal(map.sources.get("dem-coverage").data, payload.geometry);
  assert.equal(map.layers.get("dem-coverage-outline").layout.visibility, "none");
  assert.equal(map.layers.get("dem-coverage-halo").layout.visibility, "none");
});
