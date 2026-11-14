"""Fetch TROPOMI L2 SO2 orbits from the Copernicus Data Space and keep the
scanlines that cross the project domain.

Orbit files are about 1 GB each, so each is downloaded, cut down and deleted
before the next. The subset keeps every variable of the original file (all
groups), sliced along the scanline dimension only, so averaging kernels,
air-mass factors and pixel corners stay available for the observation operator.

Credentials: CDSE_S3_ACCESS_KEY / CDSE_S3_SECRET_KEY.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import netCDF4
import numpy as np
import requests

from .. import config

CATALOGUE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
# S5P_<stream>_L2__SO2____<start>_<end>_<orbit>_<collection>_<processor>_<processed>.nc
NAME = re.compile(r"S5P_(?P<stream>\w{4})_L2__SO2____(?P<start>\d{8}T\d{6})_\d{8}T\d{6}_(?P<orbit>\d{5})_\d{2}_\d{6}_(?P<processed>\d{8}T\d{6})")
STREAM_RANK = {"NRTI": 0, "OFFL": 1, "RPRO": 2}


def search(day: date, dom: config.Domain) -> list[dict]:
    """Products sensing the domain on `day`, one per orbit (best stream, latest processing)."""
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    flt = (
        "Collection/Name eq 'SENTINEL-5P' and contains(Name,'L2__SO2___')"
        f" and ContentDate/Start ge {start:%Y-%m-%dT%H:%M:%S}.000Z and ContentDate/Start lt {end:%Y-%m-%dT%H:%M:%S}.000Z"
        f" and OData.CSC.Intersects(area=geography'SRID=4326;{dom.wkt()}')"
    )
    url, params, found = CATALOGUE, {"$filter": flt, "$top": 200, "$orderby": "ContentDate/Start"}, []
    while url:
        r = requests.get(url, params=params, timeout=120)
        r.raise_for_status()
        body = r.json()
        found += body["value"]
        url, params = body.get("@odata.nextLink"), None

    best: dict[str, tuple] = {}
    for p in found:
        m = NAME.match(p["Name"])
        if not m:
            continue
        rank = (STREAM_RANK.get(m["stream"], -1), m["processed"])
        if m["orbit"] not in best or rank > best[m["orbit"]][0]:
            best[m["orbit"]] = (rank, p)
    return [p for _, p in sorted(best.values(), key=lambda v: v[1]["ContentDate"]["Start"])]


def s3_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name="default",
        aws_access_key_id=os.environ["CDSE_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["CDSE_S3_SECRET_KEY"],
    )


def download(product: dict, dest: Path, client=None) -> Path:
    """Download the product's NetCDF file. S3Path may be the file or a folder holding it."""
    client = client or s3_client()
    prefix = product["S3Path"].removeprefix("/eodata/")
    listing = client.list_objects_v2(Bucket="eodata", Prefix=prefix)
    keys = [o["Key"] for o in listing.get("Contents", []) if o["Key"].endswith(".nc")]
    if len(keys) != 1:
        raise RuntimeError(f"expected one .nc under {prefix}, found {keys}")
    client.download_file("eodata", keys[0], str(dest))
    return dest


def scanline_window(lat: np.ndarray, lon: np.ndarray, dom: config.Domain) -> slice | None:
    """Smallest scanline range holding every pixel centre inside the domain."""
    inside = np.asarray(dom.contains(np.ma.filled(lat, np.nan), np.ma.filled(lon, np.nan)))
    rows = np.flatnonzero(inside.any(axis=1))
    return slice(int(rows[0]), int(rows[-1]) + 1) if rows.size else None


def _copy_group(src, dst, window: slice) -> None:
    dst.setncatts({a: src.getncattr(a) for a in src.ncattrs()})
    for name, dim in src.dimensions.items():
        size = window.stop - window.start if name == "scanline" else len(dim)
        dst.createDimension(name, size)
    for name, var in src.variables.items():
        fill = var.getncattr("_FillValue") if "_FillValue" in var.ncattrs() else None
        numeric = var.dtype != str and not var.dtype.hasobject
        out = dst.createVariable(name, var.datatype, var.dimensions, fill_value=fill, **({"zlib": True, "complevel": 4} if numeric and var.dimensions else {}))
        out.setncatts({a: var.getncattr(a) for a in var.ncattrs() if a != "_FillValue"})
        out.set_auto_maskandscale(False)
        var.set_auto_maskandscale(False)
        if not var.dimensions:
            out.assignValue(var.getValue())
            continue
        # Dimensions are looked up through parent groups, so "scanline" is found by name.
        index = tuple(window if d == "scanline" else slice(None) for d in var.dimensions)
        out[:] = var[index]
    for name, group in src.groups.items():
        _copy_group(group, dst.createGroup(name), window)


def subset(src_path: Path, dst_path: Path, dom: config.Domain) -> int:
    """Write the in-domain scanlines of an orbit file. Returns the number kept (0: nothing written)."""
    with netCDF4.Dataset(src_path) as src:
        product = src["PRODUCT"]
        window = scanline_window(product["latitude"][0], product["longitude"][0], dom)
        if window is None:
            return 0
        with netCDF4.Dataset(dst_path, "w") as dst:
            _copy_group(src, dst, window)
            dst.setncattr("subset_source", src_path.name)
            dst.setncattr("subset_scanline_start", window.start)
            dst.setncattr("subset_scanline_stop", window.stop)
    return window.stop - window.start


def key_for(product: dict) -> str:
    m = NAME.match(product["Name"])
    d = m["start"]
    stem = product["Name"].removesuffix(".nc")
    return f"tropomi/so2/{d[:4]}/{d[4:6]}/{d[6:8]}/{stem}.subset.nc"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--eruption", required=True)
    ap.add_argument("--days", nargs="*", help="restrict to these YYYY-MM-DD days")
    ap.add_argument("--list", action="store_true", help="only list the products that would be fetched")
    args = ap.parse_args(argv)

    cfg = config.load()
    dom = config.domain(cfg)
    days = config.days(args.eruption, "tropomi", cfg)
    if args.days:
        days = [d for d in days if d.isoformat() in set(args.days)]

    bucket = client = None
    if not args.list:
        from .. import storage

        bucket, client = storage.bucket("private"), s3_client()

    for day in days:
        for product in search(day, dom):
            key = key_for(product)
            if args.list:
                print(f"{product['Name']}  {product['ContentLength'] / 1e6:.0f} MB")
                continue
            orbit = NAME.match(product["Name"])["orbit"]
            done = [k for k in bucket.list(key.rsplit("/", 1)[0] + "/") if f"_{orbit}_" in k]
            if key in done or key + ".empty" in done:
                print(f"skip {product['Name']}", flush=True)
                continue
            with tempfile.TemporaryDirectory() as tmp:
                raw = download(product, Path(tmp) / product["Name"], client)
                cut = Path(tmp) / "subset.nc"
                n = subset(raw, cut, dom)
                print(f"{product['Name']}: {n} scanlines in domain", flush=True)
                if n:
                    bucket.upload(cut, key)
                else:
                    # Marker so orbits that only clip the search box are not downloaded again.
                    bucket.client.put_object(Bucket=bucket.name, Key=key + ".empty", Body=b"")


if __name__ == "__main__":
    main()
