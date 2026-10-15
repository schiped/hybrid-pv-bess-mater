"""Joint sizing and dispatch of a PV + BESS hybrid behind a fixed export limit (Pyomo + HiGHS).

Decision variables: PV DC capacity, inverter AC capacity, battery power and energy,
plus hourly dispatch over a representative year. The objective is lifetime NPV:
annual margin times an annuity factor minus capex. Two topologies:

- DC-coupled: the battery charges on the DC bus through a DC/DC converter, so it can
  catch inverter clipping, and discharges through the shared PV inverter.
- AC-coupled: the battery has its own PCS and can only charge from PV output after
  the PV inverter; PV clipping is lost.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyomo.environ as pyo

from .config import Battery, Costs, Market, Site


@dataclass
class Fixed:
    """Optional fixed sizes; None leaves the variable free."""
    pv_mwp: float | None = None
    inv_mw: float | None = None
    bess_mw: float | None = None
    bess_mwh: float | None = None


def annuity(rate: float, years: int) -> float:
    return (1 - (1 + rate) ** -years) / rate


def solve(
    cf: pd.Series, spot: pd.Series, grid: pd.Series, site: Site, costs: Costs, mk: Market,
    bat: Battery, coupling: str = "dc", fixed: Fixed | None = None,
) -> dict:
    fixed = fixed or Fixed()
    T = range(len(cf))
    c, g = cf.to_numpy(), grid.to_numpy()
    # All exports sell at spot. The PPA is a financial hedge (contract for differences) on a
    # fixed reference solar profile (DC/AC 1.3 behind the POI), so it moves revenue but not
    # dispatch, and the plant cannot game it by routing energy through the battery.
    s_spot = spot.to_numpy()
    price = s_spot
    ref = np.minimum(c * 1.3, 1.0) * site.poi_mw
    cfd = float(np.sum(mk.ppa_share * ref * (mk.ppa_usd_mwh - s_spot)))
    af = annuity(costs.wacc, costs.life_years)

    m = pyo.ConcreteModel()
    m.pv = pyo.Var(bounds=(0, 4 * site.poi_mw))
    m.inv = pyo.Var(bounds=(0, site.poi_mw))  # AC capacity cannot exceed the grid permit
    m.pb = pyo.Var(bounds=(0, 2 * site.poi_mw))
    m.eb = pyo.Var(bounds=(0, 12 * site.poi_mw))
    for var, val in [(m.pv, fixed.pv_mwp), (m.inv, fixed.inv_mw), (m.pb, fixed.bess_mw), (m.eb, fixed.bess_mwh)]:
        if val is not None:
            var.fix(val)

    m.to_inv = pyo.Var(T, within=pyo.NonNegativeReals)  # PV DC power sent to inverter
    m.ch = pyo.Var(T, within=pyo.NonNegativeReals)
    m.dis = pyo.Var(T, within=pyo.NonNegativeReals)
    m.soc = pyo.Var(T, within=pyo.NonNegativeReals)
    m.exp = pyo.Var(T, within=pyo.NonNegativeReals)

    m.c = pyo.ConstraintList()
    m.c.add(m.pv <= costs.max_dc_ac * m.inv)
    m.c.add(m.eb >= bat.min_hours * m.pb)
    m.c.add(m.eb <= bat.max_hours * m.pb)
    n = len(c)
    for t in T:
        m.c.add(m.ch[t] <= m.pb)
        m.c.add(m.dis[t] <= m.pb)
        m.c.add(m.soc[t] <= m.eb)
        prev = m.soc[n - 1] if t == 0 else m.soc[t - 1]  # cyclic year
        m.c.add(m.soc[t] == prev + bat.eta_charge * m.ch[t] - m.dis[t] / bat.eta_discharge)
        m.c.add(m.exp[t] <= site.poi_mw * g[t])
        if coupling == "dc":
            m.c.add(m.to_inv[t] + m.ch[t] <= c[t] * m.pv)
            m.c.add(m.to_inv[t] + m.dis[t] <= m.inv)
            m.c.add(m.exp[t] == m.to_inv[t] + m.dis[t])
        else:
            m.c.add(m.to_inv[t] <= c[t] * m.pv)
            m.c.add(m.to_inv[t] <= m.inv)
            m.c.add(m.ch[t] <= m.to_inv[t])
            m.c.add(m.exp[t] == m.to_inv[t] - m.ch[t] + m.dis[t])

    power_conv = costs.dcdc_usd_per_kw if coupling == "dc" else costs.pcs_usd_per_kw
    capex = (
        costs.pv_usd_per_wp * 1e6 * m.pv
        + costs.inverter_usd_per_wac * 1e6 * m.inv
        + power_conv * 1e3 * m.pb
        + costs.bess_usd_per_kwh * 1e3 * m.eb
    )
    om = costs.pv_om_usd_per_kwp_year * 1e3 * m.pv + costs.bess_om_share * (
        power_conv * 1e3 * m.pb + costs.bess_usd_per_kwh * 1e3 * m.eb
    )
    revenue = sum(s_spot[t] * m.exp[t] for t in T) + cfd
    m.obj = pyo.Objective(expr=af * (revenue - om) - capex, sense=pyo.maximize)

    res = pyo.SolverFactory("appsi_highs").solve(m)
    assert res.solver.termination_condition == pyo.TerminationCondition.optimal, res.solver.termination_condition

    v = lambda x: np.array([pyo.value(x[t]) for t in T])
    hourly = pd.DataFrame(
        {"pv_avail": c * pyo.value(m.pv), "to_inv": v(m.to_inv), "charge": v(m.ch), "discharge": v(m.dis),
         "soc": v(m.soc), "export": v(m.exp), "price": price, "spot": spot.to_numpy(), "grid": g},
        index=cf.index,
    )
    sizes = {k: float(pyo.value(getattr(m, k))) for k in ("pv", "inv", "pb", "eb")}
    capex_v = float(pyo.value(capex))
    om_v = float(pyo.value(om))
    rev_v = float(pyo.value(revenue))
    energy = float(hourly.export.sum())
    avail = float(hourly.pv_avail.sum())
    return {
        "coupling": coupling,
        "priority": site.priority,
        "sizes": sizes,
        "npv_musd": float(pyo.value(m.obj)) / 1e6,
        "capex_musd": capex_v / 1e6,
        "revenue_musd_year": rev_v / 1e6,
        "om_musd_year": om_v / 1e6,
        "export_gwh": energy / 1e3,
        "pv_available_gwh": avail / 1e3,
        "spilled_share": 1 - float((hourly.to_inv + (hourly.charge if coupling == "dc" else 0)).sum()) / avail if avail else 0.0,
        "capture_price": rev_v / energy if energy else 0.0,
        "lcoe_usd_mwh": (capex_v / af + om_v) / energy if energy else 0.0,
        "hourly": hourly,
    }
