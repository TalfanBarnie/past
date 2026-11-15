// Colour scale for station markers: blue (low) to red (high), logarithmic in concentration.

const STOPS: [number, [number, number, number]][] = [
  [0.0, [33, 102, 172]],
  [0.25, [103, 169, 207]],
  [0.5, [253, 219, 140]],
  [0.75, [239, 138, 98]],
  [1.0, [178, 24, 43]],
];

export const SCALE_MIN = 1; // µg/m3
export const SCALE_MAX = 3000;
export const INACTIVE = "#b8bcc2";

export function stationColour(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return INACTIVE;
  const t = Math.min(1, Math.max(0, Math.log(Math.max(value, SCALE_MIN) / SCALE_MIN) / Math.log(SCALE_MAX / SCALE_MIN)));
  for (let k = 1; k < STOPS.length; k++) {
    const [t1, c1] = STOPS[k];
    if (t <= t1) {
      const [t0, c0] = STOPS[k - 1];
      const f = (t - t0) / (t1 - t0);
      return `rgb(${c0.map((c, i) => Math.round(c + f * (c1[i] - c)))})`;
    }
  }
  return `rgb(${STOPS[STOPS.length - 1][1]})`;
}

export function stationGradient(): string {
  return `linear-gradient(to right, ${STOPS.map(([t, c]) => `rgb(${c}) ${t * 100}%`).join(", ")})`;
}
