import { createCategoricalMapView } from "./categoricalMap.js";

// Colors follow class identity in both the chart and map, independent of rank.
const CLASSES = [
  [11, "Irrigated croplands", "Орошаемые пашни", "#d5b950"],
  [14, "Rainfed croplands", "Неорошаемые пашни", "#dfc878"],
  [20, "Mosaic Croplands/Vegetation", "Пашни и естественная растительность", "#b6bb68"],
  [30, "Mosaic Vegetation/Croplands", "Растительность и пашни", "#97b775"],
  [40, "Closed to open broadleaved evergreen or semi-deciduous forest", "Вечнозелёные и полулистопадные леса", "#26734d"],
  [50, "Closed broadleaved deciduous forest", "Сомкнутые лиственные леса", "#4b8b4b"],
  [60, "Open broadleaved deciduous forest", "Разреженные лиственные леса", "#86ae68"],
  [70, "Closed needleleaved evergreen forest", "Сомкнутые вечнозелёные хвойные леса", "#29695b"],
  [90, "Open needleleaved deciduous or evergreen forest", "Разреженные хвойные леса", "#68a18a"],
  [100, "Closed to open mixed broadleaved and needleleaved forest", "Смешанные леса", "#4a9378"],
  [110, "Mosaic Forest-Shrubland/Grassland", "Леса, кустарники и луга", "#7eaa69"],
  [120, "Mosaic Grassland/Forest-Shrubland", "Луга, леса и кустарники", "#adc581"],
  [130, "Closed to open shrubland", "Кустарники", "#a3a25a"],
  [140, "Closed to open grassland", "Травянистая растительность", "#b9ca77"],
  [150, "Sparse vegetation", "Разреженная растительность", "#c5c79b"],
  [160, "Closed to open broadleaved forest regularly flooded fresh-brackish water", "Периодически затапливаемые леса", "#479486"],
  [170, "Closed broadleaved forest permanently flooded saline-brackish water", "Постоянно затопленные леса", "#397e85"],
  [180, "Closed to open vegetation regularly flooded", "Периодически затапливаемая растительность", "#8abca6"],
  [190, "Artificial areas", "Застроенные территории", "#c87565"],
  [200, "Bare areas", "Открытый грунт", "#c4ae8d"],
  [210, "Water bodies", "Водные объекты", "#4c91bd"],
  [220, "Permanent snow and ice", "Постоянные снега и ледники", "#b5d9df"],
  [230, "No data", "Нет данных", "#a9ada6"],
].map(([code, name, label, color]) => ({ code, name, label, color }));
const byCode = new Map(CLASSES.map(entry => [entry.code, entry]));
const byName = new Map(CLASSES.map(entry => [entry.name, entry]));

export function landcoverColor(code) {
  if (byCode.has(code)) return byCode.get(code).color;
  const hue = ((code * 137.508 + 24) % 360 + 360) % 360;
  return `hsl(${hue.toFixed(1)}, 43%, 49%)`;
}

export function landcoverEntries(data) {
  return Object.entries(data || {})
    .filter(([, value]) => Number.isFinite(value) && value >= 0 && value <= 100)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, value]) => {
      const known = byName.get(name);
      const numericCode = /^-?\d+$/.test(name) ? Number(name) : null;
      return { name, label: known?.label || (numericCode !== null ? `Неизвестный код ${numericCode}` : name), value, color: known?.color || (numericCode !== null ? landcoverColor(numericCode) : "#999487") };
    });
}

export function createLandcoverMapView(data, statistics) {
  return createCategoricalMapView(data, {
    geometryError: "Не удалось прочитать геометрию слоя земного покрова.",
    exclude: ({ code, name }) => code === 230 || name === "No data",
    resolveCategory(code, name) {
      const known = byCode.get(code);
      return {
        name: known?.label || name,
        color: landcoverColor(code),
        // Retain the established analysis denominator, including any No data share.
        percent: statistics?.[known?.name || name] ?? statistics?.[String(code)] ?? null,
      };
    },
  });
}
