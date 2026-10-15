"""Download public inputs.

- PVGIS (EU JRC): hourly output of 1 kWp single-axis tracker (horizontal N-S axis),
  14% system losses, ERA5 weather. Default site: Ullum area, San Juan (Cuyo).
- CAMMESA: hourly SADI demand, same loader as argentina-bess-valuation.

    python data/fetch_data.py --lat -31.6 --lon -68.6 --year 2021
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd
import requests

RAW = Path(__file__).parent / "raw"
PVGIS = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"
CAMMESA = "https://api.cammesa.com/demanda-svc/demanda/ObtieneDemandaYTemperaturaRegionByFecha"


def fetch_pvgis(lat: float, lon: float, year: int) -> Path:
    params = dict(lat=lat, lon=lon, startyear=year, endyear=year, pvcalculation=1,
                  peakpower=1, loss=14, trackingtype=1, outputformat="json")
    r = requests.get(PVGIS, params=params, timeout=120)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["outputs"]["hourly"])
    t = pd.to_datetime(df["time"], format="%Y%m%d:%H%M", utc=True).dt.floor("h")
    s = pd.Series(df["P"].to_numpy() / 1000, index=t, name="cf")  # kW per kWp
    out = RAW / f"pvgis_tracker_{lat}_{lon}_{year}.csv"
    s.to_csv(out)
    print(f"PVGIS: {len(s)} h, capacity factor {s.mean():.1%}")
    return out


def fetch_cammesa(days_back: int = 200, region: int = 1002) -> Path:
    today, frames = dt.date.today(), []
    for d in range(days_back, 0, -1):
        day = (today - dt.timedelta(days=d)).isoformat()
        rows = requests.get(CAMMESA, params={"fecha": day, "id_region": region}, timeout=30).json()
        if rows:
            frames.append(pd.DataFrame(rows))
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["fecha"], format="%Y-%m-%dT%H:%M:%S.%f%z")
    hourly = df.set_index("time")[["dem", "temp"]].sort_index().resample("1h").mean().dropna(subset=["dem"])
    out = RAW / "cammesa_sadi_demand_hourly.csv"
    hourly.rename(columns={"dem": "demand_mw", "temp": "temp_c"}).to_csv(out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=-31.6)
    ap.add_argument("--lon", type=float, default=-68.6)
    ap.add_argument("--year", type=int, default=2021)
    ap.add_argument("--skip-cammesa", action="store_true")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    fetch_pvgis(a.lat, a.lon, a.year)
    if not a.skip_cammesa:
        fetch_cammesa()
