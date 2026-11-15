"""Fetch CARRA (west domain) from the Climate Data Store and cache daily
subsets over the project domain.

CARRA is only served for its whole domain, so each request is downloaded as
GRIB, cropped to the grid window covering the project box and discarded. One
NetCDF file is written per UTC day, on the native Lambert grid and the native
hybrid model levels:

  hourly state  analysis at 00, 03, ... 21 and its +1 h and +2 h forecasts
  hourly fluxes mean over each hour, from differences of the accumulated
                +1, +2 and +3 h forecast fields

Winds are relative to the grid axes (x, y), which is what advection on the
native grid needs. `hybrid_a`/`hybrid_b` give half-level pressures
p = a + b * surface_pressure, from the model top down.

The CDS runs one archive request per user at a time, so days are fetched in
sequence. Days already in the bucket are skipped.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import eccodes
import netCDF4
import numpy as np
import requests

from .. import config

API = (os.environ.get("CDSAPI_URL") or "https://cds.climate.copernicus.eu/api").rstrip("/")
N_LEVELS = 65
CYCLES = range(0, 24, 3)
MARGIN = 4  # grid points kept outside the lat/lon box
MISSING = 9.96921e36  # NetCDF default float fill, also used for GRIB bitmap gaps
EARTH_RADIUS = {0: 6367470.0, 6: 6371229.0}  # GRIB2 shapeOfTheEarth codes for spheres, metres

# name in file: (CDS variable, GRIB shortName, least significant digit kept)
LEVEL_VARS = {
    "u": ("u_component_of_wind", "u", 2),
    "v": ("v_component_of_wind", "v", 2),
    "t": ("temperature", "t", 2),
    "q": ("specific_humidity", "q", 6),
    "tke": ("turbulent_kinetic_energy", "tke", 3),
}
SURFACE_VARS = {
    "sp": ("surface_pressure", "sp", 0),
    "t2m": ("2m_temperature", "2t", 2),
    "skt": ("skin_temperature", "skt", 2),
    "u10": ("10m_u_component_of_wind", "10u", 2),
    "v10": ("10m_v_component_of_wind", "10v", 2),
    "cloud_base": ("cloud_base", "cdcb", 0),
    "cloud_top": ("cloud_top", "cdct", 0),
    "tcc": ("total_cloud_cover", "tcc", 1),
    "lcc": ("low_cloud_cover", "lcc", 1),
}
STATIC_VARS = {
    "orog": ("orography", "orog", 1),
    "lsm": ("land_sea_mask", "lsm", 2),
    "z0": ("surface_roughness", "sr", 5),
    "z0h": ("surface_roughness_length_for_heat", "srlh", 6),
}
# Accumulated since the start of the forecast; stored as hourly means or sums.
FLUX_VARS = {
    "sshf": ("surface_sensible_heat_flux", "sshf", 1),
    "slhf": ("surface_latent_heat_flux", "slhf", 1),
    "tau_x": ("time_integral_of_surface_eastward_momentum_flux", "tisemf", 4),
    "tau_y": ("time_integral_of_surface_northward_momentum_flux", "tisnmf", 4),
    "tp": ("total_precipitation", "tp", 3),
}


# --- Climate Data Store -----------------------------------------------------


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["PRIVATE-TOKEN"] = os.environ["CDSAPI_KEY"]
    return s


def retrieve(dataset: str, request: dict, dest: Path, poll: float = 20.0) -> Path:
    """Submit a request, wait for it and download the result."""
    s = _session()
    r = s.post(f"{API}/retrieve/v1/processes/{dataset}/execution", json={"inputs": request}, timeout=120)
    if not r.ok:
        raise RuntimeError(f"CDS rejected the request ({r.status_code}): {r.text[:500]}")
    job = f"{API}/retrieve/v1/jobs/{r.json()['jobID']}"
    t0, failures = time.time(), 0
    while True:
        time.sleep(poll)
        try:
            status = s.get(job, timeout=120).json()
            failures = 0
        except (requests.RequestException, ValueError):
            failures += 1
            if failures > 30:
                raise
            continue
        if status["status"] == "successful":
            break
        if status["status"] in ("failed", "rejected", "dismissed"):
            raise RuntimeError(f"CDS job {status['status']}: {s.get(job + '/results', timeout=120).text[:500]}")
    href = s.get(job + "/results", timeout=120).json()["asset"]["value"]["href"]
    print(f"  {dataset} ready after {time.time() - t0:.0f} s", flush=True)
    for attempt in range(5):
        try:
            with requests.get(href, stream=True, timeout=300) as dl, open(dest, "wb") as f:
                dl.raise_for_status()
                for block in dl.iter_content(1 << 22):
                    f.write(block)
            break
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(30)
    print(f"  downloaded {dest.stat().st_size / 1e9:.2f} GB after {time.time() - t0:.0f} s", flush=True)
    return dest


def _base(day: date) -> dict:
    return {"domain": "west_domain", "year": f"{day:%Y}", "month": f"{day:%m}", "day": f"{day:%d}", "data_format": "grib"}


def _times(cycles) -> list[str]:
    return [f"{h:02d}:00" for h in cycles]


# --- GRIB reading -----------------------------------------------------------


class Grid:
    """Window of the CARRA grid covering the domain, fixed by the first message read."""

    def __init__(self, gid, dom: config.Domain):
        self.ny, self.nx = eccodes.codes_get(gid, "Ny"), eccodes.codes_get(gid, "Nx")
        if eccodes.codes_get(gid, "iScansNegatively") or not eccodes.codes_get(gid, "jScansPositively"):
            raise ValueError("unexpected GRIB scanning mode")
        lat = eccodes.codes_get_array(gid, "latitudes").reshape(self.ny, self.nx)
        lon = (eccodes.codes_get_array(gid, "longitudes").reshape(self.ny, self.nx) + 180.0) % 360.0 - 180.0
        inside = dom.contains(lat, lon)
        rows, cols = np.flatnonzero(inside.any(axis=1)), np.flatnonzero(inside.any(axis=0))
        self.j = slice(max(rows[0] - MARGIN, 0), min(rows[-1] + 1 + MARGIN, self.ny))
        self.i = slice(max(cols[0] - MARGIN, 0), min(cols[-1] + 1 + MARGIN, self.nx))
        self.lat, self.lon = lat[self.j, self.i], lon[self.j, self.i]
        self.projection = {
            "grid_mapping_name": "lambert_conformal_conic",
            "standard_parallel": [eccodes.codes_get(gid, "Latin1InDegrees"), eccodes.codes_get(gid, "Latin2InDegrees")],
            "latitude_of_projection_origin": eccodes.codes_get(gid, "LaDInDegrees"),
            "longitude_of_central_meridian": (eccodes.codes_get(gid, "LoVInDegrees") + 180.0) % 360.0 - 180.0,
            "earth_radius": EARTH_RADIUS[eccodes.codes_get(gid, "shapeOfTheEarth")],
            "dx_m": eccodes.codes_get(gid, "DxInMetres"),
            "dy_m": eccodes.codes_get(gid, "DyInMetres"),
            "full_grid_nx": self.nx,
            "full_grid_ny": self.ny,
            "window_i0": self.i.start,
            "window_j0": self.j.start,
            "winds_relative_to_grid": eccodes.codes_get(gid, "uvRelativeToGrid"),
        }

    def crop(self, gid) -> np.ndarray:
        if eccodes.codes_get(gid, "Ny") != self.ny or eccodes.codes_get(gid, "Nx") != self.nx:
            raise ValueError("GRIB message on a different grid")
        # Some fields (cloud base and top where there is no cloud) carry a bitmap.
        eccodes.codes_set(gid, "missingValue", MISSING)
        values = eccodes.codes_get_values(gid).reshape(self.ny, self.nx)[self.j, self.i].astype(np.float32)
        values[values == np.float32(MISSING)] = np.nan
        return values


def messages(path: Path):
    """Yield (handle, shortName, level, valid hour offset from the run day, lead hours)."""
    with open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                return
            try:
                lead = int(eccodes.codes_get(gid, "endStep"))
                hour = eccodes.codes_get(gid, "dataTime") // 100 + lead
                yield gid, eccodes.codes_get(gid, "shortName"), eccodes.codes_get(gid, "level"), hour, lead
            finally:
                eccodes.codes_release(gid)


# --- Daily file -------------------------------------------------------------


class DayFile:
    def __init__(self, path: Path, day: date, dom: config.Domain, top_level: int):
        self.path, self.day, self.dom, self.top = path, day, dom, top_level
        self.levels = np.arange(top_level, N_LEVELS + 1)
        self.grid: Grid | None = None
        self.ds: netCDF4.Dataset | None = None
        self.filled: dict[str, np.ndarray] = {}

    def _create(self, gid) -> None:
        self.grid = g = Grid(gid, self.dom)
        ny, nx = g.lat.shape
        ds = self.ds = netCDF4.Dataset(self.path, "w")
        ds.title = "CARRA west domain subset"
        ds.source = "Copernicus Arctic Regional Reanalysis (C3S), reanalysis-carra-model-levels and -single-levels"
        ds.createDimension("time", 24)
        ds.createDimension("level", self.levels.size)
        ds.createDimension("half_level", self.levels.size + 1)
        ds.createDimension("y", ny)
        ds.createDimension("x", nx)

        t = ds.createVariable("time", "f8", ("time",))
        t.units = f"hours since {self.day:%Y-%m-%d} 00:00:00"
        t.calendar = "standard"
        t[:] = np.arange(24)
        lev = ds.createVariable("level", "i4", ("level",))
        lev.long_name = "model level number (1 at model top, 65 nearest the surface)"
        lev[:] = self.levels
        for name, values in (("latitude", g.lat), ("longitude", g.lon)):
            v = ds.createVariable(name, "f8", ("y", "x"), zlib=True)
            v[:] = values
        crs = ds.createVariable("projection", "i4", ())
        crs.setncatts(g.projection)

        def make(name, dims, digits):
            chunks = {"time": 1, "level": 1, "y": ny, "x": nx}
            v = ds.createVariable(
                name, "f4", dims, zlib=True, complevel=4, shuffle=True, least_significant_digit=digits,
                chunksizes=[chunks[d] for d in dims], fill_value=np.float32(MISSING),
            )
            v.grid_mapping = "projection"
            return v

        for name, (long_name, _, digits) in LEVEL_VARS.items():
            make(name, ("time", "level", "y", "x"), digits).long_name = long_name
            self.filled[name] = np.zeros((24, self.levels.size), bool)
        for name, (long_name, _, digits) in SURFACE_VARS.items():
            make(name, ("time", "y", "x"), digits).long_name = long_name
            self.filled[name] = np.zeros(24, bool)
        for name, (long_name, _, digits) in FLUX_VARS.items():
            v = make(name, ("time", "y", "x"), digits)
            v.long_name = long_name
            v.cell_methods = "time: sum over the hour starting at time" if name == "tp" else "time: mean over the hour starting at time"
            self.filled[name] = np.zeros(24, bool)
        for name, (long_name, _, digits) in STATIC_VARS.items():
            make(name, ("y", "x"), digits).long_name = long_name
            self.filled[name] = np.zeros(1, bool)

        # Half-level coefficients: 66 of each, from the top of the model down.
        pv = eccodes.codes_get_array(gid, "pv")
        if pv.size != 2 * (N_LEVELS + 1):
            raise ValueError(f"expected {2 * (N_LEVELS + 1)} hybrid coefficients, found {pv.size}")
        for name, coef in (("hybrid_a", pv[: N_LEVELS + 1]), ("hybrid_b", pv[N_LEVELS + 1 :])):
            v = ds.createVariable(name, "f8", ("half_level",))
            v.long_name = f"{name} at the half levels bounding levels {self.top}..{N_LEVELS}"
            v[:] = coef[self.top - 1 :]

    def add_state(self, path: Path) -> None:
        """Instantaneous model-level, surface and static fields."""
        by_short = {short: name for group in (LEVEL_VARS, SURFACE_VARS, STATIC_VARS) for name, (_, short, _) in group.items()}
        for gid, short, level, hour, _ in messages(path):
            if self.ds is None:
                if eccodes.codes_get(gid, "typeOfLevel") != "hybrid":
                    raise RuntimeError("the first file added must hold model-level fields (for the hybrid coefficients)")
                self._create(gid)
            name = by_short.get(short)
            if name is None:
                raise ValueError(f"unexpected GRIB field {short!r} in {path.name}")
            values = self.grid.crop(gid)
            if "units" not in self.ds[name].ncattrs():
                self.ds[name].units = eccodes.codes_get(gid, "units")
            if name in LEVEL_VARS:
                self.ds[name][hour, level - self.top] = np.ma.masked_invalid(values)
                self.filled[name][hour, level - self.top] = True
            elif name in SURFACE_VARS:
                self.ds[name][hour] = np.ma.masked_invalid(values)
                self.filled[name][hour] = True
            elif not self.filled[name][0]:
                self.ds[name][:] = np.ma.masked_invalid(values)
                self.filled[name][0] = True

    def add_fluxes(self, path: Path) -> None:
        """De-accumulate +1, +2, +3 h forecast fields into hourly values."""
        by_short = {short: name for name, (_, short, _) in FLUX_VARS.items()}
        acc: dict[tuple, np.ndarray] = {}
        for gid, short, _, hour, lead in messages(path):
            if short in by_short:
                acc[by_short[short], hour - lead, lead] = self.grid.crop(gid)
                if "units" not in self.ds[by_short[short]].ncattrs():
                    units = eccodes.codes_get(gid, "units")
                    self.ds[by_short[short]].units = units if short == "tp" else f"({units}) s-1"
        for (name, cycle, lead), values in acc.items():
            previous = acc.get((name, cycle, lead - 1)) if lead > 1 else 0.0
            if previous is None:
                raise ValueError(f"{name}: missing +{lead - 1} h field for the {cycle:02d} UTC run")
            hour = cycle + lead - 1  # start of the accumulation hour
            scale = 1.0 if name == "tp" else 1.0 / 3600.0
            self.ds[name][hour] = np.ma.masked_invalid((values - previous) * scale)
            self.filled[name][hour] = True

    def close(self) -> None:
        missing = [name for name, done in self.filled.items() if not done.all()]
        self.ds.close()
        if missing:
            raise RuntimeError(f"incomplete day {self.day}: missing fields for {missing}")


def fetch_day(day: date, dest: Path, dom: config.Domain, top_level: int, workdir: Path) -> Path:
    levels = [str(k) for k in range(top_level, N_LEVELS + 1)]
    level_req = {**_base(day), "variable": [v[0] for v in LEVEL_VARS.values()], "model_level": levels}
    surface_req = {**_base(day), "level_type": "surface_or_atmosphere"}
    instant = [v[0] for v in SURFACE_VARS.values()]
    grib = workdir / "chunk.grib"
    out = DayFile(dest, day, dom, top_level)

    # Model levels first: about 3 MB per field, so the day is split to bound disk use.
    plan = [("reanalysis-carra-model-levels", {**level_req, "product_type": "analysis", "time": _times(CYCLES)}, out.add_state)]
    for half in (range(0, 12, 3), range(12, 24, 3)):
        plan.append(("reanalysis-carra-model-levels", {**level_req, "product_type": "forecast", "leadtime_hour": ["1", "2"], "time": _times(half)}, out.add_state))
    plan.append(("reanalysis-carra-single-levels", {**surface_req, "product_type": "analysis", "time": _times(CYCLES), "variable": instant + [v[0] for v in STATIC_VARS.values()]}, out.add_state))
    plan.append(("reanalysis-carra-single-levels", {**surface_req, "product_type": "forecast", "leadtime_hour": ["1", "2"], "time": _times(CYCLES), "variable": instant}, out.add_state))
    plan.append(("reanalysis-carra-single-levels", {**surface_req, "product_type": "forecast", "leadtime_hour": ["1", "2", "3"], "time": _times(CYCLES), "variable": [v[0] for v in FLUX_VARS.values()]}, out.add_fluxes))

    for dataset, request, add in plan:
        retrieve(dataset, request, grib)
        add(grib)
        grib.unlink()
    out.close()
    return dest


def key_for(day: date) -> str:
    return f"met/carra/{day:%Y/%m}/carra_west_{day:%Y%m%d}.nc"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--eruption", required=True)
    ap.add_argument("--days", nargs="*", help="restrict to these YYYY-MM-DD days")
    ap.add_argument("--budget-hours", type=float, default=5.0, help="do not start another day after this long")
    ap.add_argument("--local", type=Path, help="write files here instead of uploading (testing)")
    ap.add_argument("--status", action="store_true", help="only report how many days are in the bucket")
    args = ap.parse_args(argv)

    cfg = config.load()
    dom, top = config.domain(cfg), cfg["met"]["carra_top_level"]
    days = config.days(args.eruption, "met", cfg)
    if args.days:
        days = [d for d in days if d.isoformat() in set(args.days)]

    bucket = None
    if not args.local:
        from .. import storage

        bucket = storage.bucket("private")

    if args.status:
        done = sum(bucket.exists(key_for(d)) for d in days)
        print(f"{done} of {len(days)} days in the bucket", flush=True)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"done={done}\nremaining={len(days) - done}\n")
        return

    started, remaining, fetched = time.time(), [], 0
    for n, day in enumerate(days):
        if bucket and bucket.exists(key_for(day)):
            print(f"skip {day}", flush=True)
            continue
        if time.time() - started > args.budget_hours * 3600:
            remaining = [d for d in days[n:] if not bucket or not bucket.exists(key_for(d))]
            break
        print(f"{datetime.now():%H:%M:%S} fetching {day}", flush=True)
        with tempfile.TemporaryDirectory(dir=args.local) as tmp:
            path = fetch_day(day, Path(tmp) / Path(key_for(day)).name, dom, top, Path(tmp))
            if bucket:
                bucket.upload(path, key_for(day))
            else:
                path.rename(args.local / path.name)
        fetched += 1
    if remaining:
        print(f"time budget reached; {len(remaining)} days left, starting {remaining[0]}. Run again to continue.", flush=True)


if __name__ == "__main__":
    main()
