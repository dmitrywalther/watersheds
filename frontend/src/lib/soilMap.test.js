import test from "node:test";
import assert from "node:assert/strict";
import * as soil from "./soil.js";

const polygon = { type: "Polygon", coordinates: [[[35, 57], [36, 57], [36, 58], [35, 58], [35, 57]], [[35.2, 57.2], [35.3, 57.2], [35.3, 57.3], [35.2, 57.3], [35.2, 57.2]]] };
const feature = (code, name = `Почва ${code}`) => ({ type: "Feature", properties: { code, name }, geometry: polygon });
const payload = { status: "ok", geometry: { type: "FeatureCollection", features: [feature(38), feature(43), feature(38)] }, approximate: false, sample_resolution_m: 250, reason: null };

test("soil colors are stable by code, independent of category rank or visibility", () => {
  assert.equal(typeof soil.soilColor, "function");
  assert.equal(soil.soilColor(38), soil.soilColor(38));
  assert.notEqual(soil.soilColor(38), soil.soilColor(43));
  assert.notEqual(soil.soilColor(307), soil.soilColor(38));
  assert.equal(typeof soil.soilColor(999), "string");
});

test("soil map retains clipped holes and sampling metadata without altering precise shares", () => {
  assert.equal(typeof soil.createSoilMapView, "function");
  const before = structuredClone(payload);
  const view = soil.createSoilMapView({ ...payload, approximate: true, sample_resolution_m: 1000 }, { status: "ok", categories: [{ code: 43, percent: 75 }, { code: 38, percent: 25 }] });
  assert.equal(view.status, "ok");
  assert.equal(view.approximate, true);
  assert.equal(view.sampleResolutionM, 1000);
  assert.deepEqual(view.geometry.features[0].geometry, polygon);
  assert.deepEqual(view.categories.map(entry => [entry.code, entry.percent]), [[43, 75], [38, 25]]);
  assert.equal(view.geometry.features[0].properties.color, soil.soilColor(38));
  assert.deepEqual(payload, before);
});

test("missing, empty and failed soil maps never retain stale geometry or legend", () => {
  assert.equal(typeof soil.createSoilMapView, "function");
  for (const data of [undefined, { ...payload, status: "unavailable", reason: "Размер слишком велик" }, { ...payload, status: "no_data" }, { ...payload, geometry: { type: "FeatureCollection", features: [] } }]) {
    const view = soil.createSoilMapView(data);
    assert.notEqual(view.status, "ok");
    assert.equal(view.geometry, null);
    assert.deepEqual(view.categories, []);
  }
  assert.equal(soil.createSoilMapView({ status: "unavailable", reason: "Размер слишком велик" }).reason, "Размер слишком велик");
});

test("malformed map polygons fail safely without touching the statistical analysis", () => {
  assert.equal(typeof soil.createSoilMapView, "function");
  for (const bad of [{ ...feature(38), geometry: { type: "Point", coordinates: [35, 57] } }, feature("38"), { ...feature(38), geometry: { type: "Polygon", coordinates: [[[35, 57], [36, Infinity], [36, 58], [35, 57]]] } }]) {
    const view = soil.createSoilMapView({ ...payload, geometry: { type: "FeatureCollection", features: [bad] } });
    assert.equal(view.status, "unavailable");
    assert.equal(view.geometry, null);
  }
});

test("map legend includes only drawn classes and keeps unknown codes even without statistics", () => {
  assert.equal(typeof soil.createSoilMapView, "function");
  const view = soil.createSoilMapView({ ...payload, geometry: { type: "FeatureCollection", features: [feature(999, " "), feature(38)] } }, { status: "ok", categories: [{ code: 43, percent: 100 }] });
  assert.deepEqual(view.categories.map(entry => entry.code), [38, 999]);
  assert.equal(view.categories[1].name, "Неизвестный код 999");
  assert.equal(view.categories[1].percent, null);
});

function fakeMap() {
  const sources = new Map(); const layers = new Map(); const order = [];
  return {
    sources, layers, order,
    getSource: id => sources.get(id), getLayer: id => layers.get(id),
    addSource(id, source) { sources.set(id, { ...source, setData(data) { this.data = data; } }); },
    addLayer(layer, before) { layers.set(layer.id, structuredClone(layer)); const index = order.indexOf(before); index < 0 ? order.push(layer.id) : order.splice(index, 0, layer.id); },
    setLayoutProperty(id, property, value) { const layer = layers.get(id); layer.layout = { ...layer.layout, [property]: value }; },
    setPaintProperty(id, property, value) { layers.get(id).paint[property] = value; },
  };
}

test("soil colors sit below the basin outline without green tint; visibility is independent", () => {
  assert.equal(typeof soil.updateAnalysisLayers, "function");
  const map = fakeMap(); const view = soil.createSoilMapView(payload);
  const state = { watershed: payload.geometry, basinVisible: true, soilMap: view, soilVisible: true };
  soil.updateAnalysisLayers(map, state);
  assert.deepEqual(map.order, ["watershed-fill", "watershed-soil-fill", "watershed-outline"]);
  assert.equal(map.sources.get("watershed-soil").tolerance, 0);
  assert.equal(map.sources.get("watershed-result").tolerance, 0);
  assert.equal(map.layers.get("watershed-fill").paint["fill-opacity"], 0);
  assert.equal(map.layers.get("watershed-soil-fill").layout.visibility, "visible");
  soil.updateAnalysisLayers(map, { ...state, basinVisible: false });
  assert.equal(map.layers.get("watershed-outline").layout.visibility, "none");
  assert.equal(map.layers.get("watershed-soil-fill").layout.visibility, "visible");
  soil.updateAnalysisLayers(map, { ...state, soilVisible: false });
  assert.equal(map.layers.get("watershed-fill").paint["fill-opacity"], 0.18);
  assert.equal(map.layers.get("watershed-soil-fill").layout.visibility, "none");
});

test("point, DEM or calculation invalidation clears the map; history and style reload restore it", () => {
  assert.equal(typeof soil.updateAnalysisLayers, "function");
  const map = fakeMap(); const state = { watershed: payload.geometry, basinVisible: true, soilMap: soil.createSoilMapView(payload), soilVisible: true };
  soil.updateAnalysisLayers(map, state);
  soil.updateAnalysisLayers(map, { ...state, watershed: null, soilMap: soil.createSoilMapView() });
  assert.deepEqual(map.sources.get("watershed-soil").data.features, []);
  assert.equal(map.layers.get("watershed-soil-fill").layout.visibility, "none");
  soil.updateAnalysisLayers(map, state);
  assert.deepEqual(map.sources.get("watershed-soil").data, state.soilMap.geometry);
  const freshStyle = fakeMap();
  soil.updateAnalysisLayers(freshStyle, { ...state, soilVisible: false });
  assert.deepEqual(freshStyle.sources.get("watershed-soil").data, state.soilMap.geometry);
  assert.equal(freshStyle.layers.get("watershed-soil-fill").layout.visibility, "none");
  assert.equal(freshStyle.layers.get("watershed-outline").layout.visibility, "visible");
});
