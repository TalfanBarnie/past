"""Build the TROPOMI overpass index and map images for the web viewer.

For every orbit subset in the private bucket this finds the pixels inside the
CARRA rectangle, records when the first and last of them were observed, and
draws the SO2 column of the good-quality pixels, for each assumed plume height,
as a transparent PNG in Web Mercator. All images share one bounding box.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import netCDF4
import numpy as np

from .. import config
from .store import Sink, Source

DU_PER_MOL_M2 = 2241.15
QA_MIN = 0.5
# Columns retrieved for a plume assumed at 7 km or at 1 km. The instrument is less sensitive near
# the ground, so the 1 km column is larger and several times noisier; each has its own colour scale.
PRODUCTS = {
    "7km": {"label": "SO2 column for a plume at 7 km", "variable": "sulfurdioxide_total_vertical_column_7km", "max": 10.0},
    "1km": {"label": "SO2 column for a plume at 1 km", "variable": "sulfurdioxide_total_vertical_column_1km", "max": 20.0},
}
IMAGE_WIDTH = 1024
PROCESSING = 3  # bump to rebuild every image
# Light to dark purple. Retrieval noise is a couple of DU, so columns near zero are almost
# transparent (enough to show where there is coverage) and opacity rises with the column.
RAMP = [(0.0, (225, 215, 240, 28)), (0.25, (190, 160, 215, 60)), (0.5, (140, 90, 190, 175)), (0.75, (95, 40, 150, 225)), (1.0, (50, 0, 85, 245))]
NAME = re.compile(r"S5P_(?P<stream>\w{4})_L2__SO2____(?P<start>\d{8}T\d{6})_\d{8}T\d{6}_(?P<orbit>\d{5})_")


# --- geometry ---------------------------------------------------------------


def lcc_xy(lat, lon, w: dict):
    """Lambert conformal conic (one standard parallel, sphere): metres from the pole-centred origin."""
    n = math.sin(math.radians(w["lat0"]))
    f = math.cos(math.radians(w["lat0"])) * math.tan(math.pi / 4 + math.radians(w["lat0"]) / 2) ** n / n
    rho = w["earth_radius_m"] * f / np.tan(np.pi / 4 + np.radians(lat) / 2) ** n
    theta = n * np.radians((np.asarray(lon) - w["lon0"] + 180.0) % 360.0 - 180.0)
    return rho * np.sin(theta), -rho * np.cos(theta)


def in_window(lat, lon, w: dict) -> np.ndarray:
    """True where a point falls inside the cached CARRA rectangle."""
    x0, y0 = lcc_xy(w["corner_lat"], w["corner_lon"], w)
    x, y = lcc_xy(lat, lon, w)
    i, j = (x - x0) / w["dx_m"], (y - y0) / w["dx_m"]
    return (i >= -0.5) & (i <= w["nx"] - 0.5) & (j >= -0.5) & (j <= w["ny"] - 0.5)


def window_bbox(w: dict) -> list[float]:
    """West, south, east, north of the rectangle, rounded outwards to 0.05 degrees."""
    x0, y0 = lcc_xy(w["corner_lat"], w["corner_lon"], w)
    n = math.sin(math.radians(w["lat0"]))
    f = math.cos(math.radians(w["lat0"])) * math.tan(math.pi / 4 + math.radians(w["lat0"]) / 2) ** n / n
    i = np.linspace(-0.5, w["nx"] - 0.5, 200)
    j = np.linspace(-0.5, w["ny"] - 0.5, 200)
    ex = np.concatenate([i, i, np.full_like(j, i[0]), np.full_like(j, i[-1])]) * w["dx_m"] + x0
    ey = np.concatenate([np.full_like(i, j[0]), np.full_like(i, j[-1]), j, j]) * w["dx_m"] + y0
    rho = np.hypot(ex, ey)
    lon = w["lon0"] + np.degrees(np.arctan2(ex, -ey)) / n
    lat = np.degrees(2 * np.arctan((w["earth_radius_m"] * f / rho) ** (1 / n)) - np.pi / 2)
    r = lambda v, up: (math.ceil(v * 20) if up else math.floor(v * 20)) / 20  # noqa: E731
    return [r(lon.min(), False), r(lat.min(), False), r(lon.max(), True), r(lat.max(), True)]


def mercator_y(lat):
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def image_size(bbox) -> tuple[int, int]:
    west, south, east, north = bbox
    height = IMAGE_WIDTH * (mercator_y(north) - mercator_y(south)) / math.radians(east - west)
    return IMAGE_WIDTH, int(round(height))


# --- one orbit --------------------------------------------------------------


def colours(du: np.ndarray, scale_max: float) -> np.ndarray:
    t = np.sqrt(np.clip(du / scale_max, 0.0, 1.0))
    stops = np.array([s for s, _ in RAMP])
    rgba = np.array([c for _, c in RAMP], dtype=float)
    return np.stack([np.interp(t, stops, rgba[:, k]) for k in range(4)], axis=-1).round().astype(np.uint8)


def process(path: Path, window: dict, bbox) -> tuple[dict, dict[str, bytes]] | None:
    """Overpass summary and one PNG per product for a subset file, or None when no pixel is inside the rectangle."""
    from PIL import Image, ImageDraw

    with netCDF4.Dataset(path) as ds:
        product = ds["PRODUCT"]
        lat = np.ma.filled(product["latitude"][0], np.nan)
        lon = np.ma.filled(product["longitude"][0], np.nan)
        inside = in_window(lat, lon, window) & np.isfinite(lat)
        if not inside.any():
            return None
        reference = datetime.strptime(product["delta_time"].units.split("since ")[1][:19], "%Y-%m-%d %H:%M:%S")
        delta = np.ma.filled(product["delta_time"][0].astype(float), np.nan)
        if delta.ndim == 1:
            delta = np.broadcast_to(delta[:, None], lat.shape)
        seen = delta[inside]
        detail = product["SUPPORT_DATA/DETAILED_RESULTS"]
        columns = {k: np.ma.filled(detail[v["variable"]][0], np.nan) * DU_PER_MOL_M2 for k, v in PRODUCTS.items()}
        good = inside & (np.ma.filled(product["qa_value"][0], 0.0) > QA_MIN)
        for du in columns.values():
            good &= np.isfinite(du)
        geo = product["SUPPORT_DATA/GEOLOCATIONS"]
        corner_lat = np.ma.filled(geo["latitude_bounds"][0], np.nan)[good]
        corner_lon = np.ma.filled(geo["longitude_bounds"][0], np.nan)[good]

    west, south, east, north = bbox
    width, height = image_size(bbox)
    px = (corner_lon - west) / (east - west) * width
    py = (mercator_y(north) - mercator_y(corner_lat)) / (mercator_y(north) - mercator_y(south)) * height
    drawable = np.isfinite(px).all(axis=1) & np.isfinite(py).all(axis=1)
    corners = [list(zip(px[k].tolist(), py[k].tolist())) for k in range(len(px))]
    stamp = lambda ms: (reference + timedelta(milliseconds=float(ms))).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    orbit = int(NAME.match(path.name)["orbit"])
    meta = {
        "orbit": orbit,
        "stream": NAME.match(path.name)["stream"],
        "start": stamp(np.nanmin(seen)),
        "end": stamp(np.nanmax(seen)),
        "pixels": int(inside.sum()),
        "good_pixels": int(good.sum()),
        "max_du": {},
        "images": {},
        "processing": PROCESSING,
    }
    pngs = {}
    for key, spec in PRODUCTS.items():
        values = columns[key][good]
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        rgba = colours(values, spec["max"])
        for k in np.argsort(values):  # highest columns drawn last, on top
            if drawable[k]:
                draw.polygon(corners[k], fill=tuple(int(c) for c in rgba[k]))
        buffer = io.BytesIO()
        image.save(buffer, "PNG", optimize=True)
        meta["max_du"][key] = round(float(values.max()), 1) if values.size else None
        meta["images"][key] = f"tropomi/img/{orbit}_{key}.png"
        pngs[key] = buffer.getvalue()
    return meta, pngs


# --- all orbits -------------------------------------------------------------


def write_index(sink: Sink, window: dict, bbox, overpasses: dict) -> None:
    width, height = image_size(bbox)
    index = {
        "version": 1,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "units": "DU",
        "products": [
            {"id": k, "label": v["label"], "legend": {"scale": "sqrt", "min": 0.0, "max": v["max"], "stops": [{"at": s, "rgba": list(c)} for s, c in RAMP]}}
            for k, v in PRODUCTS.items()
        ],
        "qa_min": QA_MIN,
        "bbox": bbox,
        "image_size": [width, height],
        "projection": "Web Mercator; every image covers bbox exactly",
        "times": "start and end are when the first and last pixels inside the CARRA rectangle were observed (UTC)",
        "overpasses": sorted(overpasses.values(), key=lambda o: o.get("start", "")),
    }
    sink.write("tropomi/index.json", json.dumps(index).encode(), "application/json", compress=True)


def build(source: Source, sink: Sink, budget_hours: float, limit: int | None = None) -> int:
    window = config.load()["carra_window"]
    bbox = window_bbox(window)
    previous = sink.read("tropomi/index.json")
    # Entries from an older processing version stay in the index until their image is redrawn,
    # so the published list never shrinks while a rebuild is under way.
    overpasses = {o["orbit"]: o for o in json.loads(previous)["overpasses"]} if previous else {}
    keys = [k for k in source.list("tropomi/so2/") if k.endswith(".subset.nc")]
    current = {orbit for orbit, o in overpasses.items() if o.get("processing") == PROCESSING}
    todo = [k for k in keys if int(NAME.match(Path(k).name)["orbit"]) not in current]
    print(f"{len(keys)} orbit files, {len(todo)} to process", flush=True)
    started, done = time.time(), 0
    for key in todo[:limit]:
        if time.time() - started > budget_hours * 3600:
            break
        with tempfile.TemporaryDirectory() as tmp:
            result = process(source.download(key, Path(tmp) / Path(key).name), window, bbox)
        orbit = int(NAME.match(Path(key).name)["orbit"])
        if result is None:
            overpasses[orbit] = {"orbit": orbit, "start": "", "processing": PROCESSING, "outside": True}
        else:
            meta, pngs = result
            for product, png in pngs.items():
                sink.write(meta["images"][product], png, "image/png", max_age=86400)
            overpasses[orbit] = meta
        done += 1
        if done % 50 == 0:
            write_index(sink, window, bbox, overpasses)
            print(f"{done} of {len(todo)} processed", flush=True)
    # Orbits that never enter the rectangle are kept, marked "outside", so they are not processed again.
    write_index(sink, window, bbox, overpasses)
    return len(todo) - done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--private-dir", type=Path, help="read from this folder instead of the private bucket (testing)")
    ap.add_argument("--out-dir", type=Path, help="write to this folder instead of the public bucket (testing)")
    ap.add_argument("--prefix", default="web/v1")
    ap.add_argument("--budget-hours", type=float, default=5.0)
    ap.add_argument("--limit", type=int, help="process at most this many orbits")
    args = ap.parse_args(argv)
    left = build(Source(args.private_dir), Sink(args.prefix, args.out_dir), args.budget_hours, args.limit)
    print(f"{left} orbit files left to process", flush=True)


if __name__ == "__main__":
    main()
