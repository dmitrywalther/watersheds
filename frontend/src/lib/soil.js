import { createCategoricalMapView } from "./categoricalMap.js";
export { BASIN_SOURCE, SOIL_SOURCE, updateAnalysisLayers, formatCategoryShare as formatSoilShare } from "./categoricalMap.js";

export function createSoilView(soil) {
  const status = ["ok", "no_data", "unavailable"].includes(soil?.status) ? soil.status : "unavailable";
  const categories = status === "ok" && Array.isArray(soil.categories)
    ? soil.categories
      .filter(category => Number.isInteger(category?.code) && Number.isFinite(category.percent) && category.percent >= 0 && category.percent <= 100)
      .map(category => ({
        code: category.code,
        name: typeof category.name === "string" && category.name.trim() ? category.name.trim() : `Неизвестный код ${category.code}`,
        percent: category.percent,
      }))
      .sort((a, b) => b.percent - a.percent || a.code - b.code)
    : [];
  const coverage = soil?.coverage_percent;
  return {
    status: status === "ok" && !categories.length ? "no_data" : status,
    categories,
    dominant: categories[0] || null,
    coveragePercent: Number.isFinite(coverage) && coverage >= 0 && coverage <= 100 ? coverage : null,
    reason: typeof soil?.reason === "string" && soil.reason.trim() ? soil.reason.trim() : null,
  };
}

// Class identity, rather than prevalence, determines its color everywhere.
export function soilColor(code) {
  if (code === 307) return "#5496b8";
  if (code === 306) return "#a4cdd1";
  const hue = ((code * 137.508 + 24) % 360 + 360) % 360;
  return `hsl(${hue.toFixed(1)}, 43%, 49%)`;
}

export function createSoilMapView(data, soil) {
  const stats = new Map(createSoilView(soil).categories.map(category => [category.code, category.percent]));
  return createCategoricalMapView(data, {
    geometryError: "Не удалось прочитать геометрию почвенного слоя.",
    resolveCategory: (code, name) => ({ name, color: soilColor(code), percent: stats.get(code) ?? null }),
  });
}
