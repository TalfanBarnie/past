import { useEffect, useRef, useState } from "react";
import Feature from "ol/Feature";
import OlMap from "ol/Map";
import View from "ol/View";
import Point from "ol/geom/Point";
import ImageLayer from "ol/layer/Image";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import { fromLonLat, get as getProjection, transformExtent } from "ol/proj";
import { register } from "ol/proj/proj4";
import ImageStatic from "ol/source/ImageStatic";
import VectorSource from "ol/source/Vector";
import XYZ from "ol/source/XYZ";
import { Circle, Fill, RegularShape, Stroke, Style } from "ol/style";
import TileGrid from "ol/tilegrid/TileGrid";
import proj4 from "proj4";
import "ol/ol.css";
import { INACTIVE, SCALE_MAX, SCALE_MIN, stationColour, stationGradient } from "./colors";
import { DATA_URL, formatTime, loadSnapshotRow } from "./data";
import eruptions from "./eruptions.json";
import { openCard, setState, useStore } from "./store";

// ISN93, the projection of the Landmælingar Íslands map tiles.
proj4.defs(
  "EPSG:3057",
  "+proj=lcc +lat_1=64.25 +lat_2=65.75 +lat_0=65 +lon_0=-19 +x_0=500000 +y_0=500000 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs",
);
register(proj4);
getProjection("EPSG:3057")!.setExtent([-600000, -400000, 1700000, 1500000]);

const TILE_SIZE = 1048576; // metres covered by the tile grid from its origin
const baseSource = new XYZ({
  url: "https://gis.lmi.is/mapcache/wmts/1.0.0/LMI_Kort/default/EPSG:3057/{z}/{y}/{x}.png",
  projection: "EPSG:3057",
  crossOrigin: "anonymous",
  attributions: "Background map © Landmælingar Íslands / Náttúrufræðistofnun",
  tileGrid: new TileGrid({
    origin: [0, TILE_SIZE],
    extent: [0, 0, TILE_SIZE, TILE_SIZE],
    resolutions: Array.from({ length: 16 }, (_, z) => 1024 * 2 ** (5 - z)),
    tileSize: 512,
    minZoom: 5,
  }),
});

function stationStyle(feature: Feature): Style {
  const value = feature.get("value") as number | null;
  const hover = feature.get("hover") as boolean;
  const open = feature.get("open") as boolean;
  const radius = hover ? 10 : 7;
  const fill = new Fill({ color: stationColour(value) });
  const stroke = new Stroke({ color: hover || open ? "#111827" : "#ffffff", width: hover ? 3 : open ? 2.5 : 1.5 });
  // Mobile stations are drawn as diamonds: their listed position may not hold for the whole record.
  const image = feature.get("mobile")
    ? new RegularShape({ points: 4, radius: radius + 2, fill, stroke })
    : new Circle({ radius, fill, stroke });
  return new Style({ image, zIndex: hover ? 3 : open ? 2 : 1 });
}

function ventStyle(feature: Feature): Style {
  const active = feature.get("active") as boolean;
  return new Style({
    image: new RegularShape({
      points: 3,
      radius: active ? 9 : 6,
      fill: new Fill({ color: active ? "#111827" : "rgba(17,24,39,0.25)" }),
      stroke: new Stroke({ color: "#ffffff", width: 1.5 }),
    }),
  });
}

export function MapView() {
  const element = useRef<HTMLDivElement>(null);
  const layers = useRef<{ stations: VectorSource; vents: VectorSource; tropomi: ImageLayer<ImageStatic> }>();
  const stations = useStore((s) => s.stations);
  const tropomi = useStore((s) => s.tropomi);
  const time = useStore((s) => s.time);
  const hover = useStore((s) => s.hover);
  const cards = useStore((s) => s.cards);
  const orbit = useStore((s) => s.orbit);
  const [row, setRow] = useState<Float32Array | null>(null);

  useEffect(() => {
    const stationSource = new VectorSource();
    const ventSource = new VectorSource();
    const tropomiLayer = new ImageLayer<ImageStatic>({ opacity: 0.95 });
    const map = new OlMap({
      target: element.current!,
      layers: [
        new TileLayer({ source: baseSource }),
        tropomiLayer,
        new VectorLayer({ source: ventSource, style: (f) => ventStyle(f as Feature) }),
        new VectorLayer({ source: stationSource, style: (f) => stationStyle(f as Feature) }),
      ],
      view: new View({ projection: "EPSG:3057", center: fromLonLat([-22.15, 63.97], "EPSG:3057"), resolution: 220, minResolution: 4, maxResolution: 4000 }),
    });
    const stationAt = (pixel: number[]) =>
      map.forEachFeatureAtPixel(pixel, (f) => f.get("station") as string | undefined, { hitTolerance: 4 }) ?? null;
    map.on("pointermove", (event) => {
      if (event.dragging) return;
      const id = stationAt(event.pixel);
      const target = map.getTargetElement();
      if (target) target.style.cursor = id ? "pointer" : "";
      setState({ hover: id });
    });
    map.on("click", (event) => {
      const id = stationAt(event.pixel);
      if (id) openCard(id);
    });
    for (const e of eruptions) {
      const feature = new Feature(new Point(fromLonLat([e.longitude, e.latitude], "EPSG:3057")));
      feature.setProperties({ start: Date.parse(e.start), end: Date.parse(e.end), active: false });
      ventSource.addFeature(feature);
    }
    layers.current = { stations: stationSource, vents: ventSource, tropomi: tropomiLayer };
    const observer = new ResizeObserver(() => map.updateSize());
    observer.observe(element.current!);
    return () => {
      observer.disconnect();
      map.setTarget(undefined);
    };
  }, []);

  // Station markers.
  useEffect(() => {
    if (!stations || !layers.current) return;
    const source = layers.current.stations;
    source.clear();
    for (const s of stations.stations) {
      const feature = new Feature(new Point(fromLonLat([s.longitude, s.latitude], "EPSG:3057")));
      feature.setId(s.id);
      feature.setProperties({ station: s.id, mobile: s.mobile, value: null, hover: false, open: false });
      source.addFeature(feature);
    }
  }, [stations]);

  // Values at the selected time.
  useEffect(() => {
    if (!stations) return;
    let current = true;
    loadSnapshotRow(stations, time).then((r) => current && setRow(r));
    return () => {
      current = false;
    };
  }, [stations, time]);

  useEffect(() => {
    if (!stations || !layers.current) return;
    stations.stations.forEach((s, k) => {
      const feature = layers.current!.stations.getFeatureById(s.id);
      const value = row ? row[k] : NaN;
      feature?.setProperties({ value: Number.isNaN(value) ? null : value, hover: hover === s.id, open: cards.includes(s.id) });
    });
    layers.current.vents.getFeatures().forEach((f) => f.set("active", time >= f.get("start") && time <= f.get("end")));
  }, [stations, row, hover, cards, time]);

  // The selected TROPOMI overpass.
  const overpass = tropomi && orbit !== null ? tropomi.overpasses.find((o) => o.orbit === orbit) ?? null : null;
  useEffect(() => {
    if (!layers.current || !tropomi) return;
    layers.current.tropomi.setSource(
      overpass
        ? new ImageStatic({
            url: `${DATA_URL}/${overpass.image}`,
            projection: "EPSG:3857",
            imageExtent: transformExtent(tropomi.bbox, "EPSG:4326", "EPSG:3857"),
            crossOrigin: "anonymous",
            interpolate: false,
          })
        : null,
    );
  }, [tropomi, overpass]);

  const legendStops = tropomi?.legend.stops ?? [];
  return (
    <div className="map">
      <div ref={element} className="map-canvas" />
      <div className="map-caption">
        <div>
          Date and time of Tropomi image:{" "}
          {overpass ? `start ${formatTime(overpass.startMs)}, end ${formatTime(overpass.endMs)}` : "none selected"}
        </div>
        <div>Date and time of ground station data {formatTime(time)}</div>
      </div>
      <div className="map-legend">
        <div className="legend-title">Ground SO₂ (µg/m³)</div>
        <div className="legend-bar" style={{ background: stationGradient() }} />
        <div className="legend-ticks">
          {[1, 10, 100, 1000].map((v) => (
            <span key={v} style={{ left: `${(Math.log(v / SCALE_MIN) / Math.log(SCALE_MAX / SCALE_MIN)) * 100}%` }}>
              {v}
            </span>
          ))}
        </div>
        <div className="legend-row">
          <span className="swatch" style={{ background: INACTIVE }} /> no data
          <span className="swatch diamond" /> mobile station
          <span className="swatch vent" /> eruption site
        </div>
        {overpass && (
          <>
            <div className="legend-title">TROPOMI SO₂ column, 1 km (DU)</div>
            <div
              className="legend-bar"
              style={{
                background: `linear-gradient(to right, ${legendStops
                  .map((s) => `rgba(${s.rgba[0]},${s.rgba[1]},${s.rgba[2]},${(s.rgba[3] / 255).toFixed(2)}) ${s.at * 100}%`)
                  .join(", ")})`,
              }}
            />
            <div className="legend-ticks">
              {[0, 1, 5, 10, 20].map((v) => (
                <span key={v} style={{ left: `${Math.sqrt(v / tropomi!.legend.max) * 100}%` }}>
                  {v}
                </span>
              ))}
            </div>
            <div className="legend-row">
              {overpass.good_pixels
                ? `${overpass.good_pixels} good-quality pixels, highest ${overpass.max_du} DU`
                : "no good-quality pixels in this overpass"}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
