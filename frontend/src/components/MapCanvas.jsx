import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { watershedBounds } from "../lib/geo.js";
import { COVERAGE_SOURCE, coverageFitBounds, updateDemCoverage } from "../lib/demCoverage.js";
import { BASIN_SOURCE, SOIL_SOURCE, LANDCOVER_SOURCE, ELEVATION_SOURCE, SLOPE_SOURCE, updateAnalysisLayers } from "../lib/categoricalMap.js";
import { createMapHoverController, requestTerrainValue } from "../lib/mapHover.js";
import "./MapHover.css";

const HOME = { center: [37.3, 56.35], zoom: 6.6 };

function mapStyle(basemap) {
  if (basemap === "light") return "https://tiles.openfreemap.org/styles/liberty";
  const satellite = basemap === "satellite";
  const service = satellite ? "World_Imagery" : "World_Topo_Map";
  return {
    version: 8,
    sources: {
      "esri-basemap": {
        type: "raster",
        tiles: [`https://server.arcgisonline.com/ArcGIS/rest/services/${service}/MapServer/tile/{z}/{y}/{x}`],
        tileSize: 256,
        maxzoom: 19,
        attribution: satellite
          ? 'Tiles &copy; <a href="https://www.esri.com/" target="_blank" rel="noopener noreferrer">Esri</a> — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community'
          : 'Tiles &copy; <a href="https://www.esri.com/" target="_blank" rel="noopener noreferrer">Esri</a> — Esri, HERE, Garmin, Intermap, increment P Corp., GEBCO, USGS, FAO, NPS, NRCAN, GeoBase, IGN, Kadaster NL, Ordnance Survey, Esri Japan, METI, Esri China (Hong Kong), OpenStreetMap contributors, and the GIS User Community',
      },
    },
    layers: [{ id: "esri-basemap", type: "raster", source: "esri-basemap" }],
  };
}

function fitWatershed(map, watershed) {
  const bounds = watershedBounds(watershed);
  if (!bounds) return;
  const width = map.getContainer().clientWidth;
  map.fitBounds(bounds, { padding: width < 600 ? 45 : 90, maxZoom: 13, duration: 900 });
}

export default function MapCanvas({
  basemap = "topo",
  point = null,
  watershed = null,
  visible = true,
  soilMap = null,
  landcoverMap = null,
  elevationMap = null,
  slopeMap = null,
  activeThematicLayer = null,
  coverage = null,
  coverageVisible = true,
  selecting = true,
  onSelect,
  onMove,
  onError,
  onSoilError,
  onLandcoverError,
  onElevationError,
  onSlopeError,
  onReady,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const markerRef = useRef(null);
  const currentRef = useRef({});
  const styleRef = useRef(null);
  const styleReadyRef = useRef(false);
  const errorRef = useRef({ active: false, source: null });
  const hoverRef = useRef(null);

  // MapLibre subscriptions remain stable and always read the latest React props.
  useEffect(() => {
    currentRef.current = { basemap, point, watershed, visible, soilMap, landcoverMap, elevationMap, slopeMap, activeThematicLayer, coverage, coverageVisible, selecting, onSelect, onMove, onError, onSoilError, onLandcoverError, onElevationError, onSlopeError, onReady };
  }, [basemap, point, watershed, visible, soilMap, landcoverMap, elevationMap, slopeMap, activeThematicLayer, coverage, coverageVisible, selecting, onSelect, onMove, onError, onSoilError, onLandcoverError, onElevationError, onSlopeError, onReady]);

  useEffect(() => {
    let map;
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        style: mapStyle(currentRef.current.basemap),
        ...HOME,
        attributionControl: false,
      });
    } catch {
      currentRef.current.onError?.("Не удалось запустить карту. Проверьте, включено ли аппаратное ускорение в браузере.");
      return undefined;
    }
    mapRef.current = map;
    styleRef.current = currentRef.current.basemap;
    let disposed = false;
    let resizeFrame;

    map.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: "metric" }), "bottom-left");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    map.getCanvas().setAttribute("aria-label", "Интерактивная карта. Используйте стрелки для перемещения и клавиши плюс и минус для масштаба.");

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, focusAfterOpen: false, offset: 16, maxWidth: "290px", className: "map-hover-popup" });
    const tooltip = document.createElement("div");
    tooltip.className = "map-hover-content";
    tooltip.setAttribute("role", "tooltip");
    tooltip.dataset.testid = "map-feature-tooltip";
    const heading = document.createElement("div");
    heading.className = "map-hover-heading";
    const swatch = document.createElement("i");
    swatch.setAttribute("aria-hidden", "true");
    const title = document.createElement("strong");
    const value = document.createElement("p");
    const detail = document.createElement("small");
    heading.append(swatch, title);
    tooltip.append(heading, value, detail);
    const hover = createMapHoverController({
      map, getContext: () => currentRef.current,
      isReady: () => !disposed && styleReadyRef.current,
      show: content => {
        title.textContent = content.title;
        value.textContent = content.value;
        detail.textContent = content.detail || "";
        detail.hidden = !content.detail;
        swatch.style.backgroundColor = content.color;
        tooltip.dataset.status = content.status;
        popup.setDOMContent(tooltip).setLngLat([content.lng, content.lat]);
        if (!popup.isOpen()) popup.addTo(map);
      },
      hide: () => popup.remove(),
      fetchValue: (target, signal) => requestTerrainValue(import.meta.env.VITE_API_URL || "/api", target, signal),
    });
    hoverRef.current = hover;

    const clearError = () => {
      if (!errorRef.current.active) return;
      errorRef.current = { active: false, source: null };
      currentRef.current.onError?.(null);
    };
    const handleStyleLoad = () => {
      if (disposed) return;
      hover.reset();
      styleReadyRef.current = true;
      const current = currentRef.current;
      updateDemCoverage(map, current.coverage?.geometry, current.coverageVisible);
      updateAnalysisLayers(map, { watershed: current.watershed, basinVisible: current.visible, soilMap: current.soilMap, landcoverMap: current.landcoverMap, elevationMap: current.elevationMap, slopeMap: current.slopeMap, activeThematicLayer: current.activeThematicLayer });
      current.onSoilError?.(null);
      current.onLandcoverError?.(null);
      current.onElevationError?.(null);
      current.onSlopeError?.(null);
    };
    const handleClick = (event) => {
      if (!currentRef.current.selecting) {
        // The same inspection is available by tapping a thematic fill on touch screens.
        hover.inspect(event);
        return;
      }
      hover.reset();
      const { lat, lng } = event.lngLat.wrap();
      if (Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 85) {
        currentRef.current.onSelect?.({ lat, lng });
      }
    };
    const handleMove = () => {
      const center = map.getCenter();
      currentRef.current.onMove?.({ lat: center.lat, lng: center.lng, zoom: map.getZoom() });
    };
    const handleHover = event => { if (map.isMoving()) hover.reset(); else hover.inspect(event); };
    const clearHover = () => hover.reset();
    const handleError = (event) => {
      if (event.sourceId === SOIL_SOURCE) {
        currentRef.current.onSoilError?.("Не удалось отобразить почвенный слой. Попробуйте выключить и включить его.");
        return;
      }
      if (event.sourceId === LANDCOVER_SOURCE) {
        currentRef.current.onLandcoverError?.("Не удалось отобразить земной покров. Попробуйте выключить и включить его.");
        return;
      }
      if (event.sourceId === ELEVATION_SOURCE) {
        currentRef.current.onElevationError?.("Не удалось отобразить высоты. Попробуйте выключить и включить слой.");
        return;
      }
      if (event.sourceId === SLOPE_SOURCE) {
        currentRef.current.onSlopeError?.("Не удалось отобразить уклоны. Попробуйте выключить и включить слой.");
        return;
      }
      errorRef.current = { active: true, source: event.sourceId || null };
      currentRef.current.onError?.("Карта загружается не полностью. Проверьте подключение или смените подложку.");
    };
    const handleSourceData = (event) => {
      if (event.sourceId === SOIL_SOURCE) {
        if (event.isSourceLoaded) currentRef.current.onSoilError?.(null);
        return;
      }
      if (event.sourceId === LANDCOVER_SOURCE) {
        if (event.isSourceLoaded) currentRef.current.onLandcoverError?.(null);
        return;
      }
      if (event.sourceId === ELEVATION_SOURCE) {
        if (event.isSourceLoaded) currentRef.current.onElevationError?.(null);
        return;
      }
      if (event.sourceId === SLOPE_SOURCE) {
        if (event.isSourceLoaded) currentRef.current.onSlopeError?.(null);
        return;
      }
      const failedSource = errorRef.current.source;
      if (event.sourceId !== BASIN_SOURCE && event.sourceId !== COVERAGE_SOURCE && event.isSourceLoaded && (!failedSource || !map.getSource(failedSource) || event.sourceId === failedSource)) clearError();
    };

    map.on("style.load", handleStyleLoad);
    map.on("click", handleClick);
    map.on("mousemove", handleHover);
    map.on("movestart", clearHover);
    map.on("move", handleMove);
    map.on("error", handleError);
    map.on("load", clearError);
    map.on("sourcedata", handleSourceData);
    map.getCanvas().addEventListener("mouseleave", clearHover);
    map.getCanvas().addEventListener("touchcancel", clearHover);
    handleMove();

    const resizeObserver = new ResizeObserver(() => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => { if (!disposed) map.resize(); });
    });
    resizeObserver.observe(containerRef.current);

    currentRef.current.onReady?.({
      zoomIn: () => { if (!disposed) map.zoomIn(); },
      zoomOut: () => { if (!disposed) map.zoomOut(); },
      resetNorth: () => { if (!disposed) map.resetNorth(); },
      flyTo: ({ lat, lng }, zoom = 10) => { if (!disposed) map.flyTo({ center: [lng, lat], zoom, duration: 1100 }); },
      fitWatershed: () => { if (!disposed) fitWatershed(map, currentRef.current.watershed); },
      fitCoverage: () => {
        const bounds = coverageFitBounds(currentRef.current.coverage);
        if (!disposed && bounds) map.fitBounds(bounds, { padding: map.getContainer().clientWidth < 600 ? 45 : 80, maxZoom: 10, duration: 1000 });
      },
      home: () => { if (!disposed) map.flyTo({ ...HOME, bearing: 0, pitch: 0, duration: 1100 }); },
    });

    return () => {
      disposed = true;
      hover.destroy();
      hoverRef.current = null;
      resizeObserver.disconnect();
      cancelAnimationFrame(resizeFrame);
      map.off("style.load", handleStyleLoad);
      map.off("click", handleClick);
      map.off("mousemove", handleHover);
      map.off("movestart", clearHover);
      map.off("move", handleMove);
      map.off("error", handleError);
      map.off("load", clearError);
      map.off("sourcedata", handleSourceData);
      map.getCanvas().removeEventListener("mouseleave", clearHover);
      map.getCanvas().removeEventListener("touchcancel", clearHover);
      markerRef.current?.remove();
      markerRef.current = null;
      map.remove();
      mapRef.current = null;
      styleReadyRef.current = false;
      currentRef.current.onReady?.(null);
    };
  }, []);

  useEffect(() => {
    hoverRef.current?.reset();
  }, [basemap, point, watershed, visible, soilMap, landcoverMap, elevationMap, slopeMap, activeThematicLayer, coverage, selecting]);

  useEffect(() => {
    if (!mapRef.current || styleRef.current === basemap) return;
    styleReadyRef.current = false;
    styleRef.current = basemap;
    errorRef.current = { active: false, source: null };
    currentRef.current.onError?.(null);
    mapRef.current.setStyle(mapStyle(basemap));
  }, [basemap]);

  useEffect(() => {
    if (mapRef.current && styleReadyRef.current) updateDemCoverage(mapRef.current, coverage?.geometry, coverageVisible);
  }, [coverage, coverageVisible]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!point) {
      markerRef.current?.remove();
      markerRef.current = null;
      return;
    }
    if (!markerRef.current) {
      const element = document.createElement("div");
      element.className = "outlet-marker";
      element.setAttribute("role", "img");
      element.setAttribute("aria-label", "Выбранный замыкающий створ");
      markerRef.current = new maplibregl.Marker({ element, anchor: "center" }).setLngLat([point.lng, point.lat]).addTo(map);
    } else markerRef.current.setLngLat([point.lng, point.lat]);
    markerRef.current.getElement().title = `Точка стока: ${point.lat.toFixed(4)}°, ${point.lng.toFixed(4)}°`;
  }, [point]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (watershed) fitWatershed(map, watershed);
  }, [watershed]);

  useEffect(() => {
    const map = mapRef.current;
    if (map && styleReadyRef.current) updateAnalysisLayers(map, { watershed, basinVisible: visible, soilMap, landcoverMap, elevationMap, slopeMap, activeThematicLayer });
  }, [watershed, visible, soilMap, landcoverMap, elevationMap, slopeMap, activeThematicLayer]);

  useEffect(() => {
    if (mapRef.current) mapRef.current.getCanvas().style.cursor = selecting ? "crosshair" : "grab";
  }, [selecting]);

  return <div ref={containerRef} className="map-canvas" role="region" aria-label="Карта водосборных бассейнов" />;
}
