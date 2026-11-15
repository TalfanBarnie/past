"""Build the station files for the web viewer.

Two SO2 records are published per station, unmodified apart from time alignment:

  api  hourly values from the API archive (baseline-corrected by the agency)
  web  the website downloads, 10-minute where the station reports it, otherwise hourly

Every series is a little-endian Float32 array on a regular grid starting at EPOCH;
element i is the value whose averaging interval ends at EPOCH + i * step. Missing
values are NaN. Monthly "snapshot" files hold the best available value for every
station on the 10-minute grid, for colouring the map.
"""

from __future__ import annotations

import argparse
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from .store import Sink, Source

EPOCH = pd.Timestamp("2021-01-01T00:00:00")
BIN = "application/octet-stream"


def load_links() -> dict:
    return json.loads((config.CONFIG_PATH.parent / "stations.json").read_text())


def read_api(source: Source, years) -> pd.DataFrame:
    parts = []
    for year in years:
        key = f"stations/so2_{year}.parquet"
        if source.exists(key):
            parts.append(pd.read_parquet(io.BytesIO(source.read(key)), columns=["station_id", "time_end", "so2_ugm3"]))
    df = pd.concat(parts).dropna(subset=["so2_ugm3"])
    return df[df["time_end"] >= EPOCH]


def read_website_csv(data: bytes) -> pd.Series:
    """SO2 column of one website export, indexed by its time stamp. Numbers look like 1.213,7."""
    if len(data) < 10:
        return pd.Series(dtype=float)
    df = pd.read_csv(io.BytesIO(data), sep=";", decimal=",", thousands=".", encoding="utf-8-sig")
    cols = [c for c in df.columns if c.startswith("SO2")]
    if not cols:
        return pd.Series(dtype=float)
    s = pd.Series(pd.to_numeric(df[cols[0]], errors="coerce").values, index=pd.to_datetime(df.iloc[:, 0]))
    s = s.dropna().sort_index()
    return s[~s.index.duplicated()]


def website_series(source: Source, station: dict) -> pd.Series:
    """All years of a station's website download, shifted onto the API's time convention."""
    parts = []
    for entry in (station.get("website") or {}).get("years", {}).values():
        s = read_website_csv(source.read(entry["file"]))
        if len(s):
            s.index = s.index + pd.Timedelta(minutes=entry.get("minutes_to_add_to_align_with_api") or 0)
            parts.append(s)
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts).sort_index()
    return s[~s.index.duplicated()]


def to_grid(s: pd.Series, step_minutes: int, length: int) -> np.ndarray:
    out = np.full(length, np.nan, dtype="<f4")
    offset = (s.index - EPOCH) / pd.Timedelta(minutes=step_minutes)
    ok = (offset >= 0) & (offset < length) & (offset == np.round(offset))
    out[offset[ok].astype(int)] = np.round(s.values[ok], 1)
    return out


def summary(grid: np.ndarray, step_minutes: int) -> dict:
    have = np.flatnonzero(~np.isnan(grid))
    stamp = lambda i: (EPOCH + pd.Timedelta(minutes=int(i) * step_minutes)).strftime("%Y-%m-%dT%H:%MZ")  # noqa: E731
    return {"first": stamp(have[0]), "last": stamp(have[-1]), "count": int(have.size), "max": float(np.nanmax(grid))}


def best_on_10min(api_h: np.ndarray | None, web_10: np.ndarray | None, n10: int) -> np.ndarray:
    """10-minute website value where there is one, otherwise the value of the hour the slot falls in."""
    out = np.full(n10, np.nan, dtype="<f4")
    hour_of_slot = np.minimum((np.arange(n10) + 5) // 6, n10 // 6 - 1)  # slot i lies in the hour ending at index ceil(i / 6)
    if web_10 is not None:
        hourly_rows = np.full(n10 // 6, np.nan, dtype="<f4")
        hourly_rows[:] = web_10[: (n10 // 6) * 6 : 6]  # website rows that sit on the hour
        out = hourly_rows[hour_of_slot]
    if api_h is not None:
        api = api_h[hour_of_slot]
        out = np.where(np.isnan(api), out, api)
    if web_10 is not None:
        # Only a real 10-minute record overrides the hourly value: require a neighbour 10 minutes away.
        have = ~np.isnan(web_10)
        fine = have & (np.roll(have, 1) | np.roll(have, -1))
        out = np.where(fine, web_10, out)
    return out.astype("<f4")


def build(source: Source, sink: Sink) -> dict:
    links = load_links()
    now = datetime.now(timezone.utc)
    api = read_api(source, range(EPOCH.year, now.year + 1))
    # The grids stop at the end of the last month that has data.
    end = (api["time_end"].max() - pd.Timedelta(minutes=1)).to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
    n_h = int((end - EPOCH) / pd.Timedelta(hours=1))
    n_10 = n_h * 6
    api_by_station = {k: g.set_index("time_end")["so2_ugm3"] for k, g in api.groupby("station_id")}

    stations, best = [], []
    for st in links["stations"]:
        entry = {k: st[k] for k in ("id", "name", "latitude", "longitude", "mobile", "note")}
        entry["api_ids"] = [a["station_id"] for a in st["api"]]
        parts = [api_by_station[i] for i in entry["api_ids"] if i in api_by_station]
        api_h = web_10 = None
        if parts:
            s = pd.concat(parts).sort_index()
            api_h = to_grid(s[~s.index.duplicated()], 60, n_h)
        web = website_series(source, st)
        if len(web):
            web_10 = to_grid(web, 10, n_10)
        for kind, grid, step in (("api", api_h, 60), ("web", web_10, 10)):
            if grid is None or np.isnan(grid).all():
                entry[kind] = None
                continue
            name = f"series/{st['id']}.{kind}.f32"
            sink.write(name, grid.tobytes(), BIN, compress=True)
            entry[kind] = {"file": name, **summary(grid, step)}
        if entry["api"] is None and entry["web"] is None:
            continue
        stations.append(entry)
        best.append(best_on_10min(api_h, web_10, n_10))
        print(f"{st['id']}: api {entry['api'] and entry['api']['count']}, web {entry['web'] and entry['web']['count']}", flush=True)

    # Monthly snapshots: rows are 10-minute slots, columns are stations in the order of the station list.
    table = np.stack(best, axis=1)
    months = []
    for start in pd.date_range(EPOCH, end, freq="MS", inclusive="left"):
        i0 = int((start - EPOCH) / pd.Timedelta(minutes=10))
        i1 = int((start + pd.offsets.MonthBegin(1) - EPOCH) / pd.Timedelta(minutes=10))
        block = table[i0:i1]
        if np.isnan(block).all():
            continue
        months.append(f"{start:%Y-%m}")
        sink.write(f"snapshots/{start:%Y-%m}.f32", np.ascontiguousarray(block).tobytes(), BIN, compress=True)

    index = {
        "version": 1,
        "generated": now.strftime("%Y-%m-%dT%H:%MZ"),
        "units": "µg/m3",
        "epoch": EPOCH.strftime("%Y-%m-%dT%H:%MZ"),
        "end": end.strftime("%Y-%m-%dT%H:%MZ"),
        "time_convention": "element i of a series is the average over the interval ending at epoch + i * step_minutes (UTC)",
        "api": {"step_minutes": 60, "length": n_h, "description": "hourly values from the API archive (baseline-corrected)"},
        "web": {"step_minutes": 10, "length": n_10, "description": "website downloads, 10-minute where available, not baseline-corrected"},
        "snapshots": {
            "step_minutes": 10,
            "months": months,
            "file": "snapshots/YYYY-MM.f32",
            "layout": "Float32, rows are 10-minute slots from the start of the month, columns follow the station list",
            "value": "10-minute website value where there is one, otherwise the hourly API value, otherwise the hourly website value",
        },
        "stations": stations,
    }
    sink.write("stations.json", json.dumps(index, ensure_ascii=False).encode(), "application/json", compress=True)
    return index


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--private-dir", type=Path, help="read from this folder instead of the private bucket (testing)")
    ap.add_argument("--out-dir", type=Path, help="write to this folder instead of the public bucket (testing)")
    ap.add_argument("--prefix", default="web/v1")
    args = ap.parse_args(argv)
    index = build(Source(args.private_dir), Sink(args.prefix, args.out_dir))
    print(f"{len(index['stations'])} stations, {len(index['snapshots']['months'])} months", flush=True)


if __name__ == "__main__":
    main()
