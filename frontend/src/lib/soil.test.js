import test from "node:test";
import assert from "node:assert/strict";
import * as soilModule from "./soil.js";

test("old results without soil data remain unavailable, not empty coverage", () => {
  assert.equal(typeof soilModule.createSoilView, "function");
  const view = soilModule.createSoilView();
  assert.equal(view.status, "unavailable");
  assert.equal(view.coveragePercent, null);
  assert.equal(view.dominant, null);
  assert.deepEqual(view.categories, []);
  assert.equal(view.reason, null);
});

test("soil shares sort by prevalence without changing the response or rescaling partial coverage", () => {
  const input = {
    status: "ok", coverage_percent: 40,
    categories: [
      { code: 9, name: "Подзолистые", percent: 25 },
      { code: 12, name: "Дерново-подзолистые", percent: 75 },
    ],
  };
  const before = structuredClone(input);
  const view = soilModule.createSoilView(input);
  assert.deepEqual(input, before);
  assert.equal(view.status, "ok");
  assert.equal(view.coveragePercent, 40);
  assert.deepEqual(view.categories.map(({ code, percent }) => [code, percent]), [[12, 75], [9, 25]]);
  assert.equal(view.dominant.name, "Дерново-подзолистые");
});

test("unknown codes stay visible and equal shares have a stable code order", () => {
  const view = soilModule.createSoilView({ status: "ok", categories: [
    { code: 999, name: " ", percent: 50 },
    { code: 303, name: "Вода", percent: 50 },
  ] });
  assert.equal(view.categories[0].code, 303);
  assert.equal(view.categories[1].name, "Неизвестный код 999");
  assert.equal(view.categories[1].percent, 50);
  assert.equal(view.coveragePercent, null);
});

test("no-data and unavailable responses cannot present stale category shares", () => {
  for (const status of ["no_data", "unavailable"]) {
    const view = soilModule.createSoilView({ status, coverage_percent: 0, categories: [{ code: 1, name: "Почва", percent: 100 }] });
    assert.equal(view.status, status);
    assert.equal(view.dominant, null);
    assert.deepEqual(view.categories, []);
  }
});

test("user-facing unavailability reasons survive for explaining limits and missing files", () => {
  const view = soilModule.createSoilView({ status: "unavailable", reason: "Для этого водосбора превышен предел размера анализа." });
  assert.equal(view.reason, "Для этого водосбора превышен предел размера анализа.");
});

test("invalid shares and coverage do not produce misleading charts", () => {
  const view = soilModule.createSoilView({ status: "ok", coverage_percent: 110, categories: [
    { code: 1, name: "Почва", percent: NaN },
    { code: 2, name: "Почва", percent: -5 },
    { code: 4, name: "Почва", percent: 101 },
  ] });
  assert.equal(view.status, "no_data");
  assert.equal(view.coveragePercent, null);
  assert.equal(view.dominant, null);
});

test("rare classes rounded to zero remain visible beside the dominant class", () => {
  const view = soilModule.createSoilView({ status: "ok", categories: [
    { code: 12, name: "Редкий класс", percent: 0 },
    { code: 9, name: "Преобладающий класс", percent: 100 },
  ] });
  assert.deepEqual(view.categories.map(({ code, percent }) => [code, percent]), [[9, 100], [12, 0]]);
  assert.equal(view.dominant.code, 9);
});

test("rounded zero shares are labelled as less than the precision, not absent", () => {
  assert.equal(typeof soilModule.formatSoilShare, "function");
  assert.equal(soilModule.formatSoilShare(0), "<0,01%");
  assert.equal(soilModule.formatSoilShare(0.01), "0,01%");
  assert.equal(soilModule.formatSoilShare(75.5), "75,5%");
  assert.equal(soilModule.formatSoilShare(0, 3), "<0,03%");
});

test("more than six soil classes remain available for expanding the full list", () => {
  const categories = Array.from({ length: 8 }, (_, code) => ({ code, name: `Почва ${code}`, percent: 12.5 }));
  const view = soilModule.createSoilView({ status: "ok", coverage_percent: 100, categories });
  assert.equal(view.categories.length, 8);
  assert.equal(view.categories.reduce((sum, category) => sum + category.percent, 0), 100);
});
