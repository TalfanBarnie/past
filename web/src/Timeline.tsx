import { STEP, formatTime } from "./data";
import eruptions from "./eruptions.json";
import { setState, setTime, useStore } from "./store";

const DAY = 86_400_000;

export function Timeline() {
  const index = useStore((s) => s.stations);
  const range = useStore((s) => s.range);
  const time = useStore((s) => s.time);
  if (!index) return null;
  const start = Date.parse(index.epoch);
  const end = Date.parse(index.end);
  const pct = (ms: number) => `${(Math.min(1, Math.max(0, (ms - start) / (end - start))) * 100).toFixed(3)}%`;
  const width = range[1] - range[0];
  const pan = (share: number) => setState({ range: [range[0] + share * width, range[1] + share * width] });
  const zoom = (factor: number) => {
    const centre = (range[0] + range[1]) / 2;
    const half = Math.max(1_800_000, (width * factor) / 2);
    setState({ range: [centre - half, centre + half] });
  };
  const years: number[] = [];
  for (let y = new Date(start).getUTCFullYear(); Date.UTC(y, 0, 1) < end; y++) years.push(y);
  return (
    <div className="timeline">
      <div className="timeline-buttons">
        <button onClick={() => pan(-0.5)} aria-label="Earlier">◀</button>
        <button onClick={() => pan(0.5)} aria-label="Later">▶</button>
        <button onClick={() => zoom(0.5)} aria-label="Zoom in">+</button>
        <button onClick={() => zoom(2)} aria-label="Zoom out">−</button>
        <button onClick={() => setState({ range: [start, end] })}>All</button>
        <button onClick={() => setTime(time - STEP)} aria-label="Ten minutes back">−10 min</button>
        <button onClick={() => setTime(time + STEP)} aria-label="Ten minutes forward">+10 min</button>
        <span className="timeline-time">{formatTime(time)} UTC</span>
      </div>
      <div
        className="timeline-strip"
        onClick={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const centre = start + ((e.clientX - box.left) / box.width) * (end - start);
          setState({ range: [centre - width / 2, centre + width / 2] });
        }}
      >
        {years.map((y) => (
          <span key={y} className="timeline-year" style={{ left: pct(Date.UTC(y, 0, 1)) }}>
            {y}
          </span>
        ))}
        {eruptions.map((e) => {
          const a = Date.parse(e.start);
          const b = Date.parse(e.end);
          return (
            <button
              key={e.id}
              className="timeline-eruption"
              title={`${e.id}: ${formatTime(a)} to ${formatTime(b)}`}
              style={{ left: pct(a), width: `max(4px, calc(${pct(b)} - ${pct(a)}))` }}
              onClick={(event) => {
                event.stopPropagation();
                setState({ range: [a - DAY, b + 3 * DAY] });
                setTime(a + 6 * 3_600_000);
              }}
            />
          );
        })}
        <div className="timeline-window" style={{ left: pct(range[0]), width: `max(2px, calc(${pct(range[1])} - ${pct(range[0])}))` }} />
        <div className="timeline-marker" style={{ left: pct(time) }} />
      </div>
    </div>
  );
}
