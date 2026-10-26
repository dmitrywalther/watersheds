export default function Tabs({ tabs, active, onChange, idPrefix = "analysis" }) {
  function handleKeyDown(event, index) {
    let nextIndex;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;
    if (nextIndex === undefined) return;
    event.preventDefault();
    onChange(tabs[nextIndex].id);
    event.currentTarget.parentElement.querySelectorAll('[role="tab"]')[nextIndex].focus();
  }
  return (
    <div className="analysis-tabs" role="tablist" aria-label="Разделы анализа">
      {tabs.map((tab, index) => {
        const Icon = tab.icon;
        return (
          <button key={tab.id} type="button" role="tab" id={`${idPrefix}-tab-${tab.id}`} aria-selected={tab.id === active} aria-controls={`${idPrefix}-panel-${tab.id}`} tabIndex={tab.id === active ? 0 : -1} onClick={() => onChange(tab.id)} onKeyDown={(event) => handleKeyDown(event, index)}>
            {Icon && <Icon size={15} strokeWidth={1.75} aria-hidden="true" />}
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
