#!/usr/bin/env python3
"""Collect and summarize WeatherFlow Tempest observations.

The Tempest REST API exposes one-minute historical observations for request
windows of five days or less. This collector downloads four local calendar
 days at a time, writes deterministic daily gzip archives, and rebuilds hourly
and daily summary files consumed by the static GitHub Pages dashboard.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from zoneinfo import ZoneInfo

API_ROOT = "https://swd.weatherflow.com/swd/rest"
MPS_TO_MPH = 2.2369362920544
MM_TO_IN = 1.0 / 25.4
KM_TO_MI = 0.62137119223733
SECTORS_16 = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

RAW_FIELDS = [
    "epoch",
    "timestamp_utc",
    "timestamp_local",
    "temp_f",
    "dewpoint_f",
    "rh_pct",
    "pressure_mb",
    "wind_lull_mph",
    "wind_avg_mph",
    "wind_gust_mph",
    "wind_direction_deg",
    "wind_sample_interval_s",
    "precip_raw_in",
    "precip_final_in",
    "precip_used_in",
    "precip_type",
    "lightning_count",
    "lightning_avg_distance_mi",
    "battery_v",
    "report_interval_min",
    "precip_analysis_type",
]

DAILY_FIELDS = [
    "date",
    "year",
    "month",
    "day",
    "day_of_year",
    "obs_count",
    "observation_minutes",
    "completeness_pct",
    "temp_avg_f",
    "temp_high_f",
    "temp_high_time_local",
    "temp_low_f",
    "temp_low_time_local",
    "dewpoint_avg_f",
    "dewpoint_high_f",
    "dewpoint_low_f",
    "rh_avg_pct",
    "rh_high_pct",
    "rh_low_pct",
    "pressure_avg_mb",
    "pressure_high_mb",
    "pressure_low_mb",
    "wind_avg_mph",
    "wind_max_1min_mph",
    "wind_lull_min_mph",
    "wind_gust_mph",
    "wind_gust_time_local",
    "wind_vector_dir_deg",
    "wind_vector_dir_cardinal",
    "prevailing_wind_dir",
    "precip_in",
    "precip_raw_in",
    "wet_minutes",
    "lightning_count",
    "lightning_avg_distance_mi",
    "lightning_closest_distance_mi",
    "battery_min_v",
    "report_interval_mode_min",
]

HOURLY_FIELDS = [
    "hour_local",
    "date",
    "hour",
    "obs_count",
    "observation_minutes",
    "temp_avg_f",
    "temp_high_f",
    "temp_low_f",
    "dewpoint_avg_f",
    "rh_avg_pct",
    "pressure_avg_mb",
    "wind_avg_mph",
    "wind_gust_mph",
    "wind_vector_dir_deg",
    "wind_vector_dir_cardinal",
    "precip_in",
    "lightning_count",
]


def _num(values: Sequence[Any], index: int) -> float | None:
    if index >= len(values):
        return None
    value = values[index]
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def c_to_f(value_c: float | None) -> float | None:
    return None if value_c is None else value_c * 9.0 / 5.0 + 32.0


def dewpoint_c(temp_c: float | None, rh_pct: float | None) -> float | None:
    """Return dew point using the Magnus formulation."""
    if temp_c is None or rh_pct is None or rh_pct <= 0:
        return None
    rh = min(100.0, max(0.01, rh_pct)) / 100.0
    a, b = 17.625, 243.04
    gamma = math.log(rh) + (a * temp_c) / (b + temp_c)
    return b * gamma / (a - gamma)


def cardinal_16(direction: float | None) -> str:
    if direction is None or not math.isfinite(direction):
        return ""
    return SECTORS_16[int((direction + 11.25) // 22.5) % 16]


def fmt(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.{digits}f}"


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'; use YYYY-MM-DD") from exc


def daterange(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def group_consecutive_days(days: Iterable[date], max_days: int = 4) -> list[list[date]]:
    groups: list[list[date]] = []
    current: list[date] = []
    for day in sorted(set(days)):
        if not current:
            current = [day]
        elif day == current[-1] + timedelta(days=1) and len(current) < max_days:
            current.append(day)
        else:
            groups.append(current)
            current = [day]
    if current:
        groups.append(current)
    return groups


def api_json(path: str, token: str, params: dict[str, Any], retries: int = 5) -> dict[str, Any]:
    query = dict(params)
    query["token"] = token
    url = f"{API_ROOT}{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Tempest-Beachhouse-Climate/1.0"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
            status = payload.get("status", {})
            if status.get("status_code", 0) not in (0, None):
                raise RuntimeError(status.get("status_message", "WeatherFlow API error"))
            return payload
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"WeatherFlow request failed after {retries} attempts: {exc}") from exc
            delay = min(30, 2 ** attempt)
            print(f"API request failed ({exc}); retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
    raise AssertionError("unreachable")


def discover_device_id(token: str, station_id: int) -> int:
    payload = api_json("/stations", token, {})
    stations = payload.get("stations") or payload.get("locations") or []
    station = next((s for s in stations if int(s.get("station_id", -1)) == station_id), None)
    if not station:
        raise RuntimeError(f"Station {station_id} was not found for this token")

    item_counts = Counter(
        int(item["device_id"])
        for item in station.get("station_items", [])
        if item.get("device_id") is not None
    )
    candidates: list[tuple[int, int]] = []
    for device in station.get("devices", []):
        if device.get("device_id") is None or not device.get("serial_number"):
            continue
        device_id = int(device["device_id"])
        serial = str(device.get("serial_number", "")).upper()
        dtype = str(device.get("device_type", "")).upper()
        environment = str(device.get("device_meta", {}).get("environment", "")).lower()
        score = item_counts.get(device_id, 0)
        if serial.startswith("ST-"):
            score += 1000
        if dtype in {"ST", "TEMPEST"}:
            score += 1000
        if environment == "outdoor":
            score += 100
        candidates.append((score, device_id))

    if not candidates:
        raise RuntimeError(f"No active device was found for station {station_id}")
    candidates.sort(reverse=True)
    device_id = candidates[0][1]
    print(f"Discovered Tempest device_id={device_id} for station_id={station_id}")
    return device_id


def parse_obs_st(values: Sequence[Any], tz: ZoneInfo) -> dict[str, Any] | None:
    epoch = _num(values, 0)
    if epoch is None:
        return None
    epoch_i = int(epoch)
    timestamp_utc = datetime.fromtimestamp(epoch_i, timezone.utc)
    timestamp_local = timestamp_utc.astimezone(tz)

    temp_c = _num(values, 7)
    rh = _num(values, 8)
    raw_rain_mm = _num(values, 12)
    final_rain_mm = _num(values, 19)
    used_rain_mm = final_rain_mm if final_rain_mm is not None else raw_rain_mm

    return {
        "epoch": epoch_i,
        "timestamp_utc": timestamp_utc.isoformat().replace("+00:00", "Z"),
        "timestamp_local": timestamp_local.isoformat(),
        "temp_f": c_to_f(temp_c),
        "dewpoint_f": c_to_f(dewpoint_c(temp_c, rh)),
        "rh_pct": rh,
        "pressure_mb": _num(values, 6),
        "wind_lull_mph": None if _num(values, 1) is None else _num(values, 1) * MPS_TO_MPH,
        "wind_avg_mph": None if _num(values, 2) is None else _num(values, 2) * MPS_TO_MPH,
        "wind_gust_mph": None if _num(values, 3) is None else _num(values, 3) * MPS_TO_MPH,
        "wind_direction_deg": _num(values, 4),
        "wind_sample_interval_s": _num(values, 5),
        "precip_raw_in": None if raw_rain_mm is None else raw_rain_mm * MM_TO_IN,
        "precip_final_in": None if final_rain_mm is None else final_rain_mm * MM_TO_IN,
        "precip_used_in": None if used_rain_mm is None else used_rain_mm * MM_TO_IN,
        "precip_type": _num(values, 13),
        "lightning_count": int(_num(values, 15) or 0),
        "lightning_avg_distance_mi": None if _num(values, 14) is None else _num(values, 14) * KM_TO_MI,
        "battery_v": _num(values, 16),
        "report_interval_min": _num(values, 17) or 1.0,
        "precip_analysis_type": _num(values, 21),
    }


def fetch_observations(
    token: str,
    device_id: int,
    start_utc: datetime,
    end_utc: datetime,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    payload = api_json(
        f"/observations/device/{device_id}",
        token,
        {
            "time_start": int(start_utc.timestamp()),
            "time_end": int(end_utc.timestamp()),
        },
    )
    obs_type = payload.get("type")
    if obs_type and obs_type != "obs_st":
        raise RuntimeError(
            f"Expected a Tempest device response (obs_st), but the API returned {obs_type!r}. "
            "Set device_id explicitly in config.json if auto-discovery selected the wrong device."
        )
    records: dict[int, dict[str, Any]] = {}
    observations = payload.get("obs") or []
    for values in observations:
        # Some old responses wrapped each observation in one extra list.
        if len(values) == 1 and isinstance(values[0], list):
            values = values[0]
        record = parse_obs_st(values, tz)
        if record:
            records[int(record["epoch"])] = record
    return [records[key] for key in sorted(records)]


def raw_path(root: Path, day: date) -> Path:
    return root / "data" / "raw" / str(day.year) / f"{day.isoformat()}.csv.gz"


def write_raw_day(path: Path, records: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("wb") as raw_file:
        # mtime=0 makes gzip output deterministic so unchanged days do not create git diffs.
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as gz_file:
            with io.TextIOWrapper(gz_file, encoding="utf-8", newline="") as text_file:
                writer = csv.DictWriter(text_file, fieldnames=RAW_FIELDS, lineterminator="\n")
                writer.writeheader()
                for record in records:
                    row = dict(record)
                    for key in (
                        "temp_f", "dewpoint_f", "rh_pct", "pressure_mb", "wind_lull_mph",
                        "wind_avg_mph", "wind_gust_mph", "wind_direction_deg",
                        "wind_sample_interval_s", "precip_raw_in", "precip_final_in",
                        "precip_used_in", "lightning_avg_distance_mi", "battery_v",
                        "report_interval_min", "precip_analysis_type",
                    ):
                        row[key] = fmt(row.get(key), 4)
                    writer.writerow({field: row.get(field, "") for field in RAW_FIELDS})
    temp_path.replace(path)


def read_raw_day(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            parsed: dict[str, Any] = dict(row)
            parsed["epoch"] = int(float(row["epoch"]))
            parsed["timestamp_local_dt"] = datetime.fromisoformat(row["timestamp_local"])
            for key in (
                "temp_f", "dewpoint_f", "rh_pct", "pressure_mb", "wind_lull_mph",
                "wind_avg_mph", "wind_gust_mph", "wind_direction_deg",
                "wind_sample_interval_s", "precip_raw_in", "precip_final_in",
                "precip_used_in", "lightning_avg_distance_mi", "battery_v",
                "report_interval_min", "precip_analysis_type",
            ):
                parsed[key] = float(row[key]) if row.get(key) not in (None, "") else None
            parsed["lightning_count"] = int(float(row.get("lightning_count") or 0))
            parsed["precip_type"] = int(float(row.get("precip_type") or 0))
            yield parsed


@dataclass
class ScalarAccumulator:
    weighted_sum: float = 0.0
    weight_sum: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    min_time: datetime | None = None
    max_time: datetime | None = None

    def add(self, value: float | None, weight: float, when: datetime) -> None:
        if value is None or not math.isfinite(value):
            return
        self.weighted_sum += value * weight
        self.weight_sum += weight
        if self.minimum is None or value < self.minimum:
            self.minimum = value
            self.min_time = when
        if self.maximum is None or value > self.maximum:
            self.maximum = value
            self.max_time = when

    @property
    def mean(self) -> float | None:
        return self.weighted_sum / self.weight_sum if self.weight_sum else None


@dataclass
class PeriodAccumulator:
    obs_count: int = 0
    observation_minutes: float = 0.0
    temp: ScalarAccumulator = field(default_factory=ScalarAccumulator)
    dewpoint: ScalarAccumulator = field(default_factory=ScalarAccumulator)
    rh: ScalarAccumulator = field(default_factory=ScalarAccumulator)
    pressure: ScalarAccumulator = field(default_factory=ScalarAccumulator)
    wind_avg: ScalarAccumulator = field(default_factory=ScalarAccumulator)
    wind_lull: ScalarAccumulator = field(default_factory=ScalarAccumulator)
    wind_gust: ScalarAccumulator = field(default_factory=ScalarAccumulator)
    battery: ScalarAccumulator = field(default_factory=ScalarAccumulator)
    direction_x: float = 0.0
    direction_y: float = 0.0
    sector_weights: Counter[str] = field(default_factory=Counter)
    precip: float = 0.0
    precip_raw: float = 0.0
    wet_minutes: float = 0.0
    lightning_count: int = 0
    lightning_distance_weighted: float = 0.0
    lightning_distance_weight: int = 0
    lightning_closest: float | None = None
    intervals: list[float] = field(default_factory=list)

    def add(self, row: dict[str, Any]) -> None:
        when = row["timestamp_local_dt"]
        interval = row.get("report_interval_min") or 1.0
        interval = max(0.1, min(float(interval), 60.0))
        self.obs_count += 1
        self.observation_minutes += interval
        self.intervals.append(interval)

        self.temp.add(row.get("temp_f"), interval, when)
        self.dewpoint.add(row.get("dewpoint_f"), interval, when)
        self.rh.add(row.get("rh_pct"), interval, when)
        self.pressure.add(row.get("pressure_mb"), interval, when)
        self.wind_avg.add(row.get("wind_avg_mph"), interval, when)
        self.wind_lull.add(row.get("wind_lull_mph"), interval, when)
        self.wind_gust.add(row.get("wind_gust_mph"), interval, when)
        self.battery.add(row.get("battery_v"), interval, when)

        direction = row.get("wind_direction_deg")
        speed = row.get("wind_avg_mph")
        if direction is not None and speed is not None and speed >= 0.5:
            theta = math.radians(direction)
            vector_weight = speed * interval
            self.direction_x += math.sin(theta) * vector_weight
            self.direction_y += math.cos(theta) * vector_weight
            self.sector_weights[cardinal_16(direction)] += interval

        precip = row.get("precip_used_in") or 0.0
        raw_precip = row.get("precip_raw_in") or 0.0
        self.precip += max(0.0, precip)
        self.precip_raw += max(0.0, raw_precip)
        if precip > 0 or row.get("precip_type", 0) > 0:
            self.wet_minutes += interval

        count = int(row.get("lightning_count") or 0)
        distance = row.get("lightning_avg_distance_mi")
        self.lightning_count += count
        if count > 0 and distance is not None:
            self.lightning_distance_weighted += distance * count
            self.lightning_distance_weight += count
            if self.lightning_closest is None or distance < self.lightning_closest:
                self.lightning_closest = distance

    @property
    def vector_direction(self) -> float | None:
        if self.direction_x == 0 and self.direction_y == 0:
            return None
        return math.degrees(math.atan2(self.direction_x, self.direction_y)) % 360.0

    @property
    def prevailing_direction(self) -> str:
        return self.sector_weights.most_common(1)[0][0] if self.sector_weights else ""

    @property
    def lightning_avg_distance(self) -> float | None:
        if not self.lightning_distance_weight:
            return None
        return self.lightning_distance_weighted / self.lightning_distance_weight

    @property
    def report_interval_mode(self) -> float | None:
        if not self.intervals:
            return None
        rounded = [round(value, 2) for value in self.intervals]
        return statistics.multimode(rounded)[0]


def local_time_string(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def summarize_day(day: date, rows: Iterable[dict[str, Any]], expected_minutes: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    daily = PeriodAccumulator()
    hourly: dict[datetime, PeriodAccumulator] = defaultdict(PeriodAccumulator)
    for row in rows:
        daily.add(row)
        hour_key = row["timestamp_local_dt"].replace(minute=0, second=0, microsecond=0)
        hourly[hour_key].add(row)

    vector_dir = daily.vector_direction
    daily_row = {
        "date": day.isoformat(),
        "year": day.year,
        "month": day.month,
        "day": day.day,
        "day_of_year": day.timetuple().tm_yday,
        "obs_count": daily.obs_count,
        "observation_minutes": fmt(daily.observation_minutes, 1),
        "completeness_pct": fmt(min(100.0, daily.observation_minutes / expected_minutes * 100.0), 1),
        "temp_avg_f": fmt(daily.temp.mean, 2),
        "temp_high_f": fmt(daily.temp.maximum, 2),
        "temp_high_time_local": local_time_string(daily.temp.max_time),
        "temp_low_f": fmt(daily.temp.minimum, 2),
        "temp_low_time_local": local_time_string(daily.temp.min_time),
        "dewpoint_avg_f": fmt(daily.dewpoint.mean, 2),
        "dewpoint_high_f": fmt(daily.dewpoint.maximum, 2),
        "dewpoint_low_f": fmt(daily.dewpoint.minimum, 2),
        "rh_avg_pct": fmt(daily.rh.mean, 2),
        "rh_high_pct": fmt(daily.rh.maximum, 2),
        "rh_low_pct": fmt(daily.rh.minimum, 2),
        "pressure_avg_mb": fmt(daily.pressure.mean, 2),
        "pressure_high_mb": fmt(daily.pressure.maximum, 2),
        "pressure_low_mb": fmt(daily.pressure.minimum, 2),
        "wind_avg_mph": fmt(daily.wind_avg.mean, 2),
        "wind_max_1min_mph": fmt(daily.wind_avg.maximum, 2),
        "wind_lull_min_mph": fmt(daily.wind_lull.minimum, 2),
        "wind_gust_mph": fmt(daily.wind_gust.maximum, 2),
        "wind_gust_time_local": local_time_string(daily.wind_gust.max_time),
        "wind_vector_dir_deg": fmt(vector_dir, 1),
        "wind_vector_dir_cardinal": cardinal_16(vector_dir),
        "prevailing_wind_dir": daily.prevailing_direction,
        "precip_in": fmt(daily.precip, 4),
        "precip_raw_in": fmt(daily.precip_raw, 4),
        "wet_minutes": fmt(daily.wet_minutes, 1),
        "lightning_count": daily.lightning_count,
        "lightning_avg_distance_mi": fmt(daily.lightning_avg_distance, 2),
        "lightning_closest_distance_mi": fmt(daily.lightning_closest, 2),
        "battery_min_v": fmt(daily.battery.minimum, 3),
        "report_interval_mode_min": fmt(daily.report_interval_mode, 2),
    }

    hourly_rows: list[dict[str, Any]] = []
    for hour_key, acc in sorted(hourly.items()):
        direction = acc.vector_direction
        hourly_rows.append({
            "hour_local": hour_key.isoformat(),
            "date": day.isoformat(),
            "hour": hour_key.hour,
            "obs_count": acc.obs_count,
            "observation_minutes": fmt(acc.observation_minutes, 1),
            "temp_avg_f": fmt(acc.temp.mean, 2),
            "temp_high_f": fmt(acc.temp.maximum, 2),
            "temp_low_f": fmt(acc.temp.minimum, 2),
            "dewpoint_avg_f": fmt(acc.dewpoint.mean, 2),
            "rh_avg_pct": fmt(acc.rh.mean, 2),
            "pressure_avg_mb": fmt(acc.pressure.mean, 2),
            "wind_avg_mph": fmt(acc.wind_avg.mean, 2),
            "wind_gust_mph": fmt(acc.wind_gust.maximum, 2),
            "wind_vector_dir_deg": fmt(direction, 1),
            "wind_vector_dir_cardinal": cardinal_16(direction),
            "precip_in": fmt(acc.precip, 4),
            "lightning_count": acc.lightning_count,
        })
    return daily_row, hourly_rows


def expected_minutes_for_day(day: date, tz: ZoneInfo) -> int:
    start = datetime.combine(day, dt_time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), dt_time.min, tzinfo=tz)
    return int((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 60)


def rebuild_summaries(root: Path, config: dict[str, Any], tz: ZoneInfo) -> None:
    raw_files = sorted((root / "data" / "raw").glob("*/*.csv.gz"))
    daily_rows: list[dict[str, Any]] = []
    hourly_rows: list[dict[str, Any]] = []

    for path in raw_files:
        day = date.fromisoformat(path.name.removesuffix(".csv.gz"))
        day_row, day_hours = summarize_day(day, read_raw_day(path), expected_minutes_for_day(day, tz))
        daily_rows.append(day_row)
        hourly_rows.extend(day_hours)

    docs_data = root / "docs" / "data"
    docs_data.mkdir(parents=True, exist_ok=True)
    with (docs_data / "daily.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=DAILY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(daily_rows)
    with (docs_data / "hourly.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=HOURLY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(hourly_rows)

    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    metadata = {
        "station_name": config.get("station_name", "Tempest Station"),
        "station_id": int(config["station_id"]),
        "timezone": config.get("timezone", "America/Chicago"),
        "latitude": config.get("latitude"),
        "longitude": config.get("longitude"),
        "archive_start_configured": config.get("archive_start"),
        "data_start": daily_rows[0]["date"] if daily_rows else None,
        "data_end": daily_rows[-1]["date"] if daily_rows else None,
        "days_archived": len(daily_rows),
        "hourly_rows": len(hourly_rows),
        "generated_at_utc": generated,
        "raw_frequency": "WeatherFlow historical one-minute observations",
        "units": {
            "temperature": "degF",
            "dewpoint": "degF",
            "relative_humidity": "percent",
            "pressure": "mb station pressure",
            "wind": "mph",
            "precipitation": "inches",
            "lightning_distance": "miles",
        },
    }
    (docs_data / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Rebuilt summaries: {len(daily_rows)} daily rows, {len(hourly_rows)} hourly rows")


def existing_raw_dates(root: Path) -> list[date]:
    dates: list[date] = []
    for path in (root / "data" / "raw").glob("*/*.csv.gz"):
        try:
            dates.append(date.fromisoformat(path.name.removesuffix(".csv.gz")))
        except ValueError:
            continue
    return sorted(dates)


def collect_days(
    root: Path,
    config: dict[str, Any],
    token: str,
    days: Sequence[date],
    force_refresh: bool,
) -> None:
    if not days:
        print("No dates require collection")
        return
    tz = ZoneInfo(config.get("timezone", "America/Chicago"))
    station_id = int(config["station_id"])
    device_id = int(config["device_id"]) if config.get("device_id") else discover_device_id(token, station_id)

    requested = sorted(set(days))
    if not force_refresh:
        requested = [day for day in requested if not raw_path(root, day).exists()]
    if not requested:
        print("All requested raw day files already exist")
        return

    for group in group_consecutive_days(requested, max_days=4):
        start_local = datetime.combine(group[0], dt_time.min, tzinfo=tz)
        end_local = datetime.combine(group[-1] + timedelta(days=1), dt_time.min, tzinfo=tz)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)
        print(f"Fetching {group[0]} through {group[-1]} ({start_utc.isoformat()} to {end_utc.isoformat()})")
        records = fetch_observations(token, device_id, start_utc, end_utc, tz)
        by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            local_day = datetime.fromisoformat(record["timestamp_local"]).date()
            if local_day in group:
                by_day[local_day].append(record)
        for day in group:
            day_records = by_day.get(day, [])
            if not day_records:
                print(f"WARNING: no observations returned for {day}", file=sys.stderr)
            write_raw_day(raw_path(root, day), day_records)
            print(f"Wrote {len(day_records):,} observations for {day}")
        time.sleep(0.25)


def determine_daily_days(root: Path, archive_start: date, yesterday: date, refresh_days: int) -> list[date]:
    if yesterday < archive_start:
        return []
    existing = existing_raw_dates(root)
    refresh_start = max(archive_start, yesterday - timedelta(days=max(1, refresh_days) - 1))
    targets = set(daterange(refresh_start, yesterday))
    if existing:
        catchup_start = max(archive_start, existing[-1] + timedelta(days=1))
        if catchup_start <= yesterday:
            targets.update(daterange(catchup_start, yesterday))
    return sorted(targets)


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    for required in ("station_id", "timezone", "archive_start"):
        if required not in config:
            raise RuntimeError(f"config.json is missing required key: {required}")
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("daily", "backfill", "rebuild"), required=True)
    parser.add_argument("--start-date", type=parse_date)
    parser.add_argument("--end-date", type=parse_date)
    parser.add_argument("--refresh-days", type=int, default=7)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--config", default="config.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / args.config)
    tz = ZoneInfo(config["timezone"])

    if args.mode == "rebuild":
        rebuild_summaries(root, config, tz)
        return 0

    token = os.environ.get("TEMPEST_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TEMPEST_TOKEN is not set. Add it as a GitHub Actions repository secret.")

    archive_start = parse_date(config["archive_start"])
    today_local = datetime.now(tz).date()
    yesterday = today_local - timedelta(days=1)

    if args.mode == "backfill":
        start = args.start_date or archive_start
        end = args.end_date or yesterday
        if start < archive_start:
            print(f"Clamping start date to configured archive start {archive_start}")
            start = archive_start
        if end > yesterday:
            print(f"Clamping end date to last completed local day {yesterday}")
            end = yesterday
        if end < start:
            raise RuntimeError(f"End date {end} is before start date {start}")
        days = list(daterange(start, end))
        collect_days(root, config, token, days, force_refresh=args.force_refresh)
    else:
        days = determine_daily_days(root, archive_start, yesterday, args.refresh_days)
        # Daily collection intentionally refreshes recent files so corrected Rain Check
        # precipitation can replace preliminary values.
        collect_days(root, config, token, days, force_refresh=True)

    rebuild_summaries(root, config, tz)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - workflow should print a concise fatal error
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
