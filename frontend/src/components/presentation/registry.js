// src/components/presentation/registry.js — the ONE place a
// presentation `type` string is mapped to the component that renders
// it. PresentationSurface.jsx consults this and only this; no component
// anywhere else hardcodes a type->renderer mapping of its own (Track 3's
// own "future modules easy to add without changing the execution
// system" goal — adding a module means adding one entry here plus one
// new component file, nothing else changes).
//
// Every payload is `{type, data}` (optionally `title`/`meta` — see each
// renderer's own PropTypes-style comment at its top for its exact `data`
// shape). `type` must be one of the keys below; anything else is an
// invalid/unsupported payload and PresentationSurface falls back to
// plain title/text rendering (see isValidPresentation()) rather than
// crashing or guessing at a shape it doesn't recognize.
import WeatherPresentation from "./WeatherPresentation";
import CalendarPresentation from "./CalendarPresentation";
import TablePresentation from "./TablePresentation";
import SearchResultsPresentation from "./SearchResultsPresentation";
import GenericInfoPresentation from "./GenericInfoPresentation";
import StatusPresentation from "./StatusPresentation";

export const PRESENTATION_REGISTRY = {
  weather: WeatherPresentation,
  calendar: CalendarPresentation,
  table: TablePresentation,
  search_results: SearchResultsPresentation,
  generic_information: GenericInfoPresentation,
  status: StatusPresentation,
};

export function resolvePresentationComponent(type) {
  return PRESENTATION_REGISTRY[type] || null;
}

// Real payload validation (section 5's own "structured presentation
// model", section 20's own "no presentation for unsupported/invalid
// payloads" test requirement) — deliberately permissive about `data`
// itself (each renderer is responsible for handling its own missing/
// partial fields honestly, same "never fabricate" discipline the
// backend already follows), strict about the one thing that actually
// decides ROUTING: a real, known `type` string.
export function isValidPresentation(presentation) {
  return !!(
    presentation &&
    typeof presentation === "object" &&
    typeof presentation.type === "string" &&
    PRESENTATION_REGISTRY[presentation.type]
  );
}
