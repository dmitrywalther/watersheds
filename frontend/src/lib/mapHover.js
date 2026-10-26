import { SOIL_SOURCE, LANDCOVER_SOURCE, ELEVATION_SOURCE, SLOPE_SOURCE } from "./categoricalMap.js";

const THEMES = {
  soil: { source: SOIL_SOURCE, title: "Почвы", view: "soilMap" },
  landcover: { source: LANDCOVER_SOURCE, title: "Земной покров", view: "landcoverMap" },
  elevation: { source: ELEVATION_SOURCE, title: "Высота", view: "elevationMap", unit: "m" },
  slope: { source: SLOPE_SOURCE, title: "Уклон", view: "slopeMap", unit: "degrees" },
};
const cleanText = value => typeof value === "string" && value.trim() ? value.trim() : null;
const normalizedLongitude = lng => lng >= -180 && lng <= 180 ? lng : ((lng + 180) % 360 + 360) % 360 - 180;

// Query the clipped, visible fill only: holes, NoData and points outside the basin have no hit.
export function queryMapHover(map, event, context) {
  const layer = context.activeThematicLayer;
  const theme = THEMES[layer];
  const lat = event?.lngLat?.lat; const rawLng = event?.lngLat?.lng;
  if (!theme || !context.watershed || context.selecting || context[theme.view]?.status !== "ok"
    || !Number.isFinite(lat) || !Number.isFinite(rawLng) || Math.abs(lat) > 85
    || !Number.isFinite(event?.point?.x) || !Number.isFinite(event?.point?.y)) return null;
  const layerId = `${theme.source}-fill`;
  if (!map.getLayer(layerId) || map.getLayoutProperty(layerId, "visibility") === "none") return null;
  let feature;
  try { [feature] = map.queryRenderedFeatures(event.point, { layers: [layerId] }); }
  catch { return null; } // A style can be replaced between the visibility check and the query.
  if (!feature) return null;
  const properties = feature.properties || {};
  const target = {
    layer, title: theme.title, lng: normalizedLongitude(rawLng), lat,
    color: /^#[0-9a-f]{6}$/i.test(properties.color) ? properties.color : "#77816d",
  };
  if (!theme.unit) {
    const code = Number.isInteger(properties.code) ? properties.code : null;
    target.value = cleanText(properties.name) || (code === null ? "Неизвестный класс" : `Неизвестный класс (код ${code})`);
    target.detail = code === null ? null : `Код класса: ${code}`;
  }
  return target;
}

export async function requestTerrainValue(baseUrl, target, signal, fetcher = fetch) {
  const expectedUnit = THEMES[target.layer]?.unit;
  if (!expectedUnit || !Number.isFinite(target.lat) || Math.abs(target.lat) > 85
    || !Number.isFinite(target.lng) || Math.abs(target.lng) > 180) throw new Error("Invalid terrain location");
  const params = new URLSearchParams({ latitude: target.lat, longitude: target.lng, layer: target.layer });
  const response = await fetcher(`${baseUrl.replace(/\/$/, "")}/terrain_value?${params}`, { signal });
  if (!response.ok) throw new Error("Terrain request failed");
  const value = await response.json();
  if (!["ok", "no_data", "unavailable"].includes(value?.status) || value.unit !== expectedUnit
    || (value.status === "ok" && (!Number.isFinite(value.value)
      || (target.layer === "slope" && (value.value < 0 || value.value > 90))))) throw new Error("Invalid terrain response");
  return value;
}

function terrainContent(target, response) {
  if (response.status !== "ok") return {
    ...target, status: response.status,
    value: response.status === "no_data" ? "Нет данных в этой точке" : "Значение недоступно",
    detail: cleanText(response.reason) || "Значение исходной ячейки растра",
  };
  const value = Object.is(response.value, -0) ? 0 : response.value;
  const formatted = value !== 0 && Math.abs(value) < 0.01
    ? value.toLocaleString("ru-RU", { maximumSignificantDigits: 2 })
    : value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  return {
    ...target, status: "ok", value: `${formatted}${target.layer === "slope" ? "°" : " м"}`,
    detail: `${cleanText(response.source_label) || "GMTED2010 30″"} · Значение ячейки`,
  };
}

// Each movement invalidates both the timer and the request. Generation checks also
// guard transports that finish after abort, so an old point can never re-open a popup.
export function createMapHoverController({ map, getContext, isReady, show, hide, fetchValue, delay = 130, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let generation = 0; let timer = null; let request = null; let disposed = false;
  const cancel = () => {
    generation++;
    if (timer !== null) clearTimer(timer);
    timer = null;
    request?.abort();
    request = null;
  };
  const reset = () => { cancel(); hide(); };
  const inspect = event => {
    if (disposed) return;
    const context = getContext();
    const target = isReady() ? queryMapHover(map, event, context) : null;
    if (!target) { reset(); return; }
    cancel();
    if (!THEMES[target.layer].unit) { show({ ...target, status: "ok" }); return; }
    const token = generation;
    const stillCurrent = () => {
      const now = getContext();
      return !disposed && token === generation && isReady() && !now.selecting
        && now.activeThematicLayer === target.layer && now.watershed === context.watershed
        && now[THEMES[target.layer].view] === context[THEMES[target.layer].view]
        && now.basemap === context.basemap && now.point === context.point && now.coverage === context.coverage;
    };
    show({ ...target, status: "loading", value: "Загрузка значения…", detail: "Значение исходной ячейки растра" });
    timer = setTimer(async () => {
      timer = null;
      if (!stillCurrent()) return;
      const abort = new AbortController();
      request = abort;
      try {
        const response = await fetchValue(target, abort.signal);
        if (stillCurrent() && !abort.signal.aborted) show(terrainContent(target, response));
      } catch {
        if (stillCurrent() && !abort.signal.aborted) show(terrainContent(target, { status: "unavailable", reason: "Не удалось загрузить значение. Попробуйте навести ещё раз." }));
      } finally {
        if (request === abort) request = null;
      }
    }, delay);
  };
  return { inspect, reset, destroy: () => { disposed = true; reset(); } };
}
