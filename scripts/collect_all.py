#!/usr/bin/env python3
"""Run the existing Tempest collector independently for every configured station."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def copy_tree_contents(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    settings = json.loads((root / "stations.json").read_text(encoding="utf-8"))
    timezone = settings.get("timezone", "America/Chicago")
    args = sys.argv[1:]

    station_index = []
    for station in settings["stations"]:
        station_id = str(station["station_id"])
        print(f"\n=== Collecting {station['station_name']} ({station_id}) ===", flush=True)

        permanent_raw = root / "data" / "stations" / station_id / "raw"
        permanent_summary = root / "docs" / "data" / "stations" / station_id

        # Migrate the original single-station beach archive on the first run.
        if station_id == "135442" and not permanent_raw.exists():
            copy_tree_contents(root / "data" / "raw", permanent_raw)

        with tempfile.TemporaryDirectory(prefix=f"tempest-{station_id}-") as temp_name:
            work = Path(temp_name)
            (work / "scripts").mkdir(parents=True)
            shutil.copy2(root / "scripts" / "collect.py", work / "scripts" / "collect.py")
            copy_tree_contents(permanent_raw, work / "data" / "raw")

            station_config = {
                "station_name": station["station_name"],
                "station_id": station["station_id"],
                "device_id": station.get("device_id"),
                "archive_start": station["archive_start"],
                "timezone": timezone,
                "latitude": station["latitude"],
                "longitude": station["longitude"],
            }
            (work / "config.json").write_text(
                json.dumps(station_config, indent=2) + "\n", encoding="utf-8"
            )

            subprocess.run(
                [sys.executable, str(work / "scripts" / "collect.py"), *args],
                cwd=work,
                check=True,
            )

            if permanent_raw.exists():
                shutil.rmtree(permanent_raw)
            copy_tree_contents(work / "data" / "raw", permanent_raw)
            if permanent_summary.exists():
                shutil.rmtree(permanent_summary)
            copy_tree_contents(work / "docs" / "data", permanent_summary)

        metadata_path = permanent_summary / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        station_index.append({
            "station_id": station["station_id"],
            "name": station["station_name"],
            "short_name": station.get("short_name", station["station_name"]),
            "latitude": station["latitude"],
            "longitude": station["longitude"],
            "data_start": metadata.get("data_start"),
            "data_end": metadata.get("data_end"),
            "days_archived": metadata.get("days_archived", 0),
        })

    index_path = root / "docs" / "data" / "stations.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps({"stations": station_index}, indent=2) + "\n", encoding="utf-8")
    print("\nFinished all configured stations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
