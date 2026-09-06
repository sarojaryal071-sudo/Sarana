// src/components/presentation/WeatherPresentation.jsx — real weather
// data only: `data` is exactly actions/weather.py's own
// get_weather_data() shape (see that module's own docstring), broadcast
// unchanged by main.py's get_weather dispatch. No field here is
// invented client-side — a missing value just renders as "—", never a
// guessed number.
export default function WeatherPresentation({ data }) {
  const current = data?.current || {};
  const daily = Array.isArray(data?.daily) ? data.daily : [];

  return (
    <div className="pw-weather">
      {data?.location && <div className="pw-weather-location">{data.location}</div>}
      <div className="pw-weather-now">
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
      {daily.length > 0 && (
        <div className="pw-weather-days">
          {daily.map((d) => (
            <div className="pw-weather-day" key={d.date || d.label}>
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
    </div>
  );
}
