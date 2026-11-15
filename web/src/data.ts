// Loading of the static files published by the data build.

export const DATA_URL: string =
  import.meta.env.VITE_DATA_URL ?? "https://audaciousleopardpouncespublicly.s3.us-east-005.backblazeb2.com/web/v1";

export interface SeriesInfo {
  file: string;
  first: string;
  last: string;
  count: number;
  max: number;
}

export interface Station {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  mobile: boolean;
  note: string | null;
  api: SeriesInfo | null;
  web: SeriesInfo | null;
}

export interface StationIndex {
  epoch: string;
  end: string;
  api: { step_minutes: number; length: number };
  web: { step_minutes: number; length: number };
  snapshots: { step_minutes: number; months: string[] };
  stations: Station[];
}

export interface Overpass {
  orbit: number;
  start: string;
  end: string;
  pixels: number;
  good_pixels: number;
  max_du: number | null;
  image: string;
  outside?: boolean;
  startMs: number;
  endMs: number;
}

export interface TropomiIndex {
  bbox: [number, number, number, number];
  legend: { max: number; stops: { at: number; rgba: number[] }[] };
  overpasses: Overpass[];
}

export const MINUTE = 60_000;
export const STEP = 10 * MINUTE; // the grid everything is shown on

async function getJson<T>(name: string): Promise<T> {
  const response = await fetch(`${DATA_URL}/${name}`);
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  return response.json();
}

async function getFloats(name: string): Promise<Float32Array> {
  const response = await fetch(`${DATA_URL}/${name}`);
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  return new Float32Array(await response.arrayBuffer());
}

export async function loadIndexes(): Promise<{ stations: StationIndex; tropomi: TropomiIndex }> {
  const [stations, tropomi] = await Promise.all([
    getJson<StationIndex>("stations.json"),
    getJson<TropomiIndex>("tropomi/index.json"),
  ]);
  tropomi.overpasses = tropomi.overpasses
    .filter((o) => !o.outside && o.start)
    .map((o) => ({ ...o, startMs: Date.parse(o.start), endMs: Date.parse(o.end) }));
  return { stations, tropomi };
}

export interface StationSeries {
  /** Seconds since 1970 for each 10-minute slot (interval end). */
  time: number[];
  /** Hourly API value, held across the hour it covers. */
  api: (number | null)[];
  /** Website download: 10-minute values, or the hourly value held across its hour. */
  web: (number | null)[];
}

const seriesCache = new Map<string, Promise<StationSeries>>();

export function loadSeries(index: StationIndex, station: Station): Promise<StationSeries> {
  let cached = seriesCache.get(station.id);
  if (!cached) {
    cached = buildSeries(index, station);
    seriesCache.set(station.id, cached);
  }
  return cached;
}

async function buildSeries(index: StationIndex, station: Station): Promise<StationSeries> {
  const n = index.web.length;
  const epoch = Date.parse(index.epoch) / 1000;
  const [apiRaw, webRaw] = await Promise.all([
    station.api ? getFloats(station.api.file) : null,
    station.web ? getFloats(station.web.file) : null,
  ]);
  const time = new Array<number>(n);
  const api = new Array<number | null>(n).fill(null);
  const web = new Array<number | null>(n).fill(null);
  for (let i = 0; i < n; i++) time[i] = epoch + i * 600;
  if (apiRaw) {
    // Slot i lies in the hour that ends at index ceil(i / 6).
    for (let i = 0; i < n; i++) {
      const v = apiRaw[Math.min(Math.ceil(i / 6), apiRaw.length - 1)];
      if (!Number.isNaN(v)) api[i] = v;
    }
  }
  if (webRaw) {
    for (let i = 0; i < n; i++) if (!Number.isNaN(webRaw[i])) web[i] = webRaw[i];
    // An hourly row with no 10-minute neighbours is held across the hour it covers.
    for (let h = 6; h < n; h += 6) {
      if (web[h] === null) continue;
      let alone = true;
      for (let k = 1; k < 6; k++) if (web[h - k] !== null) alone = false;
      if (alone) for (let k = 1; k < 6; k++) web[h - k] = web[h];
    }
  }
  return { time, api, web };
}

const snapshotCache = new Map<string, Promise<Float32Array | null>>();

/** Best available value of every station at a time, or null when that month has no data. */
export async function loadSnapshotRow(index: StationIndex, timeMs: number): Promise<Float32Array | null> {
  const date = new Date(timeMs - 1); // a slot ending at midnight belongs to the month before
  const month = `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
  if (!index.snapshots.months.includes(month)) return null;
  let cached = snapshotCache.get(month);
  if (!cached) {
    cached = getFloats(`snapshots/${month}.f32`).catch(() => null);
    snapshotCache.set(month, cached);
  }
  const table = await cached;
  if (!table) return null;
  const columns = index.stations.length;
  const monthStart = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
  const row = Math.round((timeMs - monthStart) / STEP);
  if (row < 0 || (row + 1) * columns > table.length) return null;
  return table.subarray(row * columns, (row + 1) * columns);
}

export function formatTime(ms: number | null): string {
  if (ms === null) return "–";
  return new Date(ms).toISOString().slice(0, 16).replace("T", " ");
}
