import netCDF4
import numpy as np

from past_so2.config import Domain
from past_so2.fetch import tropomi


def make_orbit(path, n_scan=40, n_pix=6, n_layer=3):
    with netCDF4.Dataset(path, "w") as ds:
        ds.title = "synthetic"
        p = ds.createGroup("PRODUCT")
        for name, size in [("time", 1), ("scanline", n_scan), ("ground_pixel", n_pix), ("layer", n_layer), ("corner", 4)]:
            p.createDimension(name, size)
        lat = p.createVariable("latitude", "f4", ("time", "scanline", "ground_pixel"), fill_value=9.96921e36)
        lon = p.createVariable("longitude", "f4", ("time", "scanline", "ground_pixel"), fill_value=9.96921e36)
        lat[0] = np.linspace(50, 89, n_scan)[:, None] + np.zeros(n_pix)
        lon[0] = np.linspace(-30, -10, n_pix)[None, :] + np.zeros((n_scan, 1))
        col = p.createVariable("sulfurdioxide_total_vertical_column", "f4", ("time", "scanline", "ground_pixel"))
        col[0] = np.arange(n_scan * n_pix).reshape(n_scan, n_pix)
        col.units = "mol m-2"
        utc = p.createVariable("time_utc", str, ("time", "scanline"))
        for i in range(n_scan):
            utc[0, i] = f"t{i}"
        d = p.createGroup("SUPPORT_DATA").createGroup("DETAILED_RESULTS")
        ak = d.createVariable("averaging_kernel", "f4", ("time", "scanline", "ground_pixel", "layer"))
        ak[:] = np.arange(n_scan)[None, :, None, None]
        g = p["SUPPORT_DATA"].createGroup("GEOLOCATIONS")
        b = g.createVariable("latitude_bounds", "f4", ("time", "scanline", "ground_pixel", "corner"))
        b[:] = 1.0
        scalar = ds.createGroup("METADATA").createVariable("flag", "i4", ())
        scalar.assignValue(7)


def test_subset_keeps_in_domain_scanlines(tmp_path):
    src, dst = tmp_path / "orbit.nc", tmp_path / "cut.nc"
    make_orbit(src)
    dom = Domain(west=-27, east=-12, south=62, north=67.5)
    n = tropomi.subset(src, dst, dom)
    with netCDF4.Dataset(src) as a, netCDF4.Dataset(dst) as b:
        lat = a["PRODUCT/latitude"][0, :, 0]
        rows = np.flatnonzero((lat >= 62) & (lat <= 67.5))
        assert n == rows.size > 0
        assert b["PRODUCT"].dimensions["scanline"].size == n
        np.testing.assert_array_equal(b["PRODUCT/latitude"][0, :, 0], lat[rows])
        np.testing.assert_array_equal(
            b["PRODUCT/sulfurdioxide_total_vertical_column"][:], a["PRODUCT/sulfurdioxide_total_vertical_column"][:, rows]
        )
        assert b["PRODUCT/sulfurdioxide_total_vertical_column"].units == "mol m-2"
        assert b["PRODUCT/SUPPORT_DATA/DETAILED_RESULTS/averaging_kernel"].shape == (1, n, 6, 3)
        assert b["PRODUCT/SUPPORT_DATA/DETAILED_RESULTS/averaging_kernel"][0, 0, 0, 0] == rows[0]
        assert b["PRODUCT/time_utc"][0, 0] == f"t{rows[0]}"
        assert b["METADATA/flag"].getValue() == 7
        assert b.title == "synthetic"


def test_subset_outside_domain_writes_nothing(tmp_path):
    src, dst = tmp_path / "orbit.nc", tmp_path / "cut.nc"
    make_orbit(src)
    assert tropomi.subset(src, dst, Domain(west=10, east=20, south=0, north=10)) == 0
    assert not dst.exists()


def test_search_result_naming():
    p = {"Name": "S5P_RPRO_L2__SO2____20240601T121147_20240601T135317_34378_03_020700_20250517T225302.nc"}
    assert tropomi.key_for(p) == (
        "tropomi/so2/2024/06/01/S5P_RPRO_L2__SO2____20240601T121147_20240601T135317_34378_03_020700_20250517T225302.subset.nc"
    )
