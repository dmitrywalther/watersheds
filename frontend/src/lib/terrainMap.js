import { createCategoricalMapView } from "./categoricalMap.js";

const PALETTES = {
  elevation: ["#287f83", "#80ad84", "#c9cb88", "#e4c474", "#c18a59", "#79513f"],
  slope: ["#fcf0c5", "#f6c568", "#e99143", "#c95f42", "#944563", "#56365d"],
};

function colorAt(kind, position) {
  const palette = PALETTES[kind];
  const progress = position * (palette.length - 1);
  const index = Math.min(Math.floor(progress), palette.length - 2);
  const left = palette[index]; const right = palette[index + 1];
  return `#${[1, 3, 5].map(offset => {
    const start = parseInt(left.slice(offset, offset + 2), 16);
    const end = parseInt(right.slice(offset, offset + 2), 16);
    return Math.round(start + (end - start) * (progress - index)).toString(16).padStart(2, "0");
  }).join("")}`;
}

export function formatTerrainValue(value, range) {
  const span = range.max - range.min;
  const digits = span > 0 ? Math.max(0, Math.min(20, Math.ceil(-Math.log10(span / 2)) + 1)) : 6;
  if (value !== 0 && Math.abs(value) < 0.0001 && (span === 0 || span < 0.0001)) return value.toLocaleString("ru-RU", { notation: "scientific", maximumFractionDigits: 3 });
  const normalized = Math.abs(value) < 0.5 * 10 ** -digits ? 0 : value;
  return normalized.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

export function createTerrainMapView(data, kind) {
  const unit = kind === "slope" ? "degrees" : "m";
  const empty = {
    status: data?.status === "no_data" ? "no_data" : "unavailable", geometry: null,
    range: null, bands: [], approximate: false, sampleResolutionM: null,
    unit, unitLabel: unit === "m" ? "м" : "°", sourceLabel: "GMTED2010 30″",
    reason: typeof data?.reason === "string" && data.reason.trim() ? data.reason.trim() : null,
  };
  if (data?.status !== "ok") return empty;
  const invalid = { ...empty, status: "unavailable", reason: "Не удалось прочитать шкалу или геометрию слоя рельефа." };
  const range = data.range;
  if (!PALETTES[kind] || data.unit !== unit || !Number.isFinite(range?.min) || !Number.isFinite(range?.max)
    || range.min > range.max || (kind === "slope" && (range.min < 0 || range.max > 90))
    || !Array.isArray(data.bands) || !data.bands.length || data.bands.length > 24) return invalid;
  const bands = data.bands.map(band => ({ ...band })).sort((a, b) => a.min - b.min);
  const near = (a, b) => Math.abs(a - b) <= Math.max(1, Math.abs(a), Math.abs(b)) * 1e-10;
  if (new Set(bands.map(band => band.code)).size !== bands.length
    || !near(bands[0].min, range.min) || !near(bands.at(-1).max, range.max)
    || bands.some((band, index) => !Number.isInteger(band.code) || !Number.isFinite(band.min) || !Number.isFinite(band.max)
      || band.min > band.max || (bands.length > 1 && band.min === band.max)
      || (index > 0 && !near(bands[index - 1].max, band.min)))
    || (range.min === range.max && bands.length !== 1)) return invalid;
  const coloredBands = bands.map((band, index) => ({ ...band, color: colorAt(kind, bands.length > 1 ? index / (bands.length - 1) : 0.5) }));
  const byCode = new Map(coloredBands.map(band => [band.code, band]));
  if (!Array.isArray(data.geometry?.features) || data.geometry.features.some(feature => !byCode.has(feature?.properties?.code))) return invalid;
  const view = createCategoricalMapView(data, {
    geometryError: invalid.reason,
    resolveCategory: code => ({ name: String(code), color: byCode.get(code).color }),
  });
  if (view.status !== "ok") return { ...empty, status: view.status, reason: view.reason };
  return {
    ...empty, ...view, range: { min: range.min, max: range.max }, bands: coloredBands,
    sourceLabel: typeof data.source_label === "string" && data.source_label.trim() ? data.source_label.trim() : empty.sourceLabel,
    reason: empty.reason,
    geometry: { ...view.geometry, features: view.geometry.features.map(feature => ({
      ...feature, properties: { ...feature.properties, min: byCode.get(feature.properties.code).min, max: byCode.get(feature.properties.code).max },
    })) },
  };
}

export function terrainScaleTicks(view) {
  const { min, max } = view.range;
  return min === max ? [min] : [min, min + (max - min) / 2, max];
}

export function terrainScaleBackground(view) {
  const span = view.range.max - view.range.min;
  if (span === 0) return view.bands[0].color;
  const stops = view.bands.map(band => `${band.color} ${(band.min - view.range.min) / span * 100}% ${(band.max - view.range.min) / span * 100}%`);
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}
