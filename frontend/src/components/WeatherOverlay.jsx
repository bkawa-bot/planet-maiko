/**
 * Page-level weather and seasonal ambient effects. Drifting clouds,
 * raindrops, snowflakes, fog, spring flowers, summer fireflies, autumn
 * leaves, night planets/stars/shooting stars.
 *
 * Driven by `scene.context.weather`, `scene.context.season`, and
 * `scene.context.time_bucket`. Safe to render when scene is null (returns
 * nothing). Absolutely positioned and pointer-events: none so it never
 * steals clicks.
 *
 * Props:
 *   scene — scene object from /api/scene, shape: { context: { weather,
 *           season, time_bucket }, ... }. Can be null/undefined.
 */
export default function WeatherOverlay({ scene }) {
  if (!scene?.context) return null;
  const { weather, season, time_bucket: timeBucket } = scene.context;
  const hasWeather = weather && weather !== "clear";

  return (
    <>
      {/* Weather effects */}
      {hasWeather && (
        <div className="page-weather-overlay">
          {(weather === "cloudy" || weather === "rain") && (
            <>
              <img src="/cloud1.svg" className="page-cloud page-cloud-1" alt="" />
              <img src="/cloud2.svg" className="page-cloud page-cloud-2" alt="" />
              <img src="/cloud3.svg" className="page-cloud page-cloud-3" alt="" />
              <img src="/cloud1.svg" className="page-cloud page-cloud-4" alt="" />
              <img src="/cloud2.svg" className="page-cloud page-cloud-5" alt="" />
              <img src="/cloud3.svg" className="page-cloud page-cloud-6" alt="" />
              <img src="/cloud1.svg" className="page-cloud page-cloud-7" alt="" />
            </>
          )}
          {weather === "rain" && (
            <div className="page-rain">
              {Array.from({ length: 60 }).map((_, i) => (
                <div
                  key={i}
                  className="page-raindrop"
                  style={{
                    left: `${(i * 1.7) + Math.random()}%`,
                    animationDelay: `${Math.random() * 1.2}s`,
                    animationDuration: `${0.6 + Math.random() * 0.4}s`,
                  }}
                />
              ))}
            </div>
          )}
          {weather === "snow" && (
            <div className="page-snow">
              {Array.from({ length: 40 }).map((_, i) => (
                <div
                  key={i}
                  className="page-snowflake"
                  style={{
                    left: `${i * 2.5 + Math.random() * 1.5}%`,
                    animationDelay: `${Math.random() * 5}s`,
                    animationDuration: `${3 + Math.random() * 3}s`,
                  }}
                />
              ))}
            </div>
          )}
          {weather === "fog" && <div className="page-fog" />}
        </div>
      )}

      {/* Seasonal overlays */}
      {season && (
        <div className="page-weather-overlay">
          {season === "spring" && Array.from({ length: 5 }).map((_, i) => (
            <img
              key={`fl-${i}`}
              src={i % 2 === 0 ? "/flower1.svg" : "/flower2.svg"}
              className="page-flower"
              style={{ left: `${10 + i * 18}%`, bottom: 32 }}
              alt=""
            />
          ))}

          {season === "summer" && timeBucket !== "day" && Array.from({ length: 12 }).map((_, i) => (
            <div
              key={`ff-${i}`}
              className="page-firefly"
              style={{
                left: `${5 + Math.random() * 85}%`,
                top: `${10 + Math.random() * 60}%`,
                animationDelay: `${Math.random() * 4}s`,
                animationDuration: `${2 + Math.random() * 3}s`,
              }}
            />
          ))}

          {season === "autumn" && Array.from({ length: 8 }).map((_, i) => (
            <img
              key={`lf-${i}`}
              src="/leaf.svg"
              className="page-leaf"
              style={{
                left: `${i * 12 + Math.random() * 5}%`,
                animationDelay: `${Math.random() * 6}s`,
                animationDuration: `${4 + Math.random() * 4}s`,
              }}
              alt=""
            />
          ))}

          {timeBucket === "night" && (
            <>
              <img src="/planet1.svg" className="page-planet page-planet-1" alt="" />
              <img src="/planet2.svg" className="page-planet page-planet-2" alt="" />
              {Array.from({ length: 15 }).map((_, i) => (
                <div
                  key={`st-${i}`}
                  className="page-star"
                  style={{
                    left: `${Math.random() * 95}%`,
                    top: `${Math.random() * 40}%`,
                    animationDelay: `${Math.random() * 5}s`,
                  }}
                />
              ))}
              <div className="page-shooting-star" />
            </>
          )}
        </div>
      )}
    </>
  );
}
