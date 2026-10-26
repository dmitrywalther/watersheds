import { Mountain, TrendingUp } from "lucide-react";
import StatCard from "./StatCard";
export default function TerrainTab({ terrain, kind = "elevation" }) {
  const slope = kind === "slope";
  return (
    <div className="analysis-stack">
      <div className="analysis-tab-intro"><h3>{slope ? "Уклоны поверхности" : "Высоты поверхности"}</h3><p>{slope ? "Крутизна склонов" : "Высота над уровнем моря"} внутри водосборного бассейна. Источник — GMTED2010 30″.</p></div>
      {slope
        ? <StatCard icon={TrendingUp} title="Уклон поверхности" data={terrain?.slope} unit="°" color="#a7834e" />
        : <StatCard icon={Mountain} title="Высота поверхности" data={terrain?.elevation} unit="м" />}
      <p className="analysis-footnote">Маркер на шкале показывает положение среднего значения между минимумом и максимумом.</p>
    </div>
  );
}
