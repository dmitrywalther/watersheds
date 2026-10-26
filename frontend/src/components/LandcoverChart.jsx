import { useState } from "react";
import { ChevronDown, ChevronUp, Leaf } from "lucide-react";

import { landcoverEntries } from "../lib/landcover.js";
import { formatCategoryShare } from "../lib/categoricalMap.js";

export function LandcoverSummary({ data }) {
  const dominant = landcoverEntries(data)[0];
  if (!dominant) return <p className="analysis-cover-summary">Нет данных</p>;
  return <div className="analysis-cover-summary"><strong title={dominant.name}>{dominant.label}</strong><span>{formatCategoryShare(dominant.value)}</span></div>;
}

export default function LandcoverChart({ data }) {
  const [expanded, setExpanded] = useState(false);
  const sorted = landcoverEntries(data);
  if (!sorted.length) return <div className="analysis-no-data"><Leaf size={29} strokeWidth={1.4} /><h3>Данные о покрове недоступны</h3><p>Для этого водосбора не получена классификация поверхности.</p></div>;
  const visible = expanded ? sorted : sorted.slice(0, 6);
  const rest = sorted.slice(6);
  const other = rest.reduce((sum, entry) => sum + entry.value, 0);
  return (
    <div className="analysis-stack">
      <div className="analysis-tab-intro"><h3>Мозаика ландшафта</h3><p>Распределение типов поверхности в границах водосбора.</p></div>
      <div className="analysis-cover-strip" aria-hidden="true">{sorted.map((entry) => <span key={entry.name} style={{ width: `${Math.min(100, entry.value)}%`, background: entry.color }} title={`${entry.label}: ${formatCategoryShare(entry.value)}`} />)}</div>
      <div className="analysis-cover-list">
        {visible.map((entry) => <div className="analysis-cover-row" key={entry.name}><div><span className="analysis-cover-dot" style={{ background: entry.color }} /><span title={entry.name}>{entry.label}</span><strong>{formatCategoryShare(entry.value)}</strong></div><div className="analysis-cover-track"><span style={{ width: `${Math.min(100, entry.value)}%`, background: entry.color }} /></div></div>)}
        {!expanded && rest.length > 0 && <div className="analysis-cover-other"><span>Остальные категории · {rest.length}</span><strong>{formatCategoryShare(other, rest.length)}</strong></div>}
      </div>
      {rest.length > 0 && <button className="analysis-expand" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "Показать основные" : `Все категории · ${sorted.length}`}{expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</button>}
      <p className="analysis-footnote">Доля рассчитана по ячейкам растра. Значения показаны в процентах; из-за округления сумма может отличаться от 100%.</p>
    </div>
  );
}
