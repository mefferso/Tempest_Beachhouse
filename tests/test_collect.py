import importlib.util
import math
import sys
import unittest
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect.py"
SPEC = importlib.util.spec_from_file_location("collect", MODULE_PATH)
collect = importlib.util.module_from_spec(SPEC)
sys.modules["collect"] = collect
assert SPEC.loader
SPEC.loader.exec_module(collect)


class CollectTests(unittest.TestCase):
    def test_dewpoint(self):
        value = collect.dewpoint_c(30.0, 70.0)
        self.assertIsNotNone(value)
        self.assertAlmostEqual(value, 23.9, places=1)

    def test_parse_tempest_observation(self):
        values = [
            1603481377, 0, 0.09, 0.54, 33, 6, 1014.8, 28.8, 71,
            16639, 1.83, 139, 1.27, 1, 8, 2, 2.42, 1, 0.07615,
            1.016, 0.07615, 1,
        ]
        row = collect.parse_obs_st(values, ZoneInfo("America/Chicago"))
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["temp_f"], 83.84, places=2)
        self.assertAlmostEqual(row["wind_gust_mph"], 0.54 * collect.MPS_TO_MPH, places=3)
        self.assertAlmostEqual(row["precip_used_in"], 1.016 / 25.4, places=4)
        self.assertEqual(row["lightning_count"], 2)

    def test_vector_direction_wraps_north(self):
        acc = collect.PeriodAccumulator()
        base = {
            "timestamp_local_dt": __import__("datetime").datetime.fromisoformat("2026-01-01T00:00:00-06:00"),
            "report_interval_min": 1.0,
            "wind_avg_mph": 10.0,
            "wind_lull_mph": 5.0,
            "wind_gust_mph": 15.0,
            "temp_f": 60.0,
            "dewpoint_f": 50.0,
            "rh_pct": 70.0,
            "pressure_mb": 1015.0,
            "precip_used_in": 0.0,
            "precip_raw_in": 0.0,
            "precip_type": 0,
            "lightning_count": 0,
            "lightning_avg_distance_mi": None,
            "battery_v": 2.4,
        }
        for direction in (350.0, 10.0):
            row = dict(base, wind_direction_deg=direction)
            acc.add(row)
        direction = acc.vector_direction
        self.assertTrue(direction < 1.0 or direction > 359.0)
        self.assertEqual(collect.cardinal_16(direction), "N")

    def test_dst_day_lengths(self):
        tz = ZoneInfo("America/Chicago")
        self.assertEqual(collect.expected_minutes_for_day(date(2026, 3, 8), tz), 1380)
        self.assertEqual(collect.expected_minutes_for_day(date(2026, 11, 1), tz), 1500)

    def test_chunking_never_exceeds_four_days(self):
        days = [date(2026, 1, day) for day in range(1, 11)]
        groups = collect.group_consecutive_days(days, max_days=4)
        self.assertEqual([len(group) for group in groups], [4, 4, 2])


if __name__ == "__main__":
    unittest.main()
