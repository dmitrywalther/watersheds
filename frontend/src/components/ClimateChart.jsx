import { CloudSun } from "lucide-react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
const format = (value) => Number.isFinite(value) ? value.toLocaleString("ru-RU", { maximumFractionDigits: 1 }) : "—";

function ClimateTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return <div className="analysis-chart-tooltip"><strong>{label}</strong>{payload.map((entry) => <div key={entry.dataKey}><span style={{ background: entry.color }} />{entry.name}: <b>{format(entry.value)} {entry.dataKey === "temperature" ? "°C" : "мм"}</b></div>)}</div>;
}

export default function ClimateChart({ temperature, rainfall }) {
  const data = MONTHS.map((month, index) => ({
    month,
    temperature: Number.isFinite(temperature?.[index]) ? temperature[index] : null,
    rainfall: Number.isFinite(rainfall?.[index]) ? rainfall[index] : null,
  }));
  const hasTemperature = data.some((item) => item.temperature !== null);
  const hasRainfall = data.some((item) => item.rainfall !== null);
  const annualRainfall = data.every((item) => item.rainfall !== null) ? data.reduce((sum, item) => sum + item.rainfall, 0) : null;
  const annualTemperature = data.every((item) => item.temperature !== null) ? data.reduce((sum, item) => sum + item.temperature, 0) / 12 : null;

  if (!hasTemperature && !hasRainfall) {
    return <div className="analysis-no-data"><CloudSun size={29} strokeWidth={1.4} /><h3>Климатические данные недоступны</h3><p>Для этого водосбора не получены значения температуры и осадков.</p></div>;
  }

  return (
    <div className="analysis-stack">
      <div className="analysis-tab-intro"><h3>Сезонный ритм</h3><p>Средние месячные значения по территории бассейна.</p></div>
      <div className="analysis-climate-summary">
        <div><span>Температура за год</span><strong>{format(annualTemperature)} <small>°C</small></strong></div>
        <div><span>Осадки за год</span><strong>{format(annualRainfall)} <small>мм</small></strong></div>
      </div>
      <div className="analysis-chart-legend"><span><i style={{ background: "#b48554" }} />Температура, °C</span><span><i style={{ background: "#74a9ad" }} />Осадки, мм</span></div>
      <div className="analysis-climate-chart" aria-label="График месячных температур и осадков">
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={data} margin={{ top: 8, right: 0, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="#e8ede6" vertical={false} />
            <XAxis dataKey="month" tick={{ fill: "#748278", fontSize: 10 }} tickLine={false} axisLine={{ stroke: "#e1e7de" }} interval={1} />
            <YAxis yAxisId="temp" tick={{ fill: "#8c795f", fontSize: 10 }} width={32} tickLine={false} axisLine={false} />
            <YAxis yAxisId="rain" orientation="right" tick={{ fill: "#62868a", fontSize: 10 }} width={32} tickLine={false} axisLine={false} />
            <Tooltip content={<ClimateTooltip />} cursor={{ fill: "#f0f3ed" }} />
            <Bar yAxisId="rain" dataKey="rainfall" name="Осадки" fill="#91b9ba" radius={[3, 3, 0, 0]} maxBarSize={12} isAnimationActive={false} />
            <Line yAxisId="temp" type="monotone" dataKey="temperature" name="Температура" stroke="#b48554" strokeWidth={2} dot={{ r: 2.5, fill: "#b48554", strokeWidth: 0 }} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <details className="analysis-data-table">
        <summary>Месячные значения</summary>
        <table><thead><tr><th scope="col">Месяц</th><th scope="col">°C</th><th scope="col">мм</th></tr></thead><tbody>{data.map((item) => <tr key={item.month}><th scope="row">{item.month}</th><td>{format(item.temperature)}</td><td>{format(item.rainfall)}</td></tr>)}</tbody></table>
      </details>
      <p className="analysis-footnote">Климатические характеристики исходных растров, не прогноз погоды. Годовые показатели доступны при наличии всех 12 месяцев.</p>
    </div>
  );
}
