import { useId } from "react";
import { ArrowDownToLine, ChartNoAxesCombined, CloudSun, Layers, Leaf, Mountain, MousePointer2, TrendingUp } from "lucide-react";
import Tabs from "./Tabs";
import OverviewTab from "./OverviewTab";
import TerrainTab from "./TerrainTab";
import ClimateChart from "./ClimateChart";
import LandcoverChart from "./LandcoverChart";
import SoilChart from "./SoilChart";
import "./analysis.css";

const TABS = [
  { id: "overview", label: "Обзор", icon: ChartNoAxesCombined },
  { id: "elevation", label: "Высоты", icon: Mountain },
  { id: "slope", label: "Уклоны", icon: TrendingUp },
  { id: "climate", label: "Климат", icon: CloudSun },
  { id: "landcover", label: "Покров", icon: Leaf },
  { id: "soil", label: "Почвы", icon: Layers },
];

function EmptyAnalysis() {
  return (
    <div className="analysis-empty">
      <div className="analysis-empty-art" aria-hidden="true">
        <svg viewBox="0 0 300 166" fill="none">
          <g stroke="currentColor" strokeWidth="1">
            <path d="M-24 115C27 169 75 148 101 122S132 51 164 41S226 63 271 37S328-9 337 25" />
            <path d="M-19 96C23 153 76 132 93 109S116 44 151 30S224 52 267 24S321-15 338 9" />
            <path d="M-15 78C26 134 62 122 82 97S101 37 141 21S213 40 257 12S315-29 339-7" />
            <path d="M-22 58C24 115 52 112 70 86S88 27 128 13S201 28 246 0" />
            <path d="M-15 38C26 91 33 107 54 77S74 20 114 5S184 14 234-12" />
            <path d="M-25 133C27 184 86 164 113 138S145 64 177 54S238 77 283 51S341 6 348 40" />
            <path d="M23 175C74 192 108 173 131 146S160 79 190 69S248 91 295 66S356 24 364 57" />
            <path d="M77 187C127 183 143 159 152 137S176 90 205 85S260 104 307 82" />
            <path d="M117 186C153 168 167 130 181 117S211 99 231 106S274 124 310 101" />
            <path d="M153 185C178 160 182 133 203 124S236 136 264 135S297 119 321 115" />
          </g>
          <path d="M109 0C93 36 122 43 119 71S137 101 151 103S167 117 172 140S177 158 186 170" stroke="#6faaa2" strokeWidth="2" strokeDasharray="3 5" />
          <circle cx="151" cy="103" r="18" fill="#e7eee5" stroke="#fff" strokeWidth="3" />
          <circle cx="151" cy="103" r="6" fill="#1d6553" stroke="#fff" strokeWidth="2" />
        </svg>
        <span className="analysis-art-tag"><MousePointer2 size={12} /> Ваша первая точка</span>
      </div>
      <div className="analysis-empty-copy">
        <span className="analysis-eyebrow">От точки — к целому бассейну</span>
        <h2>Здесь начинается<br />исследование</h2>
        <p>Выберите точку на реке. Мы построим границу водосбора и соберём характеристики территории.</p>
      </div>
      <ol className="analysis-steps">
        <li><span>01</span><div><strong>Найдите свою территорию</strong><p>Приблизьте карту к нужному участку реки.</p></div></li>
        <li><span>02</span><div><strong>Укажите замыкающий створ</strong><p>Нажмите на точку, в которую стекает вода.</p></div></li>
        <li><span>03</span><div><strong>Исследуйте водосбор</strong><p>Сравните рельеф, климат, покров и почвы.</p></div></li>
      </ol>
      <div className="analysis-empty-note"><Mountain size={15} /><span>Точность границы зависит от выбранной цифровой модели рельефа.</span></div>
    </div>
  );
}

export default function InfoPanel({ info, onExport, active = "overview", onSectionChange, mapViews = {}, mapErrors = {} }) {
  const id = useId();
  if (!info) return <EmptyAnalysis />;
  const thematic = ["elevation", "slope", "landcover", "soil"].includes(active);
  const mapView = mapViews[active];
  const mapNote = mapErrors[active] || (mapView?.status === "ok"
    ? "Слой включён на карте. Наведите указатель внутри водосбора, чтобы узнать значение. «Обзор» или «Климат» отключают цветной слой."
    : mapView?.reason || "Карта этого показателя недоступна для выбранного водосбора.");
  return (
    <section className="analysis-panel" aria-label="Характеристики водосбора">
      <Tabs tabs={TABS} active={active} onChange={onSectionChange} idPrefix={id} />
      <div className="analysis-content" id={`${id}-panel-${active}`} role="tabpanel" aria-labelledby={`${id}-tab-${active}`} tabIndex={0}>
        {thematic && <p className={`analysis-map-note ${mapErrors[active] || mapView?.status !== "ok" ? "unavailable" : ""}`} role="status">{mapNote}</p>}
        {active === "overview" && <OverviewTab info={info} />}
        {["elevation", "slope"].includes(active) && <TerrainTab terrain={info.terrain} kind={active} />}
        {active === "climate" && <ClimateChart temperature={info.climate?.temperature} rainfall={info.climate?.rainfall} />}
        {active === "landcover" && <LandcoverChart data={info.landcover} />}
        {active === "soil" && <SoilChart soil={info.soil} />}
      </div>
      {onExport && <button className="analysis-export" type="button" onClick={onExport}><ArrowDownToLine size={15} /> Скачать границу <span>GeoJSON</span></button>}
    </section>
  );
}
