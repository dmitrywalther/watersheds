import { Focus, LoaderCircle, RotateCw } from "lucide-react";

export default function DemCoverageControl({ coverage, status, visible, onShow, onRetry }) {
  return <div className="dem-coverage-control" aria-live="polite">
    {status === "loading" ? <div className="coverage-status"><LoaderCircle className="spin" size={13} /><span>Загружаем границы покрытия…</span></div>
      : status === "error" ? <div className="coverage-status coverage-status-error"><span>Границы покрытия не загрузились.</span><button onClick={onRetry}><RotateCw size={12} />Повторить</button></div>
        : coverage?.bounds ? <>
          <button className="coverage-show-button" onClick={onShow}><span><i className="legend-coverage" />Показать покрытие</span><Focus size={15} /></button>
          <p>{visible ? "Пунктир — приблизительная граница данных." : "Слой покрытия скрыт."} Водосбор должен целиком помещаться внутри.</p>
        </> : <p>Область с данными не найдена.</p>}
  </div>;
}
