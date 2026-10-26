export function parseDemCatalog(data) {
  const sources = data?.sources;
  if (!Array.isArray(sources) || sources.some(source => (
    !source || typeof source.id !== "string" || !source.id.trim()
    || typeof source.label !== "string" || !source.label.trim()
    || typeof source.available !== "boolean"
  )) || new Set(sources.map(source => source.id)).size !== sources.length) {
    throw new Error("Сервер вернул некорректный каталог моделей рельефа.");
  }
  return { sources, defaultSource: typeof data.default_source === "string" ? data.default_source : null };
}

export function chooseAvailableDem(sources, preferredId, defaultId) {
  const available = sources.filter(source => source.available);
  return available.find(source => source.id === preferredId)?.id
    || available.find(source => source.id === defaultId)?.id
    || available[0]?.id
    || null;
}

export function createWatershedResult(data, point, source, id) {
  return {
    id,
    watershed: data.watershed,
    source: { ...source },
    info: { ...data.analysis, ...point, dem: source.id, dem_label: source.label, dem_resolution_label: source.resolution_label },
  };
}
