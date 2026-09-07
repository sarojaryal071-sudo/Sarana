// src/components/presentation/WeatherPresentation.jsx — real weather
// data only: `data` is exactly actions/weather.py's own
// get_weather_data() shape (see that module's own docstring), broadcast
// unchanged by main.py's get_weather dispatch. No field here is
// invented client-side — a missing value just renders as "—", never a
// guessed number.
//
// `expanded` (Universal Information Surface's own compact/expanded
// state — see PresentationSurface.jsx) decides how many of the now-7
// fetched forecast days actually show: 2 (today/tomorrow) by default, a
// concise, honest glance; the full week once the user says "expand
// that"/"show me more"/"show me the next five days". The data for all 7
// is already there either way — expanding never triggers a second
// fetch, it just reveals what get_weather_data() already returned.
const COMPACT_DAY_COUNT = 2;

export default function WeatherPresentation({ data, expanded = false }) {
  const current = data?.current || {};
  const daily = Array.isArray(data?.daily) ? data.daily : [];
  const shownDaily = expanded ? daily : daily.slice(0, COMPACT_DAY_COUNT);

  return (
    <div className="pw-weather">
      {data?.location && <div className="pw-weather-location pw-reveal-item">{data.location}</div>}
      <div className="pw-weather-now pw-reveal-item" style={{ "--pw-reveal-index": 1 }}>
        <span className="pw-weather-temp">
          {current.temperature ?? "—"}
          <span className="pw-weather-unit">{current.unit || ""}</span>
        </span>
        <div className="pw-weather-now-detail">
          <div className="pw-weather-condition">{current.condition || "conditions unknown"}</div>
          <div className="pw-weather-meta">
            feels like {current.feels_like ?? "—"}{current.feels_like_unit || ""}
            {" · "}wind {current.wind ?? "—"}{current.wind_unit || ""}
          </div>
        </div>
      </div>
      {shownDaily.length > 0 && (
        <div className="pw-weather-days">
          {shownDaily.map((d, i) => (
            <div className="pw-weather-day pw-reveal-item" style={{ "--pw-reveal-index": i + 2 }} key={d.date || d.label}>
              <div className="pw-weather-day-label">{d.label}</div>
              <div className="pw-weather-day-condition">{d.condition}</div>
              <div className="pw-weather-day-range">
                <span className="pw-weather-day-high">{d.high}°</span>
                <span className="pw-weather-day-low">{d.low}°</span>
              </div>
              <div className="pw-weather-day-precip">{d.precip_probability}{d.precip_probability_unit} rain</div>
            </div>
          ))}
        </div>
      )}
      {!expanded && daily.length > COMPACT_DAY_COUNT && (
        <div className="pw-weather-more-hint">+{daily.length - COMPACT_DAY_COUNT} more days — expand for the full week</div>
      )}
    </div>
  );
}
