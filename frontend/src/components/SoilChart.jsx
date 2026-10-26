import { useId, useState } from "react";
import { ChevronDown, ChevronUp, Layers } from "lucide-react";
import { createSoilView, formatSoilShare, soilColor } from "../lib/soil.js";

const format = value => value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });

export function SoilSummary({ soil }) {
  const { status, dominant, coveragePercent } = createSoilView(soil);
  if (!dominant) return <p className="analysis-cover-summary">{status === "no_data" ? "Нет данных для этого водосбора" : "Анализ почв недоступен"}</p>;
  return (
    <>
      <div className="analysis-cover-summary analysis-soil-summary"><strong>{dominant.name}</strong><span>{formatSoilShare(dominant.percent)}</span></div>
      <p className="analysis-soil-summary-note">Доля среди ячеек с данными{coveragePercent !== null && <> · покрытие {format(coveragePercent)}%</>}</p>
    </>
  );
}

export default function SoilChart({ soil }) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const { status, categories, coveragePercent, reason } = createSoilView(soil);
  if (status !== "ok") {
    return (
      <div className="analysis-no-data analysis-soil-empty">
        <Layers size={29} strokeWidth={1.4} aria-hidden="true" />
        <h3>{status === "no_data" ? "Для этой территории нет данных о почвах" : "Анализ почв пока недоступен"}</h3>
        <p>{reason || (status === "no_data" ? "В границах водосбора не найдено ячеек с почвенной классификацией." : "В этом результате нет почвенной статистики. Постройте водосбор заново после подключения почвенной карты.")}</p>
        {status === "no_data" && coveragePercent !== null && <p className="analysis-soil-empty-coverage">Покрытие данными: {format(coveragePercent)}%</p>}
      </div>
    );
  }
  const visible = expanded ? categories : categories.slice(0, 6);
  const rest = categories.slice(6);
  const restPercent = rest.reduce((sum, entry) => sum + entry.percent, 0);
  return (
    <div className="analysis-stack analysis-soil">
      <div className="analysis-tab-intro"><h3>Почвенный состав</h3><p>Категории почвенной карты в границах водосбора, от преобладающих к редким.</p></div>
      <div className="analysis-soil-coverage">
        <div><span><Layers size={15} aria-hidden="true" /> Покрытие данными</span><strong>{coveragePercent !== null ? <>{format(coveragePercent)}<small>%</small></> : "Не определено"}</strong></div>
        {coveragePercent !== null && <div className="analysis-soil-coverage-track" aria-hidden="true"><span style={{ width: `${coveragePercent}%` }} /></div>}
        <p>{coveragePercent !== null ? "Доля ячеек водосбора, для которых есть почвенная классификация." : "Доля водосбора с почвенными данными неизвестна."}{coveragePercent !== null && coveragePercent < 100 && " Участки без данных не входят в доли категорий ниже."}</p>
      </div>
      <div className="analysis-soil-list-heading"><span>Категория карты</span><span>Среди данных</span></div>
      <div className="analysis-cover-strip analysis-soil-strip" aria-hidden="true">{categories.map(entry => <span key={entry.code} style={{ width: `${entry.percent}%`, background: soilColor(entry.code) }} />)}</div>
      <div className="analysis-cover-list" id={listId}>
        {visible.map(entry => (
          <div className="analysis-cover-row analysis-soil-row" key={entry.code}>
            <div><span className="analysis-cover-dot" style={{ background: soilColor(entry.code) }} /><span>{entry.name}<small className="analysis-soil-code">Код {entry.code}</small></span><strong>{formatSoilShare(entry.percent)}</strong></div>
            <div className="analysis-cover-track" aria-hidden="true"><span style={{ width: `${entry.percent}%`, background: soilColor(entry.code) }} /></div>
          </div>
        ))}
        {!expanded && rest.length > 0 && <div className="analysis-cover-other"><span>Остальные категории · {rest.length}</span><strong>{formatSoilShare(restPercent, rest.length)}</strong></div>}
      </div>
      {rest.length > 0 && <button className="analysis-expand analysis-soil-expand" type="button" aria-expanded={expanded} aria-controls={listId} onClick={() => setExpanded(value => !value)}>{expanded ? "Показать основные" : `Все категории · ${categories.length}`}{expanded ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}</button>}
      <p className="analysis-footnote">Доли рассчитаны среди ячеек с данными, а не от всей площади водосбора. Карта также может содержать воду, пески и другие категории. Из-за округления сумма может отличаться от 100%.</p>
    </div>
  );
}
