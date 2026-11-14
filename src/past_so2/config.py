"""Project configuration: domain box and eruption windows."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

CONFIG_PATH = Path(os.environ.get("PAST_CONFIG", Path(__file__).resolve().parents[2] / "config" / "project.yaml"))


@dataclass(frozen=True)
class Domain:
    west: float
    east: float
    south: float
    north: float

    def contains(self, lat, lon):
        return (lat >= self.south) & (lat <= self.north) & (lon >= self.west) & (lon <= self.east)

    def wkt(self) -> str:
        w, e, s, n = self.west, self.east, self.south, self.north
        return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


@dataclass(frozen=True)
class Eruption:
    name: str
    start: datetime
    end: datetime
    vent_lat: float
    vent_lon: float


def _as_datetime(value) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def load(path: Path | None = None) -> dict:
    with open(path or CONFIG_PATH) as f:
        return yaml.safe_load(f)


def domain(cfg: dict | None = None) -> Domain:
    return Domain(**(cfg or load())["domain"])


def eruption(name: str, cfg: dict | None = None) -> Eruption:
    eruptions = (cfg or load())["eruptions"]
    if name not in eruptions:
        raise KeyError(f"unknown eruption {name!r}; known: {', '.join(eruptions)}")
    e = eruptions[name]
    return Eruption(name, _as_datetime(e["start"]), _as_datetime(e["end"]), e["vent"]["lat"], e["vent"]["lon"])


def days(name: str, section: str, cfg: dict | None = None) -> list[date]:
    """Calendar days (UTC) needed for an eruption, including the padding of `section`."""
    cfg = cfg or load()
    e = eruption(name, cfg)
    first = (e.start - timedelta(hours=cfg[section]["pad_before_hours"])).date()
    last = (e.end + timedelta(hours=cfg[section]["pad_after_hours"])).date()
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]
