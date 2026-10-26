// src/components/MapControls.jsx
//
// Плавающие pill-контролы поверх карты: выбор источника DEM и подложки.

import { Layers, Map as MapIcon } from "lucide-react";
import Dropdown from "./Dropdown";

const DEM_OPTIONS = [
  { value: "ETOPO1_1min", label: "ETOPO1 1′" },
  { value: "GMTED2010_30sec", label: "GMTED2010 30″" },
  { value: "MERIT_3sec", label: "MERIT DEM 3″" },
];

const BASEMAP_OPTIONS = [
  { value: "liberty", label: "Liberty" },
  { value: "osm", label: "OSM Demo" },
];

export default function MapControls({ dem, setDem, basemap, setBasemap }) {
  return (
    <div
      style={{
        position: "absolute",
        top: 16,
        right: 16,
        zIndex: 2,
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-end",
        gap: 10,
      }}
    >
      <Dropdown
        icon={Layers}
        label="DEM"
        value={dem}
        options={DEM_OPTIONS}
        onChange={setDem}
      />
      <Dropdown
        icon={MapIcon}
        label="Basemap"
        value={basemap}
        options={BASEMAP_OPTIONS}
        onChange={setBasemap}
      />
    </div>
  );
}
