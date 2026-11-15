// A small shared store: selected time, open cards, hover, selected overpass and the visible time range.
import { useSyncExternalStore } from "react";
import { MINUTE, STEP, type StationIndex, type TropomiIndex } from "./data";

export interface State {
  stations: StationIndex | null;
  tropomi: TropomiIndex | null;
  error: string | null;
  time: number; // ms, end of the selected 10-minute interval
  range: [number, number]; // ms, visible on every graph
  cards: string[];
  hover: string | null;
  orbit: number | null;
}

let state: State = {
  stations: null,
  tropomi: null,
  error: null,
  time: Date.UTC(2024, 5, 2, 12),
  range: [Date.UTC(2024, 4, 28), Date.UTC(2024, 5, 26)],
  cards: [],
  hover: null,
  orbit: null,
};

const listeners = new Set<() => void>();

export function getState(): State {
  return state;
}

export function setState(patch: Partial<State>): void {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
  writeHash();
}

export function useStore<T>(select: (s: State) => T): T {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => select(state),
  );
}

export function snap(ms: number): number {
  return Math.round(ms / STEP) * STEP;
}

export function setTime(ms: number): void {
  const s = state.stations;
  const lo = s ? Date.parse(s.epoch) : -Infinity;
  const hi = s ? Date.parse(s.end) : Infinity;
  setState({ time: Math.min(hi, Math.max(lo, snap(ms))) });
}

export function toggleCard(id: string): void {
  setState({ cards: state.cards.includes(id) ? state.cards.filter((c) => c !== id) : [id, ...state.cards] });
}

export function openCard(id: string): void {
  if (!state.cards.includes(id)) setState({ cards: [id, ...state.cards] });
}

export function toggleOrbit(orbit: number): void {
  setState({ orbit: state.orbit === orbit ? null : orbit });
}

// --- the page address keeps the view, so it can be shared as a link ---------

let hashTimer: number | undefined;

function writeHash(): void {
  window.clearTimeout(hashTimer);
  hashTimer = window.setTimeout(() => {
    const iso = (ms: number) => new Date(ms).toISOString().slice(0, 16);
    const parts = [`t=${iso(state.time)}`, `from=${iso(state.range[0])}`, `to=${iso(state.range[1])}`];
    if (state.cards.length) parts.push(`cards=${state.cards.join(",")}`);
    if (state.orbit !== null) parts.push(`orbit=${state.orbit}`);
    history.replaceState(null, "", `#${parts.join("&")}`);
  }, 300);
}

export function readHash(): void {
  const params = new URLSearchParams(location.hash.slice(1));
  const time = (key: string) => {
    const value = params.get(key);
    const ms = value ? Date.parse(`${value}Z`) : NaN;
    return Number.isNaN(ms) ? null : ms;
  };
  const patch: Partial<State> = {};
  const t = time("t");
  const from = time("from");
  const to = time("to");
  if (t !== null) patch.time = snap(t);
  if (from !== null && to !== null && to - from >= 30 * MINUTE) patch.range = [from, to];
  const cards = params.get("cards");
  if (cards) patch.cards = cards.split(",").filter(Boolean);
  const orbit = Number(params.get("orbit"));
  if (params.get("orbit") && Number.isFinite(orbit)) patch.orbit = orbit;
  state = { ...state, ...patch };
}
