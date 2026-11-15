"""Fetch hourly ground-level SO2 from Umhverfis- og orkustofnun (api.ust.is/aq).

Whole years come from the yearly CSV archives; days after the newest archive
come from the per-date API. Output is one Parquet file per year plus a station
table, for every station that reports SO2.

Times are the end of each averaging hour, in UTC (Iceland keeps UTC all year).
"""

from __future__ import annotations

import argparse
import io
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests


BASE = "https://api.ust.is"
COLUMNS = ["station_id", "time_end", "so2_ugm3", "resolution", "verification", "validity"]


def _get(url: str, **kwargs) -> requests.Response:
    r = requests.get(url, timeout=300, **kwargs)
    r.raise_for_status()
    return r


def fetch_stations() -> pd.DataFrame:
    rows = []
    for s in _get(f"{BASE}/aq/a/getStations").json():
        params = {p.strip('"') for p in s["parameters"].strip("{}").split(",")}
        lat, lon = float(s["latitude"]), float(s["longitude"])
        if "SO2" not in params:
            continue
        rows.append(
            {
                "station_id": s["local_id"],
                "name": s["name"],
                "municipality": s["municipality"],
                "lat": lat,
                "lon": lon,
                "altitude_m": s["altitude"],
                "mobile": s["mobile"],
                "active_from": s["activity_begin"],
                "active_to": s["activity_end"],
                "station_class": s["station_classification"],
                "area_class": s["area_classification"],
                "network": s["network_name"],
            }
        )
    return pd.DataFrame(rows).sort_values("station_id").reset_index(drop=True)


def archive_urls() -> dict[int, str]:
    """Yearly CSV archive links, scraped from the API index page."""
    html = _get(f"{BASE}/aq").text
    return {int(y): BASE + path for path, y in re.findall(r'href="(/static/aq/\w+_aq_timeseries_(\d{4})\.csv)"', html)}


def parse_archive(csv_bytes: bytes) -> pd.DataFrame:
    """SO2 rows of a yearly archive. The files differ between years in date format and unit column name."""
    df = pd.read_csv(io.BytesIO(csv_bytes))
    df = df[df["pollutantnotation"] == "SO2"]
    units = set(df["concentration" if "concentration" in df else "unit"].unique())
    if units - {"µg/m3"}:
        raise ValueError(f"unexpected SO2 units in archive: {units}")
    time_end = pd.to_datetime(df["endtime"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
    time_end = time_end.fillna(pd.to_datetime(df["endtime"], format="%Y-%m-%d %H:%M:%S", errors="coerce"))
    if time_end.isna().any():
        raise ValueError("unrecognised date format in archive")
    out = pd.DataFrame(
        {
            "station_id": df["station_local_id"],
            "time_end": time_end,
            "so2_ugm3": pd.to_numeric(df["the_value"], errors="coerce"),
            "resolution": df["resolution"],
            "verification": df["verification"].astype("Int8"),
            "validity": df["validity"].astype("Int8"),
        }
    )
    return out


def fetch_day(day: date) -> pd.DataFrame:
    """One day from the API. The API reports no validity flag."""
    rows = []
    for station_id, s in _get(f"{BASE}/aq/a/getDate/date/{day:%Y-%m-%d}").json().items():
        # A day without data comes back as an error message instead of stations.
        p = s.get("parameters", {}).get("SO2") if isinstance(s, dict) else None
        if not p:
            continue
        if p["unit"] != "µg/m3":
            raise ValueError(f"unexpected SO2 unit {p['unit']!r} at {station_id}")
        for key, rec in p.items():
            if key.isdigit():
                rows.append((station_id, rec["endtime"], rec["value"], p["resolution"], rec["verification"], None))
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["time_end"] = pd.to_datetime(df["time_end"])
    df["so2_ugm3"] = pd.to_numeric(df["so2_ugm3"], errors="coerce")
    df["verification"] = df["verification"].astype("Int8")
    df["validity"] = df["validity"].astype("Int8")
    return df


def tidy(df: pd.DataFrame, station_ids) -> pd.DataFrame:
    df = df[df["station_id"].isin(set(station_ids))]
    df = df.drop_duplicates(["station_id", "time_end"], keep="first")
    return df.sort_values(["station_id", "time_end"]).reset_index(drop=True)[COLUMNS]


def fetch_year(year: int, archives: dict[int, str], station_ids) -> pd.DataFrame:
    if year in archives:
        print(f"{year}: archive {archives[year]}", flush=True)
        return tidy(parse_archive(_get(archives[year]).content), station_ids)
    today = datetime.now(timezone.utc).date()
    day, last, parts = date(year, 1, 1), min(date(year, 12, 31), today), []
    print(f"{year}: per-date API, {day} to {last}", flush=True)
    while day <= last:
        parts.append(fetch_day(day))
        day += timedelta(days=1)
        time.sleep(0.5)  # be gentle with the API
    return tidy(pd.concat(parts), station_ids)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--out", type=Path, default=Path("data/stations"))
    ap.add_argument("--upload", action="store_true", help="upload results to the private bucket")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    stations = fetch_stations()
    stations.to_parquet(args.out / "stations.parquet", index=False)
    print(f"{len(stations)} SO2 stations", flush=True)

    def keep(path: Path) -> None:
        if args.upload:
            from .. import storage

            storage.bucket("private").upload(path, f"stations/{path.name}")

    keep(args.out / "stations.parquet")
    archives = archive_urls()
    for year in args.years:
        df = fetch_year(year, archives, stations["station_id"])
        path = args.out / f"so2_{year}.parquet"
        df.to_parquet(path, index=False)
        print(f"{year}: {len(df)} rows, {df['station_id'].nunique()} stations", flush=True)
        keep(path)


if __name__ == "__main__":
    main()
