import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { StationSeries } from "./data";
import eruptions from "./eruptions.json";
import { getState, setState, setTime, toggleOrbit, useStore } from "./store";

const API_COLOUR = "#d97706";
const WEB_COLOUR = "#2563eb";
const pad = (n: number) => String(n).padStart(2, "0");

/** Axis labels in UTC, year-month-day order, as detailed as the visible span needs. */
function timeLabels(u: uPlot, splits: number[]): string[] {
  const span = u.scales.x.max! - u.scales.x.min!;
  let previousDay = "";
  return splits.map((t) => {
    const d = new Date(t * 1000);
    const day = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
    if (span > 500 * 86400) return day.slice(0, 7);
    if (span > 3 * 86400) return day;
    const clock = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
    const label = day === previousDay ? clock : `${clock}\n${day}`;
    previousDay = day;
    return label;
  });
}

const MAX_LINES = 400; // overpass lines are hidden when more than this many are in view

/** The overpass whose line is within a few pixels of x, preferring ones with usable pixels. */
function overpassNear(u: uPlot, x: number): number | null {
  const tropomi = getState().tropomi;
  if (!tropomi) return null;
  let best: { orbit: number; score: number } | null = null;
  for (const o of tropomi.overpasses) {
    const distance = Math.abs(u.valToPos(o.startMs / 1000, "x") - x);
    if (distance > 5) continue;
    const score = distance + (o.good_pixels ? 0 : 3);
    if (!best || score < best.score) best = { orbit: o.orbit, score };
  }
  return best ? best.orbit : null;
}

function drawMarkers(u: uPlot): void {
  const { tropomi, time, orbit } = getState();
  const ctx = u.ctx;
  const { left, top, width, height } = u.bbox;
  const min = u.scales.x.min!;
  const max = u.scales.x.max!;
  const xOf = (seconds: number) => Math.round(u.valToPos(seconds, "x", true));
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top, width, height);
  ctx.clip();

  // Eruption periods as a faint band.
  ctx.fillStyle = "rgba(220, 38, 38, 0.06)";
  for (const e of eruptions) {
    const a = Date.parse(e.start) / 1000;
    const b = Date.parse(e.end) / 1000;
    if (b < min || a > max) continue;
    ctx.fillRect(xOf(Math.max(a, min)), top, Math.max(1, xOf(Math.min(b, max)) - xOf(Math.max(a, min))), height);
  }

  // TROPOMI overpasses as vertical lines.
  if (tropomi) {
    const visible = tropomi.overpasses.filter((o) => o.startMs / 1000 >= min && o.startMs / 1000 <= max);
    if (visible.length <= MAX_LINES) {
      for (const o of visible) {
        if (o.orbit === orbit) continue;
        ctx.strokeStyle = o.good_pixels ? "rgba(75, 85, 99, 0.45)" : "rgba(75, 85, 99, 0.16)";
        ctx.lineWidth = devicePixelRatio;
        ctx.beginPath();
        ctx.moveTo(xOf(o.startMs / 1000) + 0.5, top);
        ctx.lineTo(xOf(o.startMs / 1000) + 0.5, top + height);
        ctx.stroke();
      }
    }
    const selected = tropomi.overpasses.find((o) => o.orbit === orbit);
    if (selected) {
      ctx.strokeStyle = "#581c87";
      ctx.lineWidth = 3 * devicePixelRatio;
      ctx.beginPath();
      ctx.moveTo(xOf(selected.startMs / 1000), top);
      ctx.lineTo(xOf(selected.startMs / 1000), top + height);
      ctx.stroke();
    }
  }

  // The selected time.
  const x = xOf(time / 1000);
  ctx.strokeStyle = "#111827";
  ctx.lineWidth = 1.5 * devicePixelRatio;
  ctx.setLineDash([6 * devicePixelRatio, 3 * devicePixelRatio]);
  ctx.beginPath();
  ctx.moveTo(x, top);
  ctx.lineTo(x, top + height);
  ctx.stroke();
  ctx.restore();
}

export function Chart({ series }: { series: StationSeries }) {
  const holder = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot>();
  const range = useStore((s) => s.range);
  const time = useStore((s) => s.time);
  const orbit = useStore((s) => s.orbit);
  const tropomi = useStore((s) => s.tropomi);

  useEffect(() => {
    const element = holder.current!;
    let downX = 0;
    const options: uPlot.Options = {
      width: element.clientWidth,
      height: 190,
      cursor: { drag: { x: true, y: false, setScale: true }, sync: { key: "past" } },
      legend: { live: true },
      scales: { x: { time: true }, y: { range: (_u, lo, hi) => [Math.min(0, lo), Math.max(10, hi * 1.08)] } },
      axes: [
        { stroke: "#4b5563", grid: { stroke: "#eef0f3" }, ticks: { stroke: "#d1d5db" }, values: timeLabels, space: 90 },
        { stroke: "#4b5563", grid: { stroke: "#eef0f3" }, ticks: { stroke: "#d1d5db" }, size: 52 },
      ],
      series: [
        { value: (_u, v) => (v == null ? "–" : new Date(v * 1000).toISOString().slice(0, 16).replace("T", " ")) },
        { label: "API archive (hourly)", stroke: API_COLOUR, width: 1.5, points: { show: false } },
        { label: "Website (10-minute)", stroke: WEB_COLOUR, width: 1, points: { show: false } },
      ],
      tzDate: (ts) => uPlot.tzDate(new Date(ts * 1000), "UTC"),
      hooks: {
        draw: [drawMarkers],
        setScale: [
          (u, key) => {
            if (key !== "x") return;
            const next: [number, number] = [u.scales.x.min! * 1000, u.scales.x.max! * 1000];
            const [a, b] = getState().range;
            if (Math.abs(next[0] - a) > 500 || Math.abs(next[1] - b) > 500) setState({ range: next });
          },
        ],
      },
    };
    const [a, b] = getState().range;
    const u = new uPlot(options, [series.time, series.api, series.web] as uPlot.AlignedData, element);
    u.setScale("x", { min: a / 1000, max: b / 1000 });
    plot.current = u;

    // A click (not a drag) picks an overpass if one is under the pointer, otherwise sets the time.
    const down = (e: MouseEvent) => (downX = e.clientX);
    const up = (e: MouseEvent) => {
      if (Math.abs(e.clientX - downX) > 3) return;
      const x = e.clientX - u.over.getBoundingClientRect().left;
      const hit = overpassNear(u, x);
      if (hit !== null) toggleOrbit(hit);
      else setTime(u.posToVal(x, "x") * 1000);
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const x = e.clientX - u.over.getBoundingClientRect().left;
      const centre = u.posToVal(x, "x") * 1000;
      const [lo, hi] = getState().range;
      const factor = e.deltaY > 0 ? 1.3 : 1 / 1.3;
      const width = Math.max(3_600_000, (hi - lo) * factor);
      const share = (centre - lo) / (hi - lo);
      setState({ range: [centre - share * width, centre + (1 - share) * width] });
    };
    u.over.addEventListener("mousedown", down);
    u.over.addEventListener("mouseup", up);
    u.over.addEventListener("wheel", wheel, { passive: false });
    const observer = new ResizeObserver(() => u.setSize({ width: element.clientWidth, height: 190 }));
    observer.observe(element);
    return () => {
      observer.disconnect();
      u.destroy();
    };
  }, [series]);

  useEffect(() => {
    const u = plot.current;
    if (!u) return;
    if (Math.abs(u.scales.x.min! * 1000 - range[0]) > 500 || Math.abs(u.scales.x.max! * 1000 - range[1]) > 500)
      u.setScale("x", { min: range[0] / 1000, max: range[1] / 1000 });
  }, [range]);

  useEffect(() => plot.current?.redraw(false), [time, orbit, tropomi]);

  return <div ref={holder} className="chart" />;
}
