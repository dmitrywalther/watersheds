import test from "node:test";
import assert from "node:assert/strict";
import { createMapHoverController, queryMapHover, requestTerrainValue } from "./mapHover.js";

const view = { status: "ok", geometry: { features: [{}] } };
const context = (layer = "soil") => ({ watershed: {}, activeThematicLayer: layer, selecting: false, soilMap: view, landcoverMap: view, elevationMap: view, slopeMap: view });
const event = (lng = 35, lat = 57) => ({ point: { x: 50, y: 80 }, lngLat: { lng, lat } });
function fakeMap(properties = { name: "Дерново-подзолистые почвы", code: 14, color: "#aabbcc" }) {
  return {
    queries: [], hidden: false, missing: false, outside: false,
    getLayer() { return this.missing ? undefined : {}; },
    getLayoutProperty() { return this.hidden ? "none" : "visible"; },
    queryRenderedFeatures(point, options) { this.queries.push({ point, options }); return this.outside ? [] : [{ properties }]; },
  };
}

test("hover reads only the active rendered thematic fill and its translated class name", () => {
  const map = fakeMap();
  const hover = queryMapHover(map, event(), context());
  assert.equal(hover.title, "Почвы");
  assert.equal(hover.value, "Дерново-подзолистые почвы");
  assert.equal(hover.color, "#aabbcc");
  assert.deepEqual(map.queries, [{ point: { x: 50, y: 80 }, options: { layers: ["watershed-soil-fill"] } }]);
  assert.equal(queryMapHover(fakeMap({ code: 999 }), event(), context("landcover")).value, "Неизвестный класс (код 999)");
});

test("outside the visible fill, hidden layers, no result, selection and invalid coordinates produce no hover", () => {
  for (const overrides of [{ outside: true }, { hidden: true }, { missing: true }]) {
    assert.equal(queryMapHover(Object.assign(fakeMap(), overrides), event(), context()), null);
  }
  const map = fakeMap();
  for (const overrides of [{ watershed: null }, { activeThematicLayer: null }, { selecting: true }, { soilMap: { status: "no_data" } }]) {
    assert.equal(queryMapHover(map, event(), { ...context(), ...overrides }), null);
  }
  for (const point of [event(NaN), event(Infinity), event(35, 86), { ...event(), point: { x: NaN, y: 80 } }]) assert.equal(queryMapHover(map, point, context()), null);
  assert.deepEqual(map.queries, []);
});

test("wrapped map copies use normalized longitude for native probes and ignore terrain band midpoints", () => {
  const hover = queryMapHover(fakeMap({ code: 4, min: 100, max: 200, name: "4", color: "#c18a59" }), event(395), context("elevation"));
  assert.equal(hover.lng, 35);
  assert.equal(hover.lat, 57);
  assert.equal(hover.value, undefined);
});

test("terrain requests encode coordinates and require a finite native value with the correct units", async () => {
  let request;
  const signal = new AbortController().signal;
  const value = await requestTerrainValue("/api/", { lng: 35.12345, lat: 57.56789, layer: "elevation" }, signal, async (url, options) => {
    request = { url, options };
    return { ok: true, json: async () => ({ status: "ok", value: -12.25, unit: "m", source_label: "GMTED2010 30″" }) };
  });
  assert.equal(request.url, "/api/terrain_value?latitude=57.56789&longitude=35.12345&layer=elevation");
  assert.equal(request.options.signal, signal);
  assert.equal(value.value, -12.25);
  for (const invalid of [{ value: NaN }, { value: "125" }, { unit: "degrees" }, { status: "bad" }]) {
    await assert.rejects(requestTerrainValue("/api", { lng: 35, lat: 57, layer: "elevation" }, signal, async () => ({ ok: true, json: async () => ({ status: "ok", value: 125, unit: "m", ...invalid }) })));
  }
  await assert.rejects(requestTerrainValue("/api", { lng: 35, lat: 57, layer: "slope" }, signal, async () => ({ ok: true, json: async () => ({ status: "ok", value: 91, unit: "degrees" }) })));
  await assert.rejects(requestTerrainValue("/api", { lng: 35, lat: 86, layer: "elevation" }, signal, async () => ({ ok: true, json: async () => ({ status: "ok", value: 100, unit: "m" }) })));
});

function controllerHarness(layer = "elevation") {
  const state = { current: context(layer), ready: true, shown: [], hidden: 0, pending: [], tasks: new Map(), id: 0 };
  const map = fakeMap();
  const controller = createMapHoverController({
    map, getContext: () => state.current, isReady: () => state.ready,
    show: value => state.shown.push(value), hide: () => state.hidden++,
    setTimer: callback => { const id = ++state.id; state.tasks.set(id, callback); return id; },
    clearTimer: id => state.tasks.delete(id),
    fetchValue: (target, signal) => new Promise((resolve, reject) => state.pending.push({ target, signal, resolve, reject })),
  });
  const flushTimer = () => { for (const [id, fn] of state.tasks) { state.tasks.delete(id); fn(); } };
  return { state, controller, map, flushTimer };
}
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

test("terrain hover is debounced and shows the returned cell value with Russian units", async () => {
  const { state, controller, flushTimer } = controllerHarness();
  controller.inspect(event());
  controller.inspect(event(35.1));
  assert.equal(state.tasks.size, 1);
  assert.equal(state.pending.length, 0);
  assert.equal(state.shown.at(-1).status, "loading");
  flushTimer();
  assert.equal(state.pending.length, 1);
  state.pending[0].resolve({ status: "ok", value: 123.25, unit: "m", source_label: "GMTED2010 30″" });
  await settle();
  assert.equal(state.shown.at(-1).value, "123,25 м");
  assert.equal(state.shown.at(-1).lng, 35.1);
  assert.equal(state.shown.at(-1).detail, "GMTED2010 30″ · Значение ячейки");
});

test("superseded requests abort and cannot overwrite a newer point even if fetch ignores abort", async () => {
  const { state, controller, flushTimer } = controllerHarness("slope");
  controller.inspect(event()); flushTimer();
  controller.inspect(event(36)); flushTimer();
  assert.equal(state.pending[0].signal.aborted, true);
  state.pending[1].resolve({ status: "ok", value: 0.1234, unit: "degrees" });
  await settle();
  const latest = state.shown.at(-1);
  assert.equal(latest.value, "0,12°");
  state.pending[0].resolve({ status: "ok", value: 42, unit: "degrees" });
  await settle();
  assert.equal(state.shown.at(-1), latest);
});

test("leave, layer changes, result changes, style reloads and destruction suppress pending results", async () => {
  for (const invalidate of [
    ({ controller }) => controller.reset(),
    ({ state }) => { state.current = { ...state.current, activeThematicLayer: "soil" }; },
    ({ state }) => { state.current = { ...state.current, watershed: {} }; },
    ({ state }) => { state.current = { ...state.current, elevationMap: { ...view } }; },
    ({ state }) => { state.current = { ...state.current, selecting: true }; },
    ({ state }) => { state.ready = false; },
    ({ controller }) => controller.destroy(),
  ]) {
    const harness = controllerHarness();
    harness.controller.inspect(event()); harness.flushTimer();
    invalidate(harness);
    const count = harness.state.shown.length;
    harness.state.pending[0].resolve({ status: "ok", value: 100, unit: "m" });
    await settle();
    assert.equal(harness.state.shown.length, count);
  }
});

test("categorical hover never fetches terrain and outside movement clears pending work", () => {
  const { state, controller, map, flushTimer } = controllerHarness("landcover");
  controller.inspect(event()); flushTimer();
  assert.equal(state.pending.length, 0);
  assert.equal(state.shown.at(-1).value, "Дерново-подзолистые почвы");
  map.outside = true;
  controller.inspect(event());
  assert.equal(state.hidden, 1);
});

test("NoData and server failures are explicit instead of showing an estimated band value", async () => {
  for (const response of [{ status: "no_data", value: null, unit: "m" }, { status: "unavailable", value: null, unit: "m" }, null]) {
    const { state, controller, flushTimer } = controllerHarness();
    controller.inspect(event()); flushTimer();
    if (response) state.pending[0].resolve(response); else state.pending[0].reject(new Error("offline"));
    await settle();
    assert.equal(state.shown.at(-1).status, response?.status ?? "unavailable");
    assert.equal(state.shown.at(-1).value, response?.status === "no_data" ? "Нет данных в этой точке" : "Значение недоступно");
  }
});
