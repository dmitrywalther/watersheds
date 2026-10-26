export const COVERAGE_SOURCE = "dem-coverage";
const COVERAGE_FILL = "dem-coverage-fill";
const COVERAGE_HALO = "dem-coverage-halo";
const COVERAGE_LINE = "dem-coverage-outline";
const EMPTY_GEOMETRY = { type: "FeatureCollection", features: [] };

function validRing(ring) {
  return Array.isArray(ring) && ring.length >= 4
    && ring.every(point => Array.isArray(point) && point.length >= 2
      && Number.isFinite(point[0]) && Number.isFinite(point[1])
      && Math.abs(point[0]) <= 180.000001 && Math.abs(point[1]) <= 90)
    && ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1];
}

function validPolygon(polygon) {
  return Array.isArray(polygon) && polygon.length > 0 && polygon.every(validRing);
}

export function parseDemCoverage(data, sourceId) {
  const features = data?.geometry?.features;
  const bounds = data?.bounds;
  const validBounds = Array.isArray(bounds) && bounds.length === 4 && bounds.every(Number.isFinite)
    && bounds[0] >= -180 && bounds[0] <= 180 && bounds[2] > bounds[0] && bounds[2] - bounds[0] <= 360.000001
    && bounds[1] >= -90 && bounds[3] <= 90 && bounds[3] > bounds[1];
  if (data?.source_id !== sourceId || data?.geometry?.type !== "FeatureCollection" || !Array.isArray(features)
    || features.some(feature => feature?.type !== "Feature" || !(feature.geometry?.type === "Polygon"
      ? validPolygon(feature.geometry.coordinates)
      : feature.geometry?.type === "MultiPolygon" && Array.isArray(feature.geometry.coordinates)
        && feature.geometry.coordinates.length > 0 && feature.geometry.coordinates.every(validPolygon)))
    || (features.length ? !validBounds : bounds !== null)
    || typeof data.approximate !== "boolean" || !Number.isFinite(data.sample_resolution_m) || data.sample_resolution_m <= 0
    || typeof data.note !== "string") {
    throw new Error("Сервер вернул некорректную границу покрытия.");
  }
  return data;
}

// A cancelled request never publishes, even when its transport ignores abort.
export function startCoverageRequest(sourceId, fetchCoverage, onState) {
  const controller = new AbortController();
  onState({ sourceId, status: "loading", coverage: null });
  const done = (async () => {
    try {
      const data = await fetchCoverage(sourceId, { signal: controller.signal });
      if (controller.signal.aborted) return;
      onState({ sourceId, status: "ready", coverage: parseDemCoverage(data, sourceId) });
    } catch {
      if (!controller.signal.aborted) onState({ sourceId, status: "error", coverage: null });
    }
  })();
  return { done, cancel: () => controller.abort() };
}

export function coverageFitBounds(coverage) {
  if (!coverage?.bounds) return null;
  const [west, south, east, north] = coverage.bounds;
  // East may exceed 180: preserve the server's shortest arc across the dateline.
  return [[west, south], [east, north]];
}

export function updateDemCoverage(map, geometry, visible) {
  const source = map.getSource(COVERAGE_SOURCE);
  if (source) source.setData(geometry || EMPTY_GEOMETRY);
  else map.addSource(COVERAGE_SOURCE, { type: "geojson", data: geometry || EMPTY_GEOMETRY });
  const before = map.getLayer("watershed-fill") ? "watershed-fill" : undefined;
  if (!map.getLayer(COVERAGE_FILL)) {
    map.addLayer({ id: COVERAGE_FILL, type: "fill", source: COVERAGE_SOURCE,
      paint: { "fill-color": "#7063ae", "fill-opacity": 0.065 } }, before);
  }
  if (!map.getLayer(COVERAGE_HALO)) {
    // Dash lengths are multiples of line width: both strokes use 6px / 4px.
    map.addLayer({ id: COVERAGE_HALO, type: "line", source: COVERAGE_SOURCE,
      paint: { "line-color": "#fbf9ff", "line-width": 4, "line-opacity": 0.75, "line-dasharray": [1.5, 1] } },
    map.getLayer(COVERAGE_LINE) ? COVERAGE_LINE : before);
  }
  if (!map.getLayer(COVERAGE_LINE)) {
    map.addLayer({ id: COVERAGE_LINE, type: "line", source: COVERAGE_SOURCE,
      paint: { "line-color": "#7160b3", "line-width": 2, "line-opacity": 0.9, "line-dasharray": [3, 2] } }, before);
  }
  for (const id of [COVERAGE_FILL, COVERAGE_HALO, COVERAGE_LINE]) map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
}
