import { useEffect, useRef, useState } from "react";
import { Chart } from "./Chart";
import { loadSeries, STEP, type Station, type StationSeries } from "./data";
import { setState, toggleCard, useStore } from "./store";

function Card({ station }: { station: Station }) {
  const index = useStore((s) => s.stations)!;
  const time = useStore((s) => s.time);
  const highlighted = useStore((s) => s.hover === station.id);
  const [series, setSeries] = useState<StationSeries | null>(null);
  const [failed, setFailed] = useState(false);
  const element = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let current = true;
    loadSeries(index, station).then(
      (s) => current && setSeries(s),
      () => current && setFailed(true),
    );
    return () => {
      current = false;
    };
  }, [index, station]);

  // Bring the card into view when its marker is hovered on the map.
  useEffect(() => {
    if (highlighted && !element.current?.matches(":hover")) element.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [highlighted]);

  const slot = Math.round((time - Date.parse(index.epoch)) / STEP);
  const at = (values: (number | null)[] | undefined) => {
    const v = values?.[slot];
    return v == null ? "–" : v.toFixed(1);
  };
  return (
    <div
      ref={element}
      className={`card${highlighted ? " highlighted" : ""}`}
      onMouseEnter={() => setState({ hover: station.id })}
      onMouseLeave={() => setState({ hover: null })}
    >
      <div className="card-head">
        <div className="card-title">
          {station.name}
          {station.mobile && <span className="badge" title="The listed position may not hold for the whole record">mobile</span>}
        </div>
        <div className="card-values">
          <span className="api">API {at(series?.api)}</span>
          <span className="web">Website {at(series?.web)}</span>
          <span className="unit">µg/m³</span>
        </div>
        <button className="close" aria-label={`Close ${station.name}`} onClick={() => toggleCard(station.id)}>
          ×
        </button>
      </div>
      {failed ? <div className="card-note">Could not load this station's data.</div> : series ? <Chart series={series} /> : <div className="card-note">Loading…</div>}
      {station.note && <div className="card-note">{station.note}</div>}
    </div>
  );
}

export function Cards() {
  const index = useStore((s) => s.stations);
  const cards = useStore((s) => s.cards);
  if (!index) return null;
  const open = cards.map((id) => index.stations.find((s) => s.id === id)).filter((s): s is Station => Boolean(s));
  const closed = index.stations.filter((s) => !cards.includes(s.id));
  return (
    <div className="cards">
      <div className="cards-help">
        <select value="" onChange={(e) => e.target.value && toggleCard(e.target.value)} aria-label="Add a station">
          <option value="">Add a station…</option>
          {closed.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <span>
          Click a graph to set the time, drag to zoom, scroll to zoom in or out, double-click for the whole record. Grey
          lines are TROPOMI overpasses: click one to show its image.
        </span>
      </div>
      {open.length === 0 && <div className="empty">Click a station on the map, or pick one above, to add its graph here.</div>}
      {open.map((s) => (
        <Card key={s.id} station={s} />
      ))}
      <div className="credits">
        Ground station data: Umhverfis- og orkustofnun (Environment and Energy Agency of Iceland) and the station
        operators, from api.ust.is and loftgæði.is. The website series are unvalidated measurements. Satellite images
        contain modified Copernicus Sentinel-5P data.
      </div>
    </div>
  );
}
