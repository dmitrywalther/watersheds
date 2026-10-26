export function parseCoordinates(value) {
  if (typeof value !== "string") return null;

  const match = value.trim().match(/^([+-]?(?:\d+(?:\.\d+)?|\.\d+))(?:\s*[,;]\s*|\s+)([+-]?(?:\d+(?:\.\d+)?|\.\d+))$/);
  if (!match) return null;

  const lat = Number(match[1]);
  const lng = Number(match[2]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 85 || Math.abs(lng) > 180) return null;

  return { lat, lng };
}

export function watershedBounds(geojson) {
  const geometries = geojson?.type === "FeatureCollection"
    ? (Array.isArray(geojson.features) ? geojson.features.map((feature) => feature?.geometry) : [])
    : [geojson?.type === "Feature" ? geojson.geometry : geojson];

  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;

  for (const geometry of geometries) {
    if (geometry?.type !== "Polygon" && geometry?.type !== "MultiPolygon") continue;
    const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
    if (!Array.isArray(polygons)) return null;

    for (const polygon of polygons) {
      if (!Array.isArray(polygon)) return null;
      for (const ring of polygon) {
        if (!Array.isArray(ring)) return null;
        for (const point of ring) {
          if (!Array.isArray(point)) return null;
          const [lng, lat] = point;
          if (!Number.isFinite(lng) || !Number.isFinite(lat) || Math.abs(lng) > 180 || Math.abs(lat) > 90) return null;
          west = Math.min(west, lng);
          south = Math.min(south, lat);
          east = Math.max(east, lng);
          north = Math.max(north, lat);
        }
      }
    }
  }

  return Number.isFinite(west) ? [[west, south], [east, north]] : null;
}

export function analysisError(error) {
  if (error?.code === "ECONNABORTED" || error?.code === "ETIMEDOUT") {
    return "Расчёт занял слишком много времени. Попробуйте ещё раз или выберите другую точку.";
  }

  if (!error?.response) {
    return "Нет соединения с сервером расчёта. Проверьте, запущен ли сервер, и попробуйте ещё раз.";
  }

  const detail = error.response.data?.error;
  if (typeof detail === "string" && detail.trim() && detail.length <= 240 && !/[<>]/.test(detail)) {
    if (/not inside any basin/i.test(detail)) {
      return "Точка находится вне покрытия доступных данных. Выберите другую точку.";
    }
    return detail.trim();
  }

  return "Не удалось рассчитать водосбор. Проверьте, входит ли точка в область данных, и попробуйте ещё раз.";
}
