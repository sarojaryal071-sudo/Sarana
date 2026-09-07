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

// A deterministic, restrained line-art glyph for the REAL `condition`
// string actions/weather.py already returns (see that module's own
// _describe_code()) — never invents a weather reading, just a visual
// read of text already on screen. An unrecognized condition string
// falls back to a plain dashed circle rather than guessing a shape.
function conditionIconKind(condition) {
  const c = (condition || "").toLowerCase();
  if (/thunder|storm/.test(c)) return "storm";
  if (/snow|sleet|ice/.test(c)) return "snow";
  if (/rain|drizzle|shower/.test(c)) return "rain";
  if (/fog|mist|haze/.test(c)) return "fog";
  if (/cloud|overcast/.test(c)) return "cloud";
  if (/clear|sun/.test(c)) return "clear";
  return "unknown";
}

function WeatherIcon({ condition, size = 40, className = "" }) {
  const kind = conditionIconKind(condition);
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.1,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": "true",
    className,
  };
  if (kind === "clear") {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4.5" />
        <line x1="12" y1="2" x2="12" y2="4.5" />
        <line x1="12" y1="19.5" x2="12" y2="22" />
        <line x1="2" y1="12" x2="4.5" y2="12" />
        <line x1="19.5" y1="12" x2="22" y2="12" />
        <line x1="4.9" y1="4.9" x2="6.6" y2="6.6" />
        <line x1="17.4" y1="17.4" x2="19.1" y2="19.1" />
        <line x1="4.9" y1="19.1" x2="6.6" y2="17.4" />
        <line x1="17.4" y1="6.6" x2="19.1" y2="4.9" />
      </svg>
    );
  }
  if (kind === "rain") {
    return (
      <svg {...common}>
        <path d="M7 14.5a4 4 0 0 1-.6-7.95 5 5 0 0 1 9.6-1.86A4.5 4.5 0 0 1 17 14.5H7Z" />
        <line x1="8" y1="17" x2="7" y2="20" />
        <line x1="12" y1="17" x2="11" y2="20" />
        <line x1="16" y1="17" x2="15" y2="20" />
      </svg>
    );
  }
  if (kind === "snow") {
    return (
      <svg {...common}>
        <path d="M7 14.5a4 4 0 0 1-.6-7.95 5 5 0 0 1 9.6-1.86A4.5 4.5 0 0 1 17 14.5H7Z" />
        <line x1="8" y1="18" x2="8" y2="20.4" />
        <line x1="6.8" y1="19.2" x2="9.2" y2="19.2" />
        <line x1="16" y1="18" x2="16" y2="20.4" />
        <line x1="14.8" y1="19.2" x2="17.2" y2="19.2" />
      </svg>
    );
  }
  if (kind === "storm") {
    return (
      <svg {...common}>
        <path d="M7 13.5a4 4 0 0 1-.6-7.95 5 5 0 0 1 9.6-1.86A4.5 4.5 0 0 1 17 13.5H7Z" />
        <polyline points="12.5 14.5 10 19 12.5 19 10.5 22.5" />
      </svg>
    );
  }
  if (kind === "fog") {
    return (
      <svg {...common}>
        <line x1="4" y1="9" x2="20" y2="9" />
        <line x1="4" y1="13" x2="20" y2="13" />
        <line x1="4" y1="17" x2="20" y2="17" />
      </svg>
    );
  }
  if (kind === "cloud") {
    return (
      <svg {...common}>
        <path d="M7 17.5a4 4 0 0 1-.6-7.95 5 5 0 0 1 9.6-1.86A4.5 4.5 0 0 1 17 17.5H7Z" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="12" cy="12" r="7" strokeDasharray="2 2" />
    </svg>
  );
}

export default function WeatherPresentation({ data, expanded = false }) {
  const current = data?.current || {};
  const daily = Array.isArray(data?.daily) ? data.daily : [];
  const shownDaily = expanded ? daily : daily.slice(0, COMPACT_DAY_COUNT);

  return (
    <div className="pw-weather">
      {data?.location && <div className="pw-weather-location pw-reveal-item">{data.location}</div>}
      <div className="pw-weather-now pw-reveal-item" style={{ "--pw-reveal-index": 1 }}>
        <WeatherIcon condition={current.condition} size={44} className="pw-weather-icon" />
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
              <WeatherIcon condition={d.condition} size={18} className="pw-weather-day-icon" />
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
