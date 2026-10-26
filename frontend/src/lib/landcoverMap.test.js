import test from "node:test";
import assert from "node:assert/strict";
import * as landcover from "./landcover.js";
import * as thematic from "./categoricalMap.js";
import { createSoilMapView, soilColor } from "./soil.js";

const polygon = { type: "Polygon", coordinates: [[[35, 57], [36, 57], [36, 58], [35, 58], [35, 57]], [[35.2, 57.2], [35.3, 57.2], [35.3, 57.3], [35.2, 57.3], [35.2, 57.2]]] };
const feature = (code, name) => ({ type: "Feature", properties: { code, name }, geometry: polygon });
const payload = { status: "ok", geometry: { type: "FeatureCollection", features: [feature(210, "Water bodies"), feature(50, "Closed broadleaved deciduous forest"), feature(210, "Water bodies")] }, approximate: false, sample_resolution_m: 250 };

test("landcover chart and map share stable semantic colors and Russian labels", () => {
  assert.equal(typeof landcover.landcoverEntries, "function");
  assert.equal(typeof landcover.createLandcoverMapView, "function");
  const data = { "Water bodies": 30, "Closed broadleaved deciduous forest": 70 };
  const entries = landcover.landcoverEntries(data);
  const view = landcover.createLandcoverMapView(payload, data);
  assert.deepEqual(view.categories.map(entry => entry.name), ["Сомкнутые лиственные леса", "Водные объекты"]);
  for (const entry of entries) assert.equal(view.categories.find(category => category.name === entry.label).color, entry.color);
  const reordered = landcover.landcoverEntries({ "Water bodies": 80, "Closed broadleaved deciduous forest": 20 });
  assert.equal(entries.find(entry => entry.name === "Water bodies").color, reordered[0].color);
  assert.equal(landcover.landcoverColor(210), "#4c91bd");
  assert.notEqual(landcover.landcoverColor(50), landcover.landcoverColor(190));
  assert.notEqual(landcover.landcoverColor(11), landcover.landcoverColor(210));
});

test("landcover legend only lists displayed classes and never renormalizes existing shares", () => {
  assert.equal(typeof landcover.createLandcoverMapView, "function");
  const before = structuredClone(payload);
  const view = landcover.createLandcoverMapView({ ...payload, approximate: true, sample_resolution_m: 1250 }, { "Water bodies": 12, "No data": 8, "Rainfed croplands": 20 });
  assert.deepEqual(view.categories.map(entry => [entry.code, entry.percent]), [[210, 12], [50, null]]);
  assert.deepEqual(view.geometry.features[0].geometry, polygon);
  assert.equal(view.approximate, true);
  assert.equal(view.sampleResolutionM, 1250);
  assert.deepEqual(payload, before);
});

test("No data is transparent while unknown landcover classes remain visible", () => {
  assert.equal(typeof landcover.createLandcoverMapView, "function");
  const view = landcover.createLandcoverMapView({ ...payload, geometry: { type: "FeatureCollection", features: [feature(230, "No data"), feature(999, " "), feature(998, "No data")] } }, { 999: 6 });
  assert.equal(view.status, "ok");
  assert.deepEqual(view.categories.map(entry => [entry.code, entry.name, entry.percent]), [[999, "Неизвестный код 999", 6]]);
  assert.equal(view.geometry.features.length, 1);
  assert.equal(view.geometry.features[0].properties.color, landcover.landcoverColor(999));
  const empty = landcover.createLandcoverMapView({ ...payload, geometry: { type: "FeatureCollection", features: [feature(230, "No data")] } });
  assert.equal(empty.status, "no_data");
  assert.equal(empty.geometry, null);
});

test("unavailable, absent, or malformed landcover maps cannot retain previous geometry", () => {
  assert.equal(typeof landcover.createLandcoverMapView, "function");
  for (const data of [undefined, { ...payload, status: "unavailable", reason: "Предел детализации" }, { ...payload, status: "no_data" }, { ...payload, geometry: { type: "FeatureCollection", features: [{ ...feature(50), geometry: { type: "Point", coordinates: [35, 57] } }] } }]) {
    const view = landcover.createLandcoverMapView(data);
    assert.notEqual(view.status, "ok");
    assert.equal(view.geometry, null);
    assert.deepEqual(view.categories, []);
  }
  assert.equal(landcover.createLandcoverMapView({ status: "unavailable", reason: "Предел детализации" }).reason, "Предел детализации");
});

test("invalid statistical shares stay absent and rare zero-rounded classes remain visible", () => {
  assert.equal(typeof landcover.createLandcoverMapView, "function");
  for (const percent of [NaN, -1, 101, "4"]) {
    const view = landcover.createLandcoverMapView(payload, { "Water bodies": percent });
    assert.equal(view.categories.find(entry => entry.code === 210).percent, null);
  }
  assert.equal(landcover.createLandcoverMapView(payload, { "Water bodies": 0 }).categories.find(entry => entry.code === 210).percent, 0);
  assert.equal(landcover.landcoverEntries({ "Water bodies": 0 })[0].value, 0);
});

test("single thematic selection switches between soil and landcover and can turn both off", () => {
  assert.equal(typeof thematic.toggleThematicLayer, "function");
  assert.equal(thematic.toggleThematicLayer("soil", "landcover"), "landcover");
  assert.equal(thematic.toggleThematicLayer("landcover", "soil"), "soil");
  assert.equal(thematic.toggleThematicLayer("landcover", "landcover"), null);
  assert.equal(thematic.toggleThematicLayer(null, "soil"), "soil");
  assert.equal(thematic.toggleThematicLayer("soil", "unknown"), "soil");
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

test("only selected thematic fill is visible below independent basin outline", () => {
  assert.equal(typeof thematic.updateAnalysisLayers, "function");
  assert.equal(typeof landcover.createLandcoverMapView, "function");
  const map = fakeMap();
  const soilMap = createSoilMapView({ ...payload, geometry: { type: "FeatureCollection", features: [feature(38, "Почва")] } });
  const state = { watershed: payload.geometry, basinVisible: true, soilMap, landcoverMap: landcover.createLandcoverMapView(payload), activeThematicLayer: "landcover" };
  thematic.updateAnalysisLayers(map, state);
  assert.deepEqual(map.order, ["watershed-fill", "watershed-soil-fill", "watershed-landcover-fill", "watershed-outline"]);
  assert.equal(map.sources.get("watershed-landcover").tolerance, 0);
  assert.equal(map.layers.get("watershed-fill").paint["fill-opacity"], 0);
  assert.equal(map.layers.get("watershed-soil-fill").layout.visibility, "none");
  assert.equal(map.layers.get("watershed-landcover-fill").layout.visibility, "visible");
  assert.equal(map.sources.get("watershed-soil").data.features[0].properties.color, soilColor(38));
  thematic.updateAnalysisLayers(map, { ...state, activeThematicLayer: "soil", basinVisible: false });
  assert.equal(map.layers.get("watershed-outline").layout.visibility, "none");
  assert.equal(map.layers.get("watershed-soil-fill").layout.visibility, "visible");
  assert.equal(map.layers.get("watershed-landcover-fill").layout.visibility, "none");
  thematic.updateAnalysisLayers(map, { ...state, activeThematicLayer: null });
  assert.equal(map.layers.get("watershed-fill").paint["fill-opacity"], 0.18);
  assert.equal(map.layers.get("watershed-soil-fill").layout.visibility, "none");
  assert.equal(map.layers.get("watershed-landcover-fill").layout.visibility, "none");
});

test("invalidation clears both sources; history and basemap reload restore the chosen layer", () => {
  assert.equal(typeof thematic.updateAnalysisLayers, "function");
  assert.equal(typeof landcover.createLandcoverMapView, "function");
  const map = fakeMap();
  const state = { watershed: payload.geometry, basinVisible: true, soilMap: createSoilMapView(payload), landcoverMap: landcover.createLandcoverMapView(payload), activeThematicLayer: "landcover" };
  thematic.updateAnalysisLayers(map, state);
  thematic.updateAnalysisLayers(map, { ...state, watershed: null });
  for (const name of ["soil", "landcover"]) {
    assert.deepEqual(map.sources.get(`watershed-${name}`).data.features, []);
    assert.equal(map.layers.get(`watershed-${name}-fill`).layout.visibility, "none");
  }
  thematic.updateAnalysisLayers(map, state);
  assert.deepEqual(map.sources.get("watershed-landcover").data, state.landcoverMap.geometry);
  const freshStyle = fakeMap();
  thematic.updateAnalysisLayers(freshStyle, state);
  assert.equal(freshStyle.layers.get("watershed-landcover-fill").layout.visibility, "visible");
  assert.equal(freshStyle.layers.get("watershed-soil-fill").layout.visibility, "none");
});
