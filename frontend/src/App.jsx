import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import axios from "axios";
import { ArrowDownToLine, ArrowRight, Check, ChevronDown, ChevronRight, CircleHelp, Database, Focus, History, Layers, Leaf, LoaderCircle, Map, MapPin, Mountain, MousePointer2, Plus, Minus, Search, SlidersHorizontal, Waves, X, CloudRain, Compass, TriangleAlert } from "lucide-react";
import InfoPanel from "./components/InfoPanel";
import MapCanvas from "./components/MapCanvas";
import DemSourceSelect, { DemCatalogStatus } from "./components/DemSourceSelect";
import DemCoverageControl from "./components/DemCoverageControl";
import CategoricalMapLegend from "./components/CategoricalMapLegend";
import TerrainMapLegend from "./components/TerrainMapLegend";
import { analysisError, parseCoordinates } from "./lib/geo";
import { chooseAvailableDem, createWatershedResult, parseDemCatalog } from "./lib/demSources";
import { startCoverageRequest } from "./lib/demCoverage";
import { createSoilMapView } from "./lib/soil.js";
import { createLandcoverMapView } from "./lib/landcover.js";
import { INITIAL_ANALYSIS_SECTION, analysisSelectionReducer, getActiveThematicLayer } from "./lib/analysisSelection.js";
import { createTerrainMapView } from "./lib/terrainMap.js";
import "./App.css";

const PLACES = [
  { name: "Москва", detail: "Река Москва", lat: 55.75, lng: 37.61, zoom: 10 },
  { name: "Верхняя Волга", detail: "Тверь · река Волга", lat: 56.86, lng: 35.91, zoom: 9 },
  { name: "Прикамье", detail: "Пермь · река Кама", lat: 58.01, lng: 56.23, zoom: 9 },
  { name: "Санкт-Петербург", detail: "Река Нева", lat: 59.94, lng: 30.31, zoom: 10 },
  { name: "Новосибирск", detail: "Река Обь", lat: 55.02, lng: 82.92, zoom: 9 },
];

function Logo() {
  return <div className="brand"><span className="brand-icon"><Waves size={23} strokeWidth={1.8} /></span><span className="brand-name">watershed<span className="brand-dot">.</span></span><span className="brand-lab">LAB</span></div>;
}

function Dialog({ type, onClose, sources, sourcesLoading, sourcesError, onRetrySources }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} className="workspace-dialog" aria-labelledby="dialog-title" onCancel={onClose} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="dialog-inner">
      <button className="icon-button dialog-close" aria-label="Закрыть окно" onClick={onClose}><X size={20} /></button>
      <span className="dialog-symbol">{type === "sources" ? <Database size={26} /> : type === "future" ? <Waves size={26} /> : <Compass size={26} />}</span>
      <span className="eyebrow">WATERSHED LAB</span>
      {type === "sources" ? <>
        <h2 id="dialog-title">Рельеф — основа анализа</h2><p>Для построения водосбора нужны подготовленные модели направления и накопления стока.</p>
        <DemCatalogStatus loading={sourcesLoading} error={sourcesError} empty={!sources.length && !sourcesLoading && !sourcesError} onRetry={onRetrySources} />
        {sources.length > 0 && <div className="source-table">{sources.map(source => <div key={source.id}><div className="source-description"><strong>{source.label}<small>{source.resolution_label}</small></strong>{Number.isFinite(source.grid_resolution_m) && <p>Шаг сетки в EPSG:3395: {source.grid_resolution_m.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} м</p>}{source.coverage_note && <p>{source.coverage_note}</p>}{!source.available && <p className="dem-unavailable-reason">{source.unavailable_reason || "Данные для построения водосбора не подготовлены."}</p>}</div><span className={source.available ? "available" : ""}>{source.available && <i />}{source.available ? "Готова" : "Недоступна"}</span></div>)}</div>}
        <p className="dialog-note">Каталог показывает модели с локальными данными и их готовность к построению водосбора. Покрытие каждой модели ограничено подготовленными растрами.</p>
        <div className="notice"><Mountain size={18} /><span>Выбранная модель определяет границу водосбора. Высоты и уклоны по-прежнему анализируются по общим данным GMTED2010 30″ независимо от этого выбора.</span></div>
      </> : type === "future" ? <>
        <h2 id="dialog-title">От водосбора — к сценариям</h2><p>Следующий шаг — исследовать возможное затопление и объекты в зоне риска.</p>
        <div className="future-step"><span>01</span><div><h3>Сценарии подъёма воды</h3><p>Потенциальная зона затопления при заданном уровне воды. В проекте уже есть экспериментальный расчёт HAND.</p></div></div>
        <div className="future-step"><span>02</span><div><h3>Оценка последствий</h3><p>Здания и население в зоне затопления, затем — оценка потенциального ущерба.</p></div></div>
        <div className="notice"><TriangleAlert size={18} /><span>Сценарная оценка не является прогнозом наводнения. Этот модуль пока не подключён к интерфейсу.</span></div>
      </> : <>
        <h2 id="dialog-title">У каждой реки есть история.<br />Начните с её водосбора.</h2><p>Картографическая лаборатория для выделения и исследования водосборных бассейнов на основе цифровых моделей рельефа.</p>
        <ol className="help-steps"><li><strong>Найдите территорию</strong><span>Перемещайте карту или введите координаты в строке поиска. Также доступны несколько мест для быстрого перехода.</span></li><li><strong>Выберите точку на реке</strong><span>В режиме выбора нажмите на карту или задайте широту и долготу в панели слева.</span></li><li><strong>Постройте водосбор</strong><span>Получите границу, площадь, характеристики рельефа, климат, земной покров и почвенный состав. Границу можно скачать в GeoJSON.</span></li></ol>
        <p className="dialog-note">Исследовательский проект на основе магистерской диссертации. Результаты зависят от разрешения и качества исходных данных; предназначены для исследовательского анализа.</p>
      </>}
      <button className="primary-button dialog-done" onClick={onClose}>Вернуться к карте <ArrowRight size={16} /></button>
    </div>
  </dialog>;
}

export default function App() {
  const [point, setPoint] = useState(null);
  const [coordinates, setCoordinates] = useState({ lat: "", lng: "" });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [demCatalog, setDemCatalog] = useState({ sources: [], defaultSource: null });
  const [selectedDemId, setSelectedDemId] = useState(null);
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [sourcesError, setSourcesError] = useState("");
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [coverageState, setCoverageState] = useState({ sourceId: null, status: "idle", coverage: null });
  const [coverageAttempt, setCoverageAttempt] = useState(0);
  const [coverageVisible, setCoverageVisible] = useState(true);
  const [exploringCoverage, setExploringCoverage] = useState(false);
  const [tab, setTab] = useState("setup");
  const [basemap, setBasemap] = useState("topo");
  const [visible, setVisible] = useState(true);
  const [analysisSection, dispatchAnalysisSelection] = useReducer(analysisSelectionReducer, INITIAL_ANALYSIS_SECTION);
  const [soilMapError, setSoilMapError] = useState(null);
  const [landcoverMapError, setLandcoverMapError] = useState(null);
  const [elevationMapError, setElevationMapError] = useState(null);
  const [slopeMapError, setSlopeMapError] = useState(null);
  const [selecting, setSelecting] = useState(true);
  const [layersOpen, setLayersOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [history, setHistory] = useState([]);
  const [mobilePanel, setMobilePanel] = useState(false);
  const [search, setSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [mapError, setMapError] = useState(null);
  const [position, setPosition] = useState({ lat: 56.35, lng: 37.3, zoom: 6.6 });
  const [toast, setToast] = useState("");
  const mapApi = useRef(null);
  const controller = useRef(null);
  const requestId = useRef(0);
  const searchRef = useRef(null);
  const sidebarScrollRef = useRef(null);
  const selectedSource = demCatalog.sources.find(source => source.id === selectedDemId);
  const coverageSourceId = selectedSource?.available ? selectedDemId : null;
  const coverage = coverageState.sourceId === coverageSourceId ? coverageState.coverage : null;
  const coverageStatus = !coverageSourceId ? "idle" : coverageState.sourceId === coverageSourceId ? coverageState.status : "loading";
  const soilMap = useMemo(() => createSoilMapView(result?.info.soil_map, result?.info.soil), [result]);
  const landcoverMap = useMemo(() => createLandcoverMapView(result?.info.landcover_map, result?.info.landcover), [result]);
  const elevationMap = useMemo(() => createTerrainMapView(result?.info.elevation_map, "elevation"), [result]);
  const slopeMap = useMemo(() => createTerrainMapView(result?.info.slope_map, "slope"), [result]);
  const thematicViews = { soil: soilMap, landcover: landcoverMap, elevation: elevationMap, slope: slopeMap };
  const thematicErrors = { soil: soilMapError, landcover: landcoverMapError, elevation: elevationMapError, slope: slopeMapError };
  const activeThematicLayer = getActiveThematicLayer(analysisSection, thematicViews);
  const activeMap = thematicViews[activeThematicLayer];
  const activeMapError = thematicErrors[activeThematicLayer];

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    const catalogController = new AbortController();
    async function loadSources() {
      try {
        const response = await axios.get(`${import.meta.env.VITE_API_URL || "/api"}/dem_sources`, { signal: catalogController.signal, timeout: 30000 });
        const catalog = parseDemCatalog(response.data);
        if (catalogController.signal.aborted) return;
        setDemCatalog(catalog);
        setSelectedDemId(current => chooseAvailableDem(catalog.sources, current, catalog.defaultSource));
      } catch (err) {
        if (!catalogController.signal.aborted && !axios.isCancel(err)) setSourcesError("Не удалось загрузить каталог моделей рельефа. Проверьте соединение с сервером и повторите загрузку.");
      } finally {
        if (!catalogController.signal.aborted) setSourcesLoading(false);
      }
    }
    loadSources();
    return () => catalogController.abort();
  }, [catalogAttempt]);
  useEffect(() => {
    if (!coverageSourceId) return undefined;
    const request = startCoverageRequest(coverageSourceId, async (source, { signal }) => {
      const response = await axios.get(`${import.meta.env.VITE_API_URL || "/api"}/dem_coverage`, { params: { source }, signal, timeout: 120000 });
      return response.data;
    }, setCoverageState);
    return request.cancel;
  }, [coverageSourceId, coverageAttempt]);
  useEffect(() => { sidebarScrollRef.current?.scrollTo({ top: 0 }); }, [tab, result?.id]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 3500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape") { setSearchOpen(false); setLayersOpen(false); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function cancelRequest() {
    requestId.current += 1;
    controller.current?.abort();
    setLoading(false);
  }
  function resetAnalysisView() {
    dispatchAnalysisSelection({ type: "reset" });
    setSoilMapError(null); setLandcoverMapError(null); setElevationMapError(null); setSlopeMapError(null);
  }
  function selectDem(id) {
    if (id === selectedDemId || !demCatalog.sources.some(source => source.id === id && source.available)) return;
    cancelRequest(); setSelectedDemId(id); setResult(null); resetAnalysisView(); setError(""); setTab("setup");
  }
  function retrySources() {
    cancelRequest(); setResult(null); resetAnalysisView(); setError(""); setDemCatalog({ sources: [], defaultSource: null });
    setSourcesLoading(true); setSourcesError(""); setCatalogAttempt(attempt => attempt + 1);
  }
  function showCoverage() {
    setCoverageVisible(true); setExploringCoverage(true); setMobilePanel(false); mapApi.current?.fitCoverage();
  }
  function selectPoint(next) {
    cancelRequest(); setPoint(next);
    setCoordinates({ lat: next.lat.toFixed(5), lng: next.lng.toFixed(5) });
    setResult(null); resetAnalysisView(); setError(""); setTab("setup"); setMobilePanel(true);
  }
  function applyCoordinates(event) {
    event.preventDefault();
    const parsed = parseCoordinates(`${coordinates.lat.trim()}, ${coordinates.lng.trim()}`);
    if (!parsed) { setError("Введите широту от −85 до 85 и долготу от −180 до 180. Десятичный разделитель — точка."); return; }
    selectPoint(parsed); mapApi.current?.flyTo(parsed, 10);
  }
  function editCoordinate(field, value) {
    cancelRequest(); setResult(null); resetAnalysisView(); setError("");
    const next = { ...coordinates, [field]: value };
    setCoordinates(next);
    setPoint(parseCoordinates(`${next.lat.trim()}, ${next.lng.trim()}`));
  }
  function goToPlace(place) {
    mapApi.current?.flyTo(place, place.zoom);
    setSearch(""); setSearchOpen(false); setSearchError(""); searchRef.current?.blur();
  }
  function submitSearch(event) {
    event.preventDefault();
    const parsed = parseCoordinates(search);
    if (parsed) {
      selectPoint(parsed); mapApi.current?.flyTo(parsed, 10);
      setSearchOpen(false); setSearchError(""); searchRef.current?.blur(); return;
    }
    const place = PLACES.find(item => item.name.toLowerCase() === search.trim().toLowerCase());
    if (place) { goToPlace(place); return; }
    setSearchError("Выберите место из списка или введите координаты: 56.86, 35.91"); setSearchOpen(true);
  }
  async function calculate() {
    if (!point || loading || sourcesLoading || sourcesError || !selectedSource?.available) return;
    const source = { ...selectedSource };
    const id = ++requestId.current;
    controller.current?.abort(); controller.current = new AbortController();
    setLoading(true); setResult(null); resetAnalysisView(); setError("");
    try {
      const response = await axios.get(`${import.meta.env.VITE_API_URL || "/api"}/get_watershed`, {
        params: { latitude: point.lat, longitude: point.lng, source: source.id }, signal: controller.current.signal, timeout: 180000,
      });
      if (id !== requestId.current) return;
      if (!response.data?.watershed?.features?.length || !response.data.analysis) {
        setError("Для этой точки водосбор не найден. Выберите другую точку на реке в пределах доступных данных."); return;
      }
      const next = createWatershedResult(response.data, point, source, Date.now());
      setResult(next); setHistory(items => [next, ...items].slice(0, 8)); setTab("results"); setVisible(true); setSelecting(false);
    } catch (err) {
      if (id === requestId.current && !axios.isCancel(err)) setError(analysisError(err));
    } finally { if (id === requestId.current) setLoading(false); }
  }
  function reset() {
    cancelRequest(); setPoint(null); setCoordinates({ lat: "", lng: "" }); setResult(null); resetAnalysisView(); setError(""); setTab("setup"); setSelecting(true); setExploringCoverage(false);
  }
  function restore(item) {
    cancelRequest(); setSelectedDemId(item.info.dem); setResult({ ...item, watershed: { ...item.watershed } }); setPoint({ lat: item.info.lat, lng: item.info.lng });
    setCoordinates({ lat: item.info.lat.toFixed(5), lng: item.info.lng.toFixed(5) }); resetAnalysisView(); setError(""); setTab("results"); setVisible(true); setSelecting(false);
  }
  function exportResult() {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result.watershed, null, 2)], { type: "application/geo+json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a"); anchor.href = url;
    anchor.download = `watershed_${result.info.dem}_${result.info.lat.toFixed(4)}_${result.info.lng.toFixed(4)}.geojson`;
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000); setToast("Граница бассейна сохранена в GeoJSON");
  }
  const places = PLACES.filter(item => `${item.name} ${item.detail}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="app-shell">
    <header className="topbar">
      <a className="brand-link" href="#" onClick={e => { e.preventDefault(); mapApi.current?.home(); }} aria-label="Watershed Lab — начальный вид карты"><Logo /></a>
      <nav className="top-nav" aria-label="Главная навигация"><button className="active" onClick={() => { setDialog(null); setMobilePanel(false); }}><Map size={15} />Карта</button><button onClick={() => setDialog("sources")}>Источники данных</button><button onClick={() => setDialog("about")}>О проекте</button></nav>
      <div className="topbar-end"><span className="research-tag"><i /> Исследовательская версия</span><button className="icon-button help-top" onClick={() => setDialog("about")} aria-label="Как пользоваться сервисом"><CircleHelp size={19} /></button><span className="user-avatar" title="Локальное рабочее пространство">W</span></div>
    </header>
    <div className="workspace">
      <aside className="tool-rail" aria-label="Инструменты рабочего пространства">
        <div className="rail-top"><button className={tab !== "history" ? "rail-button active" : "rail-button"} onClick={() => { setTab(result ? "results" : "setup"); setMobilePanel(true); }} title="Исследование" aria-label="Исследование"><Map size={20} /></button><button className={tab === "history" ? "rail-button active" : "rail-button"} onClick={() => { setTab("history"); setMobilePanel(true); }} title="История сессии" aria-label="История сессии"><History size={20} />{history.length > 0 && <i className="rail-dot" />}</button><button className="rail-button" onClick={() => setDialog("sources")} title="Каталог данных" aria-label="Каталог данных"><Database size={20} /></button><span className="rail-divider" /><button className="rail-button" onClick={() => setDialog("future")} title="Сценарии затопления · в разработке" aria-label="Сценарии затопления — в разработке"><Waves size={21} /><span className="soon-dot" /></button></div>
        <div className="rail-bottom"><button className="rail-button" onClick={() => setDialog("about")} title="Справка" aria-label="Справка"><CircleHelp size={20} /></button><span className="rail-version">v.01</span></div>
      </aside>
      <aside className={`sidebar ${mobilePanel ? "is-open" : ""}`} aria-label="Параметры и результаты анализа">
        <div className="sidebar-heading"><span className="eyebrow">ГИДРОЛОГИЧЕСКАЯ ЛАБОРАТОРИЯ</span><button className="icon-button mobile-close" onClick={() => setMobilePanel(false)} aria-label="Вернуться к карте"><X size={19} /></button><h1>{tab === "history" ? "Мои исследования" : "Водосборный бассейн"}</h1><p>{tab === "history" ? "Результаты текущей рабочей сессии" : "Исследуйте территорию, которую объединяет вода."}</p></div>
        {tab !== "history" && <div className="sidebar-tabs"><button className={tab === "setup" ? "active" : ""} onClick={() => setTab("setup")}><SlidersHorizontal size={14} />Параметры</button><button className={tab === "results" ? "active" : ""} onClick={() => setTab("results")}><Layers size={14} />Анализ{result && <i />}</button></div>}
        <div className="sidebar-scroll" ref={sidebarScrollRef}>
          {tab === "setup" && <>
            <section className="setup-section"><div className="section-label"><span>01</span><h2>Модель рельефа</h2><button className="small-help" onClick={() => setDialog("sources")} aria-label="О цифровых моделях рельефа"><CircleHelp size={14} /></button></div>
              <DemSourceSelect sources={demCatalog.sources} value={selectedDemId} onChange={selectDem} loading={sourcesLoading} error={sourcesError} onRetry={retrySources} />
              <div className="source-status"><span>{selectedSource?.available && !sourcesLoading && !sourcesError ? <><i />Готова к расчёту</> : "Выбор зависит от доступности данных"}</span><button onClick={() => setDialog("sources")}>О данных <ChevronRight size={12} /></button></div>
              {coverageSourceId && <DemCoverageControl coverage={coverage} status={coverageStatus} visible={coverageVisible} onShow={showCoverage} onRetry={() => setCoverageAttempt(attempt => attempt + 1)} />}
            </section>
            <section className="setup-section point-section"><div className="section-label"><span>02</span><h2>Замыкающий створ</h2><span className="tiny-tag">WGS 84</span></div>
              <button className={`select-point ${selecting ? "is-selecting" : ""}`} aria-pressed={selecting} onClick={() => { setSelecting(true); setMobilePanel(false); }}><MousePointer2 size={17} /><span>{selecting ? "Укажите точку на карте" : "Выбрать точку на карте"}</span>{selecting && <span className="selection-pulse" />}</button>
              <div className="field-divider"><span>или введите координаты</span></div>
              <form onSubmit={applyCoordinates} className="coordinate-form"><label>Широта<input aria-label="Широта" placeholder="56.86000" inputMode="decimal" value={coordinates.lat} onChange={e => editCoordinate("lat", e.target.value)} /></label><label>Долгота<input aria-label="Долгота" placeholder="35.91000" inputMode="decimal" value={coordinates.lng} onChange={e => editCoordinate("lng", e.target.value)} /></label><button className="coordinate-apply" type="submit" title="Перейти к координатам" aria-label="Установить точку по координатам"><ArrowRight size={16} /></button></form>
              {point && <div className="point-selected"><Check size={13} />Точка выбрана<button onClick={reset}>Сбросить</button></div>}
            </section>
            <div className="calculate-block"><button className="primary-button calculate-button" disabled={!point || loading || sourcesLoading || !!sourcesError || !selectedSource?.available} onClick={calculate}>{loading ? <LoaderCircle className="spin" size={18} /> : <Waves size={18} />}{loading ? "Строим водосбор…" : "Построить водосбор"}{!loading && <ArrowRight size={16} />}</button>{loading ? <button className="cancel-button" onClick={cancelRequest}>Отменить ожидание</button> : <p>{sourcesLoading ? "Ожидаем каталог моделей рельефа" : !selectedSource?.available || sourcesError ? "Для расчёта нужна доступная модель рельефа" : point ? "Граница и характеристики по выбранной точке" : "Для начала выберите точку на реке"}</p>}</div>
            {error && <div className="error-message" role="alert"><TriangleAlert size={17} /><span>{error}</span><button className="icon-button" onClick={() => setError("")} aria-label="Скрыть ошибку"><X size={14} /></button></div>}
            <section className="output-preview"><span className="eyebrow">ЧТО РАССКАЖЕТ ВОДОСБОР</span><div className="output-grid"><span><Focus size={17} />Граница и площадь</span><span><Mountain size={17} />Высоты и уклоны</span><span><CloudRain size={17} />Климат</span><span><Leaf size={17} />Земной покров</span><span><Layers size={17} />Почвенный состав</span></div></section>
            <div className="sidebar-story"><svg viewBox="0 0 320 116" fill="none" aria-hidden="true"><g stroke="currentColor"><path d="M-10 80Q65-10 132 58T330 20M-10 90Q65 0 132 68T330 30M-10 100Q65 10 132 78T330 40M-10 110Q65 20 132 88T330 50M-10 120Q65 30 132 98T330 60M-10 70Q65-20 132 48T330 10M-10 60Q65-30 132 38T330 0M-10 50Q65-40 132 28T330-10" /></g><path d="M180 0C170 27 134 29 142 48S177 83 150 116" stroke="#63968b" strokeWidth="2" /></svg><span>Маленькая точка.<br /><strong>Большая картина.</strong></span></div>
          </>}
          {tab === "results" && <>{result && <div className="result-heading"><span className="complete-badge"><Check size={12} />Расчёт выполнен</span><button className="text-button" onClick={reset}><Plus size={13} />Новый</button></div>}<InfoPanel key={result?.id || "empty"} info={result?.info} onExport={result ? exportResult : undefined} active={analysisSection} onSectionChange={section => dispatchAnalysisSelection({ type: "select", section })} mapViews={thematicViews} mapErrors={thematicErrors} /></>}
          {tab === "history" && (history.length ? <div className="history-list"><span className="history-note">Хранится до обновления страницы · {history.length} из 8</span>{history.map((item, i) => <button key={item.id} className="history-item" onClick={() => restore(item)}><span className="history-icon"><Waves size={19} /></span><span><strong>Водосбор {history.length - i}</strong><small>{item.info.lat.toFixed(4)}°, {item.info.lng.toFixed(4)}°</small><small>{item.info.dem_label || item.info.dem}</small><b>{typeof item.info.area_km2 === "number" ? item.info.area_km2.toLocaleString("ru-RU", { maximumFractionDigits: 2 }) : "—"} км²</b></span><ChevronRight size={16} /></button>)}</div> : <div className="empty-history"><History size={33} strokeWidth={1.3} /><h2>История ещё впереди</h2><p>Постройте первый водосбор — его результат появится здесь.</p><button className="secondary-button" onClick={() => setTab("setup")}>Начать исследование <ArrowRight size={15} /></button></div>)}
        </div>
        <div className="sidebar-footer"><span className="local-dot" />Локальное рабочее пространство<button onClick={() => setTab("history")} aria-label="История исследований"><History size={14} /></button><button onClick={() => setDialog("future")} aria-label="Будущие сценарии затопления"><Waves size={14} /></button><button onClick={() => setDialog("about")} aria-label="Информация о проекте"><CircleHelp size={13} /></button></div>
      </aside>
      <main className="map-workspace">
        <div className="map-heading"><div><span className="map-heading-icon"><Map size={17} /></span><span>Карта исследования</span><ChevronRight size={13} /><span className="map-heading-muted">{result ? "Выделенный водосбор" : "Новый водосбор"}</span></div><button className="export-button" disabled={!result} onClick={exportResult}><ArrowDownToLine size={14} /><span>Экспорт</span></button></div>
        <div className="map-stage">
          <MapCanvas basemap={basemap} point={point} watershed={result?.watershed || null} visible={visible} soilMap={soilMap} landcoverMap={landcoverMap} elevationMap={elevationMap} slopeMap={slopeMap} activeThematicLayer={activeThematicLayer} coverage={coverage} coverageVisible={coverageVisible} selecting={selecting} onSelect={selectPoint} onMove={setPosition} onError={setMapError} onSoilError={setSoilMapError} onLandcoverError={setLandcoverMapError} onElevationError={setElevationMapError} onSlopeError={setSlopeMapError} onReady={api => { mapApi.current = api; }} />
          <div className="map-top-controls">
            <div className="search-wrap" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setSearchOpen(false); }}><form className="map-search" onSubmit={submitSearch}><Search size={17} /><input ref={searchRef} aria-label="Поиск места или координат" placeholder="Место или координаты" value={search} onChange={e => { setSearch(e.target.value); setSearchOpen(true); setSearchError(""); }} onFocus={() => setSearchOpen(true)} /><button type="submit" aria-label="Найти на карте"><span>↵</span></button></form>
              {searchOpen && <div className="search-results"><span className="eyebrow">БЫСТРЫЙ ПЕРЕХОД</span>{places.map(place => <button key={place.name} onClick={() => goToPlace(place)}><MapPin size={15} /><span><strong>{place.name}</strong><small>{place.detail}</small></span><ArrowRight size={13} /></button>)}{searchError && <p className="search-error" role="alert">{searchError}</p>}<p>Координаты: широта, долгота<br /><span>Например: 56.86, 35.91</span></p></div>}
            </div>
            <div className="layers-wrap"><button className={`layers-button ${layersOpen ? "active" : ""}`} aria-label="Слои карты" aria-expanded={layersOpen} onClick={() => setLayersOpen(v => !v)}><Layers size={17} /><span>Слои карты</span><ChevronDown size={13} /></button>{layersOpen && <div className="layers-popover"><span className="eyebrow">ПОДЛОЖКА КАРТЫ</span><div className="basemap-options">{[{ id: "topo", label: "Рельеф" }, { id: "satellite", label: "Спутник" }, { id: "light", label: "Улицы" }].map(item => <button key={item.id} aria-pressed={basemap === item.id} className={basemap === item.id ? "selected" : ""} onClick={() => setBasemap(item.id)}><span className={`basemap-swatch ${item.id}`}>{basemap === item.id && <Check size={14} />}</span>{item.label}</button>)}</div><div className="layer-toggle-row"><span><i className="legend-coverage" />Покрытие ЦМР</span><button className={`toggle coverage-toggle ${coverageVisible ? "on" : ""}`} role="switch" aria-checked={coverageVisible} aria-label="Показать покрытие ЦМР" onClick={() => setCoverageVisible(value => !value)}><span /></button></div><p>{coverageStatus === "loading" ? "Загружаем границы покрытия…" : coverageStatus === "error" ? "Границы не загрузились. Повторите загрузку в параметрах." : coverage ? "Приблизительная граница области с данными. Весь водосбор должен быть внутри." : "Выберите доступную модель рельефа"}</p><div className="layer-toggle-row"><span><i className="legend-basin" />Граница водосбора</span><button className={`toggle ${visible ? "on" : ""}`} role="switch" aria-checked={visible} aria-label="Показать границу водосбора" disabled={!result} onClick={() => setVisible(v => !v)}><span /></button></div><p>{result ? `Граница рассчитана по ${result.info.dem_label || result.info.dem}` : "Появится после построения бассейна"}</p><p className="thematic-layer-note">Тематические слои — во вкладках анализа. «Обзор» отключает цветной слой.</p></div>}</div>
          </div>
          <button className="mobile-panel-toggle" onClick={() => setMobilePanel(true)}><SlidersHorizontal size={17} />{result ? "Результаты анализа" : "Параметры водосбора"}</button>
          {activeMap?.status === "ok" && (["elevation", "slope"].includes(activeThematicLayer)
            ? <TerrainMapLegend key={`${result.id}-${activeThematicLayer}`} kind={activeThematicLayer} view={activeMap} error={activeMapError} />
            : <CategoricalMapLegend key={`${result.id}-${activeThematicLayer}`} kind={activeThematicLayer} view={activeMap} error={activeMapError} />)}
          <div className={`map-context ${coverageVisible && coverage?.bounds ? "has-coverage" : ""}`} title={coverageVisible && coverage ? coverage.note : undefined}>{coverageVisible && coverage?.bounds ? <><i className="legend-coverage" /><span aria-label="Приблизительное покрытие ЦМР">Покрытие ≈</span></> : <><span className="context-dot" /><span>Модель границы</span></>}<span className="context-separator" />{result?.info.dem_label || selectedSource?.label || "Не выбрана"}</div>
          <div className="map-tools"><button className={selecting ? "active" : ""} aria-pressed={selecting} onClick={() => setSelecting(v => !v)} title="Выбрать точку" aria-label="Режим выбора точки"><MousePointer2 size={19} /></button><span /><button onClick={() => mapApi.current?.zoomIn()} aria-label="Приблизить" title="Приблизить"><Plus size={20} /></button><button onClick={() => mapApi.current?.zoomOut()} aria-label="Отдалить" title="Отдалить"><Minus size={20} /></button><span /><button onClick={() => result ? mapApi.current?.fitWatershed() : mapApi.current?.home()} title="Показать область исследования" aria-label="Показать область исследования"><Focus size={18} /></button><button className="north-button" onClick={() => mapApi.current?.resetNorth()} title="Ориентировать на север" aria-label="Ориентировать на север"><span>N</span><Compass size={19} /></button></div>
          {mapError && <div className="map-error" role="status"><TriangleAlert size={15} /><span>{mapError}</span><button onClick={() => setBasemap(basemap === "light" ? "topo" : "light")}>Сменить подложку</button></div>}
          {loading && <div className="map-loading" role="status"><span className="loading-rings"><Waves size={22} /></span><div><strong>Находим путь воды</strong><p>Выделяем бассейн и анализируем территорию…</p></div><button className="icon-button" onClick={cancelRequest} aria-label="Отменить ожидание расчёта"><X size={16} /></button></div>}
          {!point && !loading && !exploringCoverage && <div className="map-intro"><span className="map-intro-icon"><MousePointer2 size={21} strokeWidth={1.5} /></span><div><span className="eyebrow">ИССЛЕДУЙТЕ СВЯЗИ В ПРИРОДЕ</span><h2>Всё начинается с одной точки</h2><p>Нажмите на реку, чтобы задать точку водосбора.</p><div className="place-shortcuts"><span>Начать с</span>{PLACES.slice(0, 3).map(place => <button key={place.name} onClick={() => goToPlace(place)}>{place.name}<ArrowRight size={11} /></button>)}</div></div></div>}
          {point && !loading && <div className="point-map-label"><span className="point-key" /><span>{result ? "Замыкающий створ" : "Выбранная точка"}</span><strong>{point.lat.toFixed(4)}°, {point.lng.toFixed(4)}°</strong>{result && <button onClick={() => { setTab("results"); setMobilePanel(true); }}>Анализ <ArrowRight size={13} /></button>}</div>}
        </div>
        <footer className="map-statusbar"><span><span className="status-cross">+</span>{Math.abs(position.lat).toFixed(4)}° {position.lat >= 0 ? "N" : "S"}<span className="status-slash">/</span>{Math.abs(position.lng).toFixed(4)}° {position.lng >= 0 ? "E" : "W"}</span><span className="status-instruction">{selecting ? <><MousePointer2 size={12} />Нажмите на карту, чтобы выбрать точку</> : <><Map size={12} />Перетаскивайте карту для перемещения</>}</span><span>WGS 84 <i /> z {position.zoom.toFixed(1)}</span></footer>
      </main>
    </div>
    {dialog && <Dialog type={dialog} onClose={() => setDialog(null)} sources={demCatalog.sources} sourcesLoading={sourcesLoading} sourcesError={sourcesError} onRetrySources={retrySources} />}
    {toast && <div className="toast" role="status"><Check size={17} />{toast}</div>}
  </div>;
}
