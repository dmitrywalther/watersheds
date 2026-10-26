import test from "node:test";
import assert from "node:assert/strict";
import { parseCoordinates, watershedBounds, analysisError } from "./geo.js";

test("coordinates accept comma, semicolon, or whitespace separators", () => {
  for (const value of ["55.75, 37.61", " 55.75 ; 37.61 ", "55.75 37.61"]) {
    assert.deepEqual(parseCoordinates(value), { lat: 55.75, lng: 37.61 });
  }
  assert.deepEqual(parseCoordinates("-33.9, -70.65"), { lat: -33.9, lng: -70.65 });
});

test("coordinates allow Mercator boundary values and zero", () => {
  assert.deepEqual(parseCoordinates("85,180"), { lat: 85, lng: 180 });
  assert.deepEqual(parseCoordinates("-85;-180"), { lat: -85, lng: -180 });
  assert.deepEqual(parseCoordinates("0 0"), { lat: 0, lng: 0 });
});

test("coordinates reject out-of-range and non-finite values", () => {
  for (const value of ["85.001 0", "-85.001 0", "0 180.001", "0 -180.001", "NaN 0", "Infinity 0"]) {
    assert.equal(parseCoordinates(value), null, value);
  }
});

test("coordinates reject missing, ambiguous, or trailing input", () => {
  for (const value of [null, undefined, 55, "", " ", "55", "55,", ",37", "55,75,37,61", "55 37 1", "55 37 east", "55,,37"]) {
    assert.equal(parseCoordinates(value), null, String(value));
  }
});

const polygon = {
  type: "Polygon",
  coordinates: [[[36, 55], [38, 55], [38, 56], [36, 55]]],
};

test("bounds are computed from a Polygon geometry in longitude-latitude order", () => {
  assert.deepEqual(watershedBounds(polygon), [[36, 55], [38, 56]]);
});

test("bounds include every polygon in a MultiPolygon feature", () => {
  const geometry = {
    type: "MultiPolygon",
    coordinates: [polygon.coordinates, [[[-4, -5], [-2, -5], [-2, -3], [-4, -5]]]],
  };
  assert.deepEqual(watershedBounds({ type: "Feature", geometry }), [[-4, -5], [38, 56]]);
});

test("bounds combine all polygon features and ignore unrelated geometry", () => {
  assert.deepEqual(watershedBounds({
    type: "FeatureCollection",
    features: [
      { type: "Feature", geometry: polygon },
      { type: "Feature", geometry: { type: "Point", coordinates: [100, 80] } },
      { type: "Feature", geometry: { type: "Polygon", coordinates: [[[30, 50], [40, 50], [40, 60], [30, 50]]] } },
    ],
  }), [[30, 50], [40, 60]]);
});

test("bounds return null for missing or empty watershed geometry", () => {
  for (const value of [null, {}, { type: "FeatureCollection", features: [] }, { type: "Polygon", coordinates: [] }, { type: "Point", coordinates: [0, 0] }]) {
    assert.equal(watershedBounds(value), null);
  }
});

test("invalid geometry never produces unsafe map bounds", () => {
  for (const coordinates of [[[[NaN, 55], [38, 56]]], [[[36, Infinity]]], [[[181, 55]]], [[[36]]], [null]]) {
    assert.equal(watershedBounds({ type: "Polygon", coordinates }), null);
  }
});

test("API errors preserve a concise explicit JSON error", () => {
  assert.equal(analysisError({ response: { status: 400, data: { error: "Точка вне области данных." } } }), "Точка вне области данных.");
});

test("known backend coverage errors are translated into Russian", () => {
  assert.match(analysisError({ response: { status: 400, data: { error: "Pour point is not inside any basin" } } }), /вне.*покрытия/i);
});

test("HTML server responses and HTML error fields use readable fallback text", () => {
  for (const data of ["<!doctype html><h1>500 Internal Server Error</h1>", { error: "<pre>Traceback</pre>" }, { error: "x".repeat(1000) }]) {
    const message = analysisError({ response: { status: 500, data } });
    assert.match(message, /Не удалось рассчитать водосбор/);
    assert.doesNotMatch(message, /<|Traceback|xxx/);
  }
});

test("network failures explain that the calculation server is unreachable", () => {
  assert.match(analysisError({ message: "Network Error", request: {} }), /Нет соединения с сервером расчёта/);
});

test("timeouts get actionable Russian feedback", () => {
  assert.match(analysisError({ code: "ECONNABORTED", message: "timeout of 120000ms exceeded" }), /времени/i);
});
