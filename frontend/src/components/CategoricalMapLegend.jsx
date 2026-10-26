import { useId, useState } from "react";
import { ChevronDown, Layers, Leaf, TriangleAlert } from "lucide-react";
import { formatCategoryShare } from "../lib/categoricalMap.js";
import "./CategoricalMapLegend.css";

export default function CategoricalMapLegend({ kind, view, error }) {
  const [expanded, setExpanded] = useState(() => typeof window !== "undefined" && window.matchMedia("(min-width: 1100px)").matches);
  const id = useId();
  if (view.status !== "ok") return null;
  const isLandcover = kind === "landcover";
  return <section className={`thematic-map-legend ${isLandcover ? "is-landcover" : ""} ${expanded ? "is-expanded" : ""}`} aria-label={isLandcover ? "Легенда земного покрова" : "Легенда почвенной карты"}>
    <button className="thematic-map-legend-heading" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(value => !value)}>
      <span className="thematic-map-legend-icon">{isLandcover ? <Leaf size={16} aria-hidden="true" /> : <Layers size={16} aria-hidden="true" />}</span>
      <span><strong>{isLandcover ? "Земной покров" : "Почвы"}</strong><small>Классы карты · {view.categories.length}{view.approximate ? " · обобщено" : ""}</small></span>
      <span className="thematic-map-legend-colors" aria-hidden="true">{view.categories.slice(0, 4).map(entry => <i key={entry.code} style={{ background: entry.color }} />)}</span>
      <ChevronDown size={15} aria-hidden="true" />
    </button>
    <div id={id} hidden={!expanded}>
      {error && <p className="thematic-map-legend-error" role="status"><TriangleAlert size={14} aria-hidden="true" />{error}</p>}
      <ul className="thematic-map-legend-list">{view.categories.map(entry => <li key={entry.code}>
        <i style={{ background: entry.color }} aria-hidden="true" />
        <span>{entry.name}<small>Код {entry.code}</small></span>
        {entry.percent !== null && <strong>{formatCategoryShare(entry.percent)}</strong>}
      </li>)}</ul>
      <p className="thematic-map-legend-note">{view.approximate ? <>Обобщённый слой{view.sampleResolutionM !== null && <> · шаг сетки в проекции {view.sampleResolutionM.toLocaleString("ru-RU")} м</>}. Мелкие классы и пропуски данных могут быть не видны. </> : "В пределах выделенного водосбора. "}{isLandcover ? "Доли — из анализа «Покров», без пересчёта по обзорному слою. " : "Доли — по исходным ячейкам с данными. "}{view.approximate ? "Прозрачность — по обзорной маске данных." : "Участки без данных прозрачны."}</p>
    </div>
  </section>;
}
