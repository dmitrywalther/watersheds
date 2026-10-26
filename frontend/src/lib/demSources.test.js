import test from "node:test";
import assert from "node:assert/strict";
import { parseDemCatalog, chooseAvailableDem, createWatershedResult } from "./demSources.js";

const sources = [
  { id: "MERIT_3sec", label: "MERIT DEM", resolution_label: "3 угловые секунды", available: false, unavailable_reason: "Нет растра накопления стока" },
  { id: "ETOPO1_1min", label: "ETOPO1", resolution_label: "1 угловая минута", available: true },
  { id: "GMTED2010_7_5sec_clip", label: "GMTED2010 Clip", resolution_label: "7,5 угловых секунд", available: true, coverage_note: "Региональное покрытие" },
];

test("catalog preserves source metadata and a declared default", () => {
  assert.deepEqual(parseDemCatalog({ sources, default_source: "ETOPO1_1min" }), { sources, defaultSource: "ETOPO1_1min" });
});

test("catalog rejects malformed payloads instead of enabling unknown availability", () => {
  for (const data of [null, {}, { sources: {} }, { sources: [{ id: "x", label: "DEM", available: "false" }] }, { sources: [{ id: "", label: "DEM", available: true }] }, { sources: [sources[1], sources[1]] }]) {
    assert.throws(() => parseDemCatalog(data), /каталог/i);
  }
});

test("empty catalog is valid and cannot select a source", () => {
  assert.deepEqual(parseDemCatalog({ sources: [], default_source: null }), { sources: [], defaultSource: null });
  assert.equal(chooseAvailableDem([], "ETOPO1_1min", "ETOPO1_1min"), null);
});

test("selection retains an available user choice before the server default", () => {
  assert.equal(chooseAvailableDem(sources, "GMTED2010_7_5sec_clip", "ETOPO1_1min"), "GMTED2010_7_5sec_clip");
});

test("selection skips an unavailable preference and uses an available default", () => {
  assert.equal(chooseAvailableDem(sources, "MERIT_3sec", "GMTED2010_7_5sec_clip"), "GMTED2010_7_5sec_clip");
});

test("selection falls back to the first available model when the default is missing or unavailable", () => {
  assert.equal(chooseAvailableDem(sources, "unknown", "MERIT_3sec"), "ETOPO1_1min");
  assert.equal(chooseAvailableDem(sources.filter(source => !source.available), null, "MERIT_3sec"), null);
});

test("result records the requested model and coordinates independently of later catalog changes", () => {
  const source = { ...sources[2] };
  const point = { lat: 56.86, lng: 35.91 };
  const data = { watershed: { type: "FeatureCollection", features: [] }, analysis: { area_km2: 42, dem: "unexpected" }, source: { id: "other", label: "Other model" } };
  const result = createWatershedResult(data, point, source, 123);
  source.label = "Changed label";
  point.lat = 0;
  assert.deepEqual(result, {
    id: 123,
    watershed: data.watershed,
    source: sources[2],
    info: { area_km2: 42, lat: 56.86, lng: 35.91, dem: "GMTED2010_7_5sec_clip", dem_label: "GMTED2010 Clip", dem_resolution_label: "7,5 угловых секунд" },
  });
});
