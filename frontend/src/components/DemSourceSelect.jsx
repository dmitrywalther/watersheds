import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown, LoaderCircle, Mountain, TriangleAlert } from "lucide-react";

export function DemCatalogStatus({ loading, error, empty, onRetry }) {
  if (loading) return <p className="dem-catalog-status" role="status"><LoaderCircle className="spin" size={15} />Загружаем модели рельефа…</p>;
  if (error) return <div className="dem-catalog-error" role="alert"><TriangleAlert size={16} /><p>{error}</p><button type="button" onClick={onRetry}>Повторить загрузку</button></div>;
  if (empty) return <div className="dem-catalog-error" role="status"><p>В каталоге пока нет моделей рельефа.</p><button type="button" onClick={onRetry}>Обновить каталог</button></div>;
  return null;
}

export default function DemSourceSelect({ sources, value, onChange, loading, error, onRetry }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  const listId = useId();
  const selected = sources.find(source => source.id === value);

  useEffect(() => {
    if (!open) return;
    function outside(event) {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    }
    function escape(event) {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return <div className="dem-picker" ref={rootRef} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <button ref={buttonRef} type="button" className="dem-select" disabled={loading || !!error || !sources.length} aria-label="Модель рельефа" aria-expanded={open} aria-controls={listId} onClick={() => setOpen(current => !current)}>
      <span className="dem-icon"><Mountain size={23} strokeWidth={1.5} /></span>
      <span><strong>{selected?.label || "Выберите модель рельефа"}</strong><small>{selected?.resolution_label || "Доступные локальные данные"}</small></span>
      <ChevronDown size={15} />
    </button>
    <DemCatalogStatus loading={loading} error={error} empty={!sources.length && !loading && !error} onRetry={onRetry} />
    {open && <ul id={listId} className="dem-options" aria-label="Доступные модели рельефа">
      {sources.map(source => <li key={source.id} className={source.available ? "" : "unavailable"}>
        <button type="button" disabled={!source.available} aria-pressed={source.id === value} onClick={() => { onChange(source.id); setOpen(false); buttonRef.current?.focus(); }}>
          <span><strong>{source.label}</strong><small>{source.resolution_label}</small></span>
          {source.id === value && <Check size={16} />}
        </button>
        {source.coverage_note && <p>{source.coverage_note}</p>}
        {!source.available && <p className="dem-unavailable-reason">{source.unavailable_reason || "Данные для построения водосбора не подготовлены."}</p>}
      </li>)}
    </ul>}
    {!loading && !error && sources.length > 0 && !sources.some(source => source.available) && <div className="dem-catalog-error" role="status"><p>Нет моделей, готовых к расчёту. Причины указаны в списке.</p><button type="button" onClick={onRetry}>Обновить каталог</button></div>}
    {selected?.coverage_note && <p className="dem-coverage-note">{selected.coverage_note}</p>}
  </div>;
}
