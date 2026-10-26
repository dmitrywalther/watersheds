import test from "node:test";
import assert from "node:assert/strict";
import * as terrain from "./terrainMap.js";
import { toggleThematicLayer, updateAnalysisLayers } from "./categoricalMap.js";

const polygon = { type: "Polygon", coordinates: [[[35, 57], [36, 57], [36, 58], [35, 58], [35, 57]], [[35.2, 57.2], [35.3, 57.2], [35.3, 57.3], [35.2, 57.3], [35.2, 57.2]]] };
const feature = code => ({ type: "Feature", properties: { code }, geometry: polygon });
const payload = { status: "ok", unit: "m", source_label: "GMTED2010 30″", range: { min: -20, max: 40 }, bands: [{ code: 3, min: -20, max: 0 }, { code: 8, min: 0, max: 20 }, { code: 11, min: 20, max: 40 }], geometry: { type: "FeatureCollection", features: [feature(11), feature(3)] }, approximate: false, sample_resolution_m: 925 };

test("elevation colors follow the numerical range and preserve clipped holes and empty intervals", () => {
  const before = structuredClone(payload);
  const view = terrain.createTerrainMapView(payload, "elevation");
  assert.equal(view.status, "ok");
  assert.deepEqual(view.range, { min: -20, max: 40 });
  assert.equal(view.unit, "m");
  assert.equal(view.sourceLabel, "GMTED2010 30″");
  assert.deepEqual(view.bands.map(band => band.code), [3, 8, 11]);
  assert.deepEqual(view.geometry.features[0].geometry, polygon);
  assert.equal(view.geometry.features[0].properties.color, view.bands[2].color);
  assert.equal(view.geometry.features[1].properties.color, view.bands[0].color);
  assert.notEqual(view.bands[0].color, view.bands[2].color);
  assert.deepEqual(terrain.terrainScaleTicks(view), [-20, 10, 40]);
  for (const band of view.bands) assert.ok(terrain.terrainScaleBackground(view).includes(band.color));
  assert.deepEqual(payload, before);
});

test("slope uses degrees and a separate sequential palette including zero", () => {
  const data = { ...payload, unit: "degrees", range: { min: 0, max: 0.12 }, bands: [{ code: 1, min: 0, max: 0.04 }, { code: 2, min: 0.04, max: 0.08 }, { code: 3, min: 0.08, max: 0.12 }], geometry: { type: "FeatureCollection", features: [feature(1), feature(3)] }, approximate: true, reason: "Обзорная сетка" };
  const view = terrain.createTerrainMapView(data, "slope");
  assert.equal(view.status, "ok");
  assert.equal(view.unit, "degrees");
  assert.equal(view.unitLabel, "°");
  assert.equal(view.approximate, true);
  assert.equal(view.reason, "Обзорная сетка");
  assert.equal(view.sampleResolutionM, 925);
  assert.notEqual(view.bands[0].color, terrain.createTerrainMapView(payload, "elevation").bands[0].color);
  assert.deepEqual(terrain.terrainScaleTicks(view), [0, 0.06, 0.12]);
  assert.equal(terrain.formatTerrainValue(0.06, view.range), "0,06");
});

test("constant terrain has one color and one truthful tick", () => {
  const view = terrain.createTerrainMapView({ ...payload, range: { min: 100, max: 100 }, bands: [{ code: 1, min: 100, max: 100 }], geometry: { type: "FeatureCollection", features: [feature(1)] } }, "elevation");
  assert.equal(view.status, "ok");
  assert.deepEqual(terrain.terrainScaleTicks(view), [100]);
  assert.equal(terrain.terrainScaleBackground(view), view.bands[0].color);
});

test("tiny terrain differences stay legible without duplicated ticks or negative zero", () => {
  const range = { min: 0, max: 0.0000004 };
  const labels = [0, 0.0000002, 0.0000004].map(value => terrain.formatTerrainValue(value, range));
  assert.equal(new Set(labels).size, 3);
  assert.equal(terrain.formatTerrainValue(-0, { min: -2, max: 2 }), "0");
  assert.equal(terrain.formatTerrainValue(-0.00001, { min: -2, max: 2 }), "0");
});

test("missing terrain and incompatible units never leave geometry or a scale", () => {
  for (const data of [undefined, { ...payload, status: "no_data" }, { ...payload, status: "unavailable", reason: "Нет файла" }, { ...payload, unit: "degrees" }, { ...payload, geometry: { type: "FeatureCollection", features: [] } }]) {
    const view = terrain.createTerrainMapView(data, "elevation");
    assert.notEqual(view.status, "ok");
    assert.equal(view.geometry, null);
    assert.equal(view.range, null);
    assert.deepEqual(view.bands, []);
  }
  assert.equal(terrain.createTerrainMapView({ status: "unavailable", reason: "Нет файла" }, "elevation").reason, "Нет файла");
});

test("malformed numeric scales or unmatched features fail safely", () => {
  for (const invalid of [
    { range: { min: NaN, max: 40 } }, { range: { min: 40, max: -20 } },
    { bands: [] }, { bands: [{ code: 1, min: -20, max: 15 }, { code: 1, min: 15, max: 40 }] },
    { bands: [{ code: 1, min: -20, max: 10 }, { code: 2, min: 20, max: 40 }] },
    { bands: [{ code: 1, min: -20, max: 30 }, { code: 2, min: 20, max: 40 }] },
    { geometry: { type: "FeatureCollection", features: [feature(99)] } },
    { geometry: { type: "FeatureCollection", features: [{ ...feature(3), geometry: { type: "Point", coordinates: [35, 57] } }] } },
  ]) {
    const view = terrain.createTerrainMapView({ ...payload, ...invalid }, "elevation");
    assert.equal(view.status, "unavailable");
    assert.equal(view.geometry, null);
  }
  assert.equal(terrain.createTerrainMapView({ ...payload, unit: "degrees" }, "slope").status, "unavailable");
});

function fakeMap() {
  const sources = new Map(); const layers = new Map(); const order = [];
  return {
    sources, layers, order, getSource: id => sources.get(id), getLayer: id => layers.get(id),
    addSource(id, source) { sources.set(id, { ...source, setData(data) { this.data = data; } }); },
    addLayer(layer, before) { layers.set(layer.id, structuredClone(layer)); const index = order.indexOf(before); index < 0 ? order.push(layer.id) : order.splice(index, 0, layer.id); },
    setLayoutProperty(id, key, value) { const layer = layers.get(id); layer.layout = { ...layer.layout, [key]: value }; },
    setPaintProperty(id, key, value) { layers.get(id).paint[key] = value; },
  };
}

test("all four thematic fills are exclusive, independently switchable, and below the boundary", () => {
  const view = terrain.createTerrainMapView(payload, "elevation");
  const map = fakeMap();
  const state = { watershed: payload.geometry, basinVisible: true, soilMap: view, landcoverMap: view, elevationMap: view, slopeMap: view };
  for (const layer of ["soil", "landcover", "elevation", "slope"]) {
    assert.equal(toggleThematicLayer(null, layer), layer);
    assert.equal(toggleThematicLayer(layer, layer), null);
    updateAnalysisLayers(map, { ...state, activeThematicLayer: layer });
    for (const theme of ["soil", "landcover", "elevation", "slope"]) {
      assert.equal(map.layers.get(`watershed-${theme}-fill`).layout.visibility, theme === layer ? "visible" : "none");
      assert.ok(map.order.indexOf(`watershed-${theme}-fill`) < map.order.indexOf("watershed-outline"));
    }
    assert.equal(map.layers.get("watershed-fill").paint["fill-opacity"], 0);
  }
  updateAnalysisLayers(map, { ...state, activeThematicLayer: "slope", basinVisible: false });
  assert.equal(map.layers.get("watershed-outline").layout.visibility, "none");
  assert.equal(map.layers.get("watershed-slope-fill").layout.visibility, "visible");
  updateAnalysisLayers(map, { ...state, activeThematicLayer: null });
  assert.equal(map.layers.get("watershed-fill").paint["fill-opacity"], 0.18);
});

test("terrain sources clear on invalidation and recover on history or new map style", () => {
  const view = terrain.createTerrainMapView(payload, "elevation");
  const state = { watershed: payload.geometry, basinVisible: true, elevationMap: view, slopeMap: view, activeThematicLayer: "elevation" };
  const map = fakeMap();
  updateAnalysisLayers(map, state);
  updateAnalysisLayers(map, { watershed: null, basinVisible: true });
  for (const theme of ["elevation", "slope"]) {
    assert.deepEqual(map.sources.get(`watershed-${theme}`).data.features, []);
    assert.equal(map.layers.get(`watershed-${theme}-fill`).layout.visibility, "none");
  }
  for (const target of [map, fakeMap()]) {
    updateAnalysisLayers(target, state);
    assert.deepEqual(target.sources.get("watershed-elevation").data, view.geometry);
    assert.equal(target.layers.get("watershed-elevation-fill").layout.visibility, "visible");
    assert.equal(target.layers.get("watershed-slope-fill").layout.visibility, "none");
  }
});
