"""Representative-year inputs: solar profile, spot price and grid availability."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import Market, Site

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"
TZ = "America/Argentina/Buenos_Aires"


def solar_cf(year: int = 2021) -> pd.Series:
    f = next(RAW.glob(f"pvgis_tracker_*_{year}.csv"))
    s = pd.read_csv(f, index_col=0).iloc[:, 0]
    s.index = pd.to_datetime(s.index, utc=True).tz_convert(TZ)
    return s.rename("cf")


def spot_price(index: pd.DatetimeIndex, mk: Market) -> pd.Series:
    """CMg proxy on a representative year.

    CAMMESA's public API only serves recent months, so each month uses the
    hour-of-day x weekday demand shape of the nearest observed month, pushed
    through the convex merit-order curve and scaled to that month's reference CMg.
    """
    d = pd.read_csv(RAW / "cammesa_sadi_demand_hourly.csv", index_col=0)["demand_mw"]
    d.index = pd.to_datetime(d.index, utc=True).tz_convert(TZ)
    shape = d.groupby([d.index.month, d.index.dayofweek >= 5, d.index.hour]).mean()
    observed = sorted(shape.index.get_level_values(0).unique())
    nearest = {m: min(observed, key=lambda o: min(abs(o - m), 12 - abs(o - m))) for m in range(1, 13)}
    keys = list(zip([nearest[m] for m in index.month], index.dayofweek >= 5, index.hour))
    dem = pd.Series(shape.loc[keys].to_numpy(), index=index)
    rel = dem / dem.groupby(index.month).transform("mean")
    p = rel**mk.elasticity
    p = p / p.groupby(index.month).transform("mean")
    level = pd.Series(index.month, index=index).map(mk.monthly_cmg_usd)
    return (level * p * mk.spot_share_of_cmg).rename("spot")


def grid_availability(cf: pd.Series, site: Site, dc_ac: float = 1.3) -> pd.Series:
    """Share of the POI available each hour.

    Plena: always 1. Referencial A: in congested hours (high regional solar) the
    export limit drops by kappa, calibrated so a PV-only plant at the given DC/AC
    ratio loses site.ref_a_curtailment of its energy.
    """
    if site.priority == "plena":
        return pd.Series(1.0, index=cf.index)
    congested = cf >= cf[cf > 0].quantile(site.congestion_quantile)
    pv_ac = np.minimum(cf * dc_ac, 1.0)
    lo, hi = 0.0, 1.0
    for _ in range(60):
        k = (lo + hi) / 2
        g = np.where(congested, 1 - k, 1.0)
        lost = (pv_ac - np.minimum(pv_ac, g)).sum() / pv_ac.sum()
        lo, hi = (k, hi) if lost < site.ref_a_curtailment else (lo, k)
    return pd.Series(np.where(congested, 1 - (lo + hi) / 2, 1.0), index=cf.index)
