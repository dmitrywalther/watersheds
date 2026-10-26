export const INITIAL_ANALYSIS_SECTION = "overview";

const SECTIONS = new Set(["overview", "elevation", "slope", "climate", "landcover", "soil"]);
const THEMATIC_SECTIONS = new Set(["elevation", "slope", "landcover", "soil"]);

export function analysisSelectionReducer(section, action) {
  if (action.type === "reset") return INITIAL_ANALYSIS_SECTION;
  if (action.type === "select" && SECTIONS.has(action.section)) return action.section;
  return section;
}

export function getActiveThematicLayer(section, views = {}) {
  return THEMATIC_SECTIONS.has(section) && views[section]?.status === "ok" ? section : null;
}
