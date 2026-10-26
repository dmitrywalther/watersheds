export const SOIL_SOURCE = "watershed-soil";
export const LANDCOVER_SOURCE = "watershed-landcover";
export const ELEVATION_SOURCE = "watershed-elevation";
export const SLOPE_SOURCE = "watershed-slope";
export const BASIN_SOURCE = "watershed-result";
const EMPTY_GEOMETRY = { type: "FeatureCollection", features: [] };

export function formatCategoryShare(percent, categoryCount = 1) {
  const value = percent === 0 ? categoryCount * 0.01 : percent;
  return `${percent === 0 ? "<" : ""}${value.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
}

function validRing(ring) {
  return Array.isArray(ring) && ring.length >= 4
    && ring.every(point => Array.isArray(point) && point.length >= 2
      && Number.isFinite(point[0]) && Number.isFinite(point[1])
      && Math.abs(point[0]) <= 180.000001 && Math.abs(point[1]) <= 90)
    && ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1];
}
const validPolygon = polygon => Array.isArray(polygon) && polygon.length > 0 && polygon.every(validRing);

// Both thematic maps use the same clipped geometry contract and validation.
export function createCategoricalMapView(data, { resolveCategory, exclude = () => false, geometryError }) {
  const empty = { status: data?.status === "no_data" ? "no_data" : "unavailable", geometry: null, categories: [], approximate: false, sampleResolutionM: null, reason: typeof data?.reason === "string" && data.reason.trim() ? data.reason.trim() : null };
  if (data?.status !== "ok") return empty;
  const features = data.geometry?.features;
  if (data.geometry?.type !== "FeatureCollection" || !Array.isArray(features)
    || features.some(feature => feature?.type !== "Feature" || !Number.isInteger(feature.properties?.code)
      || !(feature.geometry?.type === "Polygon" ? validPolygon(feature.geometry.coordinates)
        : feature.geometry?.type === "MultiPolygon" && Array.isArray(feature.geometry.coordinates)
          && feature.geometry.coordinates.length > 0 && feature.geometry.coordinates.every(validPolygon)))) {
    return { ...empty, reason: geometryError };
  }
  const visibleFeatures = features.filter(feature => !exclude(feature.properties));
  if (!visibleFeatures.length) return { ...empty, status: "no_data" };
  const categories = new Map();
  const geometry = { type: "FeatureCollection", features: visibleFeatures.map(feature => {
    const code = feature.properties.code;
    const category = resolveCategory(code, feature.properties.name);
    const name = typeof category.name === "string" && category.name.trim() ? category.name.trim() : `Неизвестный код ${code}`;
    const percent = Number.isFinite(category.percent) && category.percent >= 0 && category.percent <= 100 ? category.percent : null;
    categories.set(code, { code, name, color: category.color, percent });
    return { ...feature, properties: { code, name, color: category.color } };
  }) };
  return { status: "ok", geometry, categories: [...categories.values()].sort((a, b) => (b.percent ?? -1) - (a.percent ?? -1) || a.code - b.code), approximate: data.approximate === true, sampleResolutionM: Number.isFinite(data.sample_resolution_m) && data.sample_resolution_m > 0 ? data.sample_resolution_m : null, reason: null };
}

export function toggleThematicLayer(current, requested) {
  if (!["soil", "landcover", "elevation", "slope"].includes(requested)) return current;
  return current === requested ? null : requested;
}

// The single active layer is restored on style reload; obsolete results clear all sources.
export function updateAnalysisLayers(map, { watershed, basinVisible, soilMap, landcoverMap, elevationMap, slopeMap, soilVisible, activeThematicLayer = soilVisible ? "soil" : null }) {
  const themes = [
    { id: "soil", source: SOIL_SOURCE, view: soilMap },
    { id: "landcover", source: LANDCOVER_SOURCE, view: landcoverMap },
    { id: "elevation", source: ELEVATION_SOURCE, view: elevationMap },
    { id: "slope", source: SLOPE_SOURCE, view: slopeMap },
  ].filter(theme => theme.id === "soil" || theme.view !== undefined || map.getSource(theme.source));
  for (const theme of themes) theme.hasData = Boolean(watershed && theme.view?.status === "ok" && theme.view.geometry?.features?.length);
  for (const [id, geometry] of [[BASIN_SOURCE, watershed], ...themes.map(theme => [theme.source, theme.hasData ? theme.view.geometry : null])]) {
    const source = map.getSource(id);
    if (source) source.setData(geometry || EMPTY_GEOMETRY);
    // Common clipped edges must not be simplified independently.
    else map.addSource(id, { type: "geojson", data: geometry || EMPTY_GEOMETRY, tolerance: 0 });
  }
  if (!map.getLayer("watershed-fill")) map.addLayer({ id: "watershed-fill", type: "fill", source: BASIN_SOURCE, paint: { "fill-color": "#27766b", "fill-opacity": 0.18 } });
  if (!map.getLayer("watershed-outline")) map.addLayer({ id: "watershed-outline", type: "line", source: BASIN_SOURCE, paint: { "line-color": "#216b5c", "line-width": 2.5 } });
  for (const theme of themes) {
    const layerId = `${theme.source}-fill`;
    if (!map.getLayer(layerId)) map.addLayer({ id: layerId, type: "fill", source: theme.source, paint: { "fill-color": ["get", "color"], "fill-opacity": 0.78, "fill-antialias": false } }, "watershed-outline");
    map.setLayoutProperty(layerId, "visibility", theme.hasData && activeThematicLayer === theme.id ? "visible" : "none");
  }
  map.setPaintProperty("watershed-fill", "fill-opacity", themes.some(theme => theme.hasData && activeThematicLayer === theme.id) ? 0 : 0.18);
  for (const id of ["watershed-fill", "watershed-outline"]) map.setLayoutProperty(id, "visibility", basinVisible ? "visible" : "none");
}
