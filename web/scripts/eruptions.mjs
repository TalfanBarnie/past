// Copies the eruption windows from the project config into the app.
import { readFileSync, writeFileSync } from "node:fs";
import { parse } from "yaml";

const config = parse(readFileSync(new URL("../../config/project.yaml", import.meta.url), "utf8"));
const iso = (t) => (t instanceof Date ? t.toISOString() : new Date(`${t}Z`).toISOString());
const eruptions = Object.entries(config.eruptions).map(([id, e]) => ({
  id,
  start: iso(e.start),
  end: iso(e.end),
  latitude: e.vent.lat,
  longitude: e.vent.lon,
}));
writeFileSync(new URL("../src/eruptions.json", import.meta.url), JSON.stringify(eruptions, null, 1));
console.log(`${eruptions.length} eruptions written`);
