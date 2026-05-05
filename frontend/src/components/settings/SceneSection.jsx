import { useState } from "react";
import { ChevronDown, ChevronRight, MapPin, Search } from "lucide-react";

/**
 * Scene & Weather — location lookup (free Open-Meteo geocoding API)
 * plus the two visual-overlay toggles (weather, hill background).
 *
 * Owns its own location-lookup state. The resolved string is shown
 * inline; the parent (Settings.jsx) sets the persistent hint by
 * deriving from config.scene.location_name on load.
 */
export default function SceneSection({ config, setConfig, initialResolved = "" }) {
  const [open, setOpen] = useState(true);
  const [locationQuery, setLocationQuery] = useState("");
  const [locationResolved, setLocationResolved] = useState(initialResolved);
  const [lookingUp, setLookingUp] = useState(false);

  const handleLocationLookup = async () => {
    if (!locationQuery.trim()) return;
    setLookingUp(true);
    try {
      const resp = await fetch(
        `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(locationQuery.trim())}&count=1&language=en&format=json`
      );
      const data = await resp.json();
      if (data.results && data.results.length > 0) {
        const r = data.results[0];
        const displayName = r.admin1 ? `${r.name}, ${r.admin1}` : r.name;
        setConfig((c) => ({
          ...c,
          scene: {
            ...c?.scene,
            latitude: r.latitude,
            longitude: r.longitude,
            location_name: displayName,
          },
        }));
        setLocationResolved(`${displayName} (${r.latitude}, ${r.longitude})`);
      } else {
        setLocationResolved("No results found");
      }
    } catch (err) {
      setLocationResolved("Lookup failed: " + err.message);
    }
    setLookingUp(false);
  };

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>Scene & Weather</span>
      </div>
      {open && (
        <div className="collapsible-body">
          <p className="integration-note">
            Enter your location to enable real weather data on the homepage. Uses the free Open-Meteo API (no key needed).
          </p>
          <div className="location-lookup">
            <label>
              <MapPin size={12} /> Location
            </label>
            <div className="location-lookup-row">
              <input
                type="text"
                value={locationQuery}
                onChange={(e) => setLocationQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleLocationLookup(); }}
                placeholder="Zipcode or city name (e.g. 02101 or Boston)"
                className="location-input"
              />
              <button
                className="btn-lookup"
                onClick={handleLocationLookup}
                disabled={lookingUp || !locationQuery.trim()}
              >
                <Search size={12} /> {lookingUp ? "Looking up..." : "Lookup"}
              </button>
            </div>
            {locationResolved && (
              <div className="location-resolved">{locationResolved}</div>
            )}
          </div>

          <div className="integration-fields" style={{ marginTop: 16 }}>
            <label>
              <input
                type="checkbox"
                checked={config.scene?.show_weather_overlay !== false}
                onChange={(e) =>
                  setConfig((c) => ({ ...c, scene: { ...(c.scene || {}), show_weather_overlay: e.target.checked } }))
                }
              />
              Show weather overlay (clouds, rain, snow, stars)
            </label>
            <label>
              <input
                type="checkbox"
                checked={config.scene?.show_hill_background !== false}
                onChange={(e) =>
                  setConfig((c) => ({ ...c, scene: { ...(c.scene || {}), show_hill_background: e.target.checked } }))
                }
              />
              Atmospheric page gradient (off = flat color)
            </label>
          </div>
        </div>
      )}
    </section>
  );
}
