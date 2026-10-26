import { useId, useState } from "react";
import { ChevronDown, Mountain, TriangleAlert, TrendingUp } from "lucide-react";
import { formatTerrainValue, terrainScaleBackground, terrainScaleTicks } from "../lib/terrainMap.js";
import "./CategoricalMapLegend.css";
import "./TerrainMapLegend.css";

export default function TerrainMapLegend({ kind, view, error }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  if (view.status !== "ok") return null;
  const isSlope = kind === "slope";
  const title = isSlope ? "Уклоны" : "Высоты";
  const ticks = terrainScaleTicks(view);
  const intervalLabel = new Intl.PluralRules("ru-RU").select(view.bands.length);
  const intervals = `${view.bands.length} ${{ one: "интервал", few: "интервала", many: "интервалов", other: "интервала" }[intervalLabel]}`;
  const rangeLabel = ticks.length === 1 ? formatTerrainValue(ticks[0], view.range) : `${formatTerrainValue(view.range.min, view.range)}–${formatTerrainValue(view.range.max, view.range)}`;
  return <section className={`thematic-map-legend terrain-map-legend is-${kind} ${expanded ? "is-expanded" : ""}`} aria-label={isSlope ? "Легенда уклонов" : "Легенда высот"}>
    <button className="thematic-map-legend-heading" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(value => !value)}>
      <span className="thematic-map-legend-icon">{isSlope ? <TrendingUp size={17} aria-hidden="true" /> : <Mountain size={17} aria-hidden="true" />}</span>
      <span><strong>{title} <span className="terrain-map-unit">{isSlope ? "°" : "м"}</span></strong><small>{view.sourceLabel}{view.approximate ? " · обобщено" : ""}</small></span>
      <ChevronDown size={15} aria-hidden="true" />
    </button>
    <figure className="terrain-map-scale" aria-label={`${title}: ${rangeLabel} ${view.unitLabel}. ${intervals}.`}>
      <div className="terrain-map-colorbar" style={{ background: terrainScaleBackground(view) }} aria-hidden="true" />
      <div className={`terrain-map-ticks ${ticks.length === 1 ? "is-constant" : ""}`} aria-hidden="true">{ticks.map(value => <span key={value}>{formatTerrainValue(value, view.range)}</span>)}</div>
      <figcaption>{ticks.length === 1 ? "Одно значение в пределах водосбора" : `${intervals} · диапазон этого водосбора`}</figcaption>
    </figure>
    {error && <p className="thematic-map-legend-error" role="status"><TriangleAlert size={14} aria-hidden="true" />{error}</p>}
    <div id={id} hidden={!expanded} className="terrain-map-details">
      <p className="thematic-map-legend-note">{view.approximate ? "Обобщённый слой. " : "В пределах выделенного водосбора. "}{view.sampleResolutionM !== null && <>Шаг сетки в проекции: {view.sampleResolutionM.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} м. </>}Цвета показывают интервалы значений{view.approximate ? " обзорной сетки. Мелкие детали и пропуски данных могут быть не видны; прозрачность — по обзорной маске." : " исходных ячеек. Участки без данных прозрачны."}</p>
      <p className="thematic-map-legend-note">Источник высот и уклонов — {view.sourceLabel}, независимо от модели границы. Шкала локальная: цвета разных водосборов могут соответствовать разным значениям. Краевые ячейки и обобщение могут давать диапазон, отличный от сводки анализа.</p>
    </div>
  </section>;
}
