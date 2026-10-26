import { Layers, Leaf, MapPin, Mountain, Satellite, TrendingUp } from "lucide-react";
import { LandcoverSummary } from "./LandcoverChart";
import { SoilSummary } from "./SoilChart";

const DEM_LABELS = {
  ETOPO1_1min: "ETOPO1 · 1′",
  GMTED2010_30sec: "GMTED2010 · 30″",
  MERIT_3sec: "MERIT DEM · 3″",
};
function Metric({ icon: Icon, label, value, unit }) {
  return <div className="analysis-metric">{Icon && <Icon size={17} strokeWidth={1.6} />}<span>{label}</span><strong>{value} <small>{unit}</small></strong></div>;
}
export default function OverviewTab({ info }) {
  const format = (value, digits = 0) => Number.isFinite(value) ? value.toLocaleString("ru-RU", { maximumFractionDigits: digits }) : "—";
  return (
    <div className="analysis-stack">
      <div className="analysis-area">
        <span className="analysis-eyebrow">Площадь водосбора</span>
        <div className="analysis-area-number">{format(info.area_km2, 2)}<span>км²</span></div>
        <span className="analysis-area-caption">Территория, питающая выбранный створ</span>
        <svg className="analysis-area-lines" viewBox="0 0 140 140" aria-hidden="true" fill="none"><g stroke="currentColor"><ellipse cx="123" cy="106" rx="69" ry="98" transform="rotate(36 123 106)" /><ellipse cx="123" cy="106" rx="53" ry="81" transform="rotate(36 123 106)" /><ellipse cx="123" cy="106" rx="37" ry="63" transform="rotate(36 123 106)" /><ellipse cx="123" cy="106" rx="21" ry="44" transform="rotate(36 123 106)" /></g></svg>
      </div>
      <div className="analysis-metric-grid">
        <Metric icon={Mountain} label="Средняя высота" value={format(info.terrain?.elevation?.mean)} unit="м" />
        <Metric icon={TrendingUp} label="Средний уклон" value={format(info.terrain?.slope?.mean, 1)} unit="°" />
      </div>
      <div className="analysis-detail-card">
        <div className="analysis-detail-heading"><Leaf size={16} /><span>Преобладающий покров</span></div>
        <LandcoverSummary data={info.landcover} />
      </div>
      <div className="analysis-detail-card analysis-soil-card">
        <div className="analysis-detail-heading"><Layers size={16} /><span>Преобладающая категория почвенной карты</span></div>
        <SoilSummary soil={info.soil} />
      </div>
      <div className="analysis-section-heading">Параметры исследования</div>
      <dl className="analysis-metadata">
        <div><dt><Satellite size={15} /> Модель границы</dt><dd>{info.dem_label || DEM_LABELS[info.dem] || info.dem || "Не указана"}{info.dem_resolution_label && <small className="analysis-dem-resolution">{info.dem_resolution_label}</small>}</dd></div>
        <div><dt><MapPin size={15} /> Точка стока</dt><dd className="analysis-coordinates">{format(info.lat, 4)}°, {format(info.lng, 4)}°</dd></div>
      </dl>
      <p className="analysis-footnote">Статистика рассчитана в пределах выделенной границы. Высоты и уклоны получены по GMTED2010 30″ независимо от выбранной модели границы. Доступность показателей зависит от покрытия исходных данных.</p>
    </div>
  );
}
