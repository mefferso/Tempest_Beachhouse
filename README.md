# Tempest Beachhouse Climate Archive

A self-maintaining climate archive and static website for the WeatherFlow Tempest at the Dauphin Island beach house.

The project downloads WeatherFlow's historical **one-minute Tempest observations**, stores one compressed raw file per local calendar day, calculates hourly and daily statistics, and publishes an interactive climate dashboard through GitHub Pages.

## What is archived

The raw archive preserves:

- Air temperature
- Relative humidity and calculated dew point
- Station pressure
- Wind lull, one-minute average, peak three-second gust, and direction
- Rain accumulation, including Rain Check-corrected accumulation when available
- Lightning strike count and average strike distance
- Battery voltage, report interval, precipitation type, and data-quality information

All WeatherFlow API values are normalized to °F, mph, inches, miles, and millibars. Calendar days use `America/Chicago`, including 23- and 25-hour daylight-saving transition days.

## Data products

| Path | Purpose |
|---|---|
| `data/raw/YYYY/YYYY-MM-DD.csv.gz` | Complete one-minute source archive for each local day |
| `docs/data/hourly.csv` | Hourly summaries for future charts and analysis |
| `docs/data/daily.csv` | Daily climate summaries used by the website |
| `docs/data/metadata.json` | Archive coverage and station metadata |
| `docs/` | Static GitHub Pages website |

The collector requests no more than four local days at once because WeatherFlow limits one-minute historical requests to ranges of five days or less.

## Daily statistics

Each completed day includes:

- Mean, high, and low temperature with occurrence times
- Mean, high, and low dew point
- Mean, high, and low RH
- Mean, high, and low station pressure
- Mean wind, highest one-minute wind, minimum lull, peak three-second gust, and gust time
- Speed-weighted vector wind direction and prevailing 16-point compass sector
- Corrected and uncorrected precipitation totals
- Wet minutes
- Lightning total, strike-weighted average distance, and closest interval-average distance
- Observation count, observed minutes, and completeness percentage

Dew point uses the Magnus formulation. Wind direction uses circular/vector math; it does not incorrectly average 350° and 10° into 180°.

## Required GitHub secret

The API token is never committed to the repository. Create a fresh WeatherFlow personal access token, then add it to this repository:

1. Open **Settings** in this repository.
2. Select **Secrets and variables → Actions**.
3. Click **New repository secret**.
4. Name it exactly `TEMPEST_TOKEN`.
5. Paste the new WeatherFlow token and save it.

The token previously embedded in the old dashboard source should be revoked and replaced because it was exposed as plain text.

## Initial historical backfill

After adding the secret:

1. Open the repository's **Actions** tab.
2. Choose **Collect Tempest climate data**.
3. Click **Run workflow**.
4. Select `backfill`.
5. Leave the start date at `2024-03-18` and the end date blank.
6. Leave **force refresh** off for the first run.
7. Run the workflow.

The backfill is restartable. Existing daily raw files are skipped, so rerunning it continues any unfinished archive rather than downloading everything again.

## Automatic daily update

The collection workflow runs every day at **08:17 UTC**—2:17 AM CST or 3:17 AM CDT—and retrieves the last seven local days. Re-fetching recent days allows WeatherFlow Rain Check corrections to replace preliminary rain values. Deterministic gzip output prevents unchanged files from creating pointless commits.

The daily workflow also catches up dates after the newest archived raw day if a scheduled run was missed.

## Publish the website

1. Open **Settings → Pages**.
2. Under **Build and deployment**, choose **GitHub Actions** as the source.
3. Open **Actions → Deploy climate website** and run it once if it has not already run.

The site will be available at:

`https://mefferso.github.io/Tempest_Beachhouse/`

## Website period modes

The dashboard supports:

- **Actual dates:** summarize one continuous date range.
- **Calendar climatology:** choose a month/day period, such as November 10–20, and combine that same period from every available year.

It calculates average highs/lows/means, records and dates, wind statistics, rainfall, lightning, pressure, data completeness, charts, a daily table, and a downloadable CSV for the selected period.

## Manual commands

Run tests:

```bash
python -m unittest discover -s tests -v
```

Rebuild summaries from existing raw files without calling WeatherFlow:

```bash
python scripts/collect.py --mode rebuild
```

Run a local backfill with the token in the environment:

```bash
export TEMPEST_TOKEN="your-token"
python scripts/collect.py --mode backfill --start-date 2024-03-18
```

Force replacement of existing raw files for a specific period:

```bash
python scripts/collect.py \
  --mode backfill \
  --start-date 2026-07-01 \
  --end-date 2026-07-31 \
  --force-refresh
```

## Station configuration

Public station settings live in `config.json`:

- Station ID: `135442`
- Archive start: `2024-03-18`
- Time zone: `America/Chicago`
- Location: Dauphin Island, Alabama

The collector normally discovers the Tempest device ID automatically from the station metadata. A specific `device_id` can be placed in `config.json` if automatic discovery ever chooses the wrong device.
