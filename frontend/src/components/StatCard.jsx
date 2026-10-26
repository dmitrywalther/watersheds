export default function StatCard({ icon: Icon, title, data, unit, color = "#1d6553" }) {
  const { min, mean, max } = data || {};
  const hasRange = [min, mean, max].every(Number.isFinite) && max >= min;
  const meanPct = hasRange && max > min ? Math.min(100, Math.max(0, ((mean - min) / (max - min)) * 100)) : 50;
  const format = (value) => Number.isFinite(value) ? value.toLocaleString("ru-RU", { maximumFractionDigits: 1 }) : "—";
  return (
    <div className="analysis-stat" style={{ "--stat-color": color }}>
      <div className="analysis-stat-title">{Icon && <Icon size={17} strokeWidth={1.6} />}<h4>{title}</h4></div>
      <div className="analysis-stat-value">{format(mean)} <span>{unit}</span><small>в среднем</small></div>
      {hasRange && <div className="analysis-range" aria-hidden="true"><span style={{ width: `${meanPct}%` }} /><i style={{ left: `${meanPct}%` }} /></div>}
      <div className="analysis-range-labels"><span>мин. <strong>{format(min)} {unit}</strong></span><span>макс. <strong>{format(max)} {unit}</strong></span></div>
      {!hasRange && <p className="analysis-footnote">Полный диапазон значений недоступен.</p>}
    </div>
  );
}
