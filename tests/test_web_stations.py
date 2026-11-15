import numpy as np
import pandas as pd

from past_so2.web import stations as ws


def test_to_grid_places_values_by_interval_end():
    s = pd.Series([1.04, 2.0, 3.0], index=ws.EPOCH + pd.to_timedelta([0, 10, 35], unit="min"))
    grid = ws.to_grid(s, 10, 6)
    assert grid[0] == np.float32(1.0) and grid[1] == 2.0
    assert np.isnan(grid[2:]).all()  # 00:35 is off the grid and dropped


def test_best_value_prefers_ten_minute_then_api_then_website_hourly():
    n10 = 18
    api = np.array([np.nan, 5.0, np.nan], dtype="<f4")  # hours ending 00:00, 01:00, 02:00
    web = np.full(n10, np.nan, dtype="<f4")
    web[[2, 3]] = [40.0, 60.0]  # a real 10-minute run at 00:20 and 00:30
    web[12] = 9.0  # a lone hourly row stamped 02:00
    best = ws.best_on_10min(api, web, n10)
    assert best[2] == 40.0 and best[3] == 60.0  # 10-minute values win
    assert best[1] == 5.0 and best[6] == 5.0  # rest of the hour ending 01:00 takes the API value
    assert best[7] == 9.0 and best[12] == 9.0  # hour ending 02:00 has no API value: website hourly row
