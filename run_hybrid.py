"""Size and value a 100 MW-export PV + BESS hybrid in San Juan under MATER.

Runs DC- vs AC-coupling x Plena vs Referencial A dispatch priority, a spread
sensitivity, and writes the development pack (capex, schedule, risks) to results/.

    python run_hybrid.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from hybrid.config import Battery, Costs, Market, Site
from hybrid.data import grid_availability, solar_cf, spot_price
from hybrid.model import Fixed, solve
from hybrid.plotting import COLORS, style

OUT = Path(__file__).parent / "results"


def capex_table(r: dict, costs: Costs) -> pd.DataFrame:
    s = r["sizes"]
    conv = costs.dcdc_usd_per_kw if r["coupling"] == "dc" else costs.pcs_usd_per_kw
    rows = [
        ("PV modules, trackers, DC BOS", f"{s['pv']:.0f} MWp", costs.pv_usd_per_wp * s["pv"]),
        ("PV inverters", f"{s['inv']:.0f} MWac", costs.inverter_usd_per_wac * s["inv"]),
        ("BESS energy (DC block + BOS)", f"{s['eb']:.0f} MWh", costs.bess_usd_per_kwh * s["eb"] / 1e3),
        ("BESS power conversion", f"{s['pb']:.0f} MW", conv * s["pb"] / 1e3),
    ]
    df = pd.DataFrame(rows, columns=["item", "size", "musd"])
    df.loc[len(df)] = ["Total", "", df.musd.sum()]
    return df


SCHEDULE = [  # task, start month, duration months, group
    ("Grid access + dispatch priority (CAMMESA/MATER)", 0, 4, "dev"),
    ("Environmental permit (provincial)", 0, 6, "dev"),
    ("PPA with GUMA/GUDI signed", 2, 4, "dev"),
    ("EPC + BESS supply tender", 4, 4, "dev"),
    ("Financial close", 7, 1, "dev"),
    ("Mobilisation, civils, piling", 8, 4, "build"),
    ("Tracker + module installation", 10, 6, "build"),
    ("Main transformer (long lead)", 8, 11, "supply"),
    ("BESS containers: shipping + customs", 11, 6, "supply"),
    ("MV collection + substation", 13, 6, "build"),
    ("BESS installation + DC/DC", 16, 3, "build"),
    ("Energisation + CAMMESA compliance tests", 19, 2, "build"),
    ("Commercial operation (habilitación comercial)", 21, 1, "milestone"),
]

RISKS = [
    ("Transformer and HV equipment lead time", "High", "High", "Order at financial close; reserve slot with OEM; float on critical path"),
    ("BESS import: customs, FX and capital controls", "Medium", "High", "Use RIGI import regime; DDP terms; FX clause in EPC"),
    ("Referencial A curtailment above 8% before line works", "Medium", "Medium", "Size BESS on curtailed energy; DC coupling; monitor CAMMESA quarterly priority reports"),
    ("Tracker piling refusal on alluvial soils", "Medium", "Medium", "Pull-out test campaign before design freeze; pre-drilling allowance"),
    ("Grid compliance tests (PT CAMMESA) delayed", "Medium", "Medium", "Early model validation; plant controller tested in factory"),
    ("Spot spread compresses as storage is built", "High", "Medium", "Contract part of BESS output; test NPV against low-spread case"),
    ("PPA counterparty credit (GUMA)", "Low", "High", "Bank guarantee or letter of credit; diversify offtakers"),
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    style()
    costs, mk, bat = Costs(), Market(), Battery()
    cf = solar_cf()
    spot = spot_price(cf.index, mk)

    results = {}
    for pr in ("plena", "referencial_a"):
        site = Site(priority=pr)
        g = grid_availability(cf, site)
        for cp in ("dc", "ac"):
            results[(pr, cp)] = solve(cf, spot, g, site, costs, mk, bat, cp)
            r = results[(pr, cp)]
            print(f"{pr:>14} {cp}: PV {r['sizes']['pv']:.0f} MWp, BESS {r['sizes']['pb']:.0f} MW / {r['sizes']['eb']:.0f} MWh, NPV {r['npv_musd']:.1f} MUSD")

    # PV-only reference with the same grid access
    site = Site()
    pv_only = solve(cf, spot, grid_availability(cf, site), site, costs, mk, bat, "dc", Fixed(bess_mw=0, bess_mwh=0))

    # Spread sensitivity (DC, Plena)
    sens = []
    for el in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
        m2 = replace(mk, elasticity=el)
        sp2 = spot_price(cf.index, m2)
        d = sp2.groupby(sp2.index.date)
        spread = float(d.apply(lambda x: x.nlargest(4).mean() - x.nsmallest(4).mean()).mean())
        r = solve(cf, sp2, grid_availability(cf, site), site, costs, m2, bat, "dc")
        sens.append({"elasticity": el, "spread": spread, "bess_mw": r["sizes"]["pb"], "bess_mwh": r["sizes"]["eb"], "npv": r["npv_musd"]})
        print(f"spread {spread:.0f}: BESS {r['sizes']['pb']:.0f} MW / {r['sizes']['eb']:.0f} MWh")
    sens = pd.DataFrame(sens)

    base = results[("plena", "dc")]

    # ---------- figures ----------
    # 1. Typical days
    h = base["hourly"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True)
    for ax, (label, day) in zip(axes, [("Summer", "2021-01-12"), ("Winter", "2021-07-13")]):
        d = h[h.index.strftime("%Y-%m-%d") == day]
        ax.fill_between(d.index, d.pv_avail, color="#f3c25d", alpha=0.35, lw=0, label="PV available (DC)")
        ax.plot(d.index, d.export, color=COLORS[0], lw=2, label="Export at POI")
        ax.bar(d.index, d.discharge, width=0.035, color=COLORS[2], label="Battery discharge")
        ax.bar(d.index, -d.charge, width=0.035, color=COLORS[1], label="Battery charge")
        ax.axhline(100, color="#999", lw=0.8, ls="--")
        tw = ax.twinx()
        tw.plot(d.index, d.spot, color="#4b5664", lw=1, ls=":")
        tw.set_ylim(0, h.spot.max() * 1.1)
        tw.set_ylabel("Spot USD/MWh" if label == "Winter" else "")
        tw.grid(False)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=h.index.tz))
        ax.set_title(f"{label} day", loc="left")
    axes[0].set_ylabel("MW")
    axes[0].legend(fontsize=8, frameon=False, loc="upper left")
    fig.suptitle("DC-coupled hybrid, 160 MWp behind a 100 MW export limit (dotted line: spot proxy)", x=0.01, ha="left", fontsize=11.5, fontweight="semibold")
    fig.tight_layout()
    fig.savefig(OUT / "fig1_typical_days.png", dpi=160)
    plt.close(fig)

    # 2. Scenario comparison
    labels = ["Plena\nDC", "Plena\nAC", "Ref. A\nDC", "Ref. A\nAC"]
    keys = [("plena", "dc"), ("plena", "ac"), ("referencial_a", "dc"), ("referencial_a", "ac")]
    fig, ax = plt.subplots(1, 3, figsize=(11, 3.9))
    ax[0].bar(labels, [results[k]["npv_musd"] for k in keys], color=COLORS[0])
    ax[0].axhline(pv_only["npv_musd"], color=COLORS[1], ls="--", lw=1.2)
    ax[0].text(3.4, pv_only["npv_musd"], "PV only", color=COLORS[1], fontsize=8.5, va="bottom", ha="right")
    ax[0].set_title("NPV, MUSD", loc="left")
    ax[1].bar(labels, [results[k]["sizes"]["eb"] for k in keys], color=COLORS[2])
    ax[1].set_title("Optimal BESS energy, MWh", loc="left")
    ax[2].bar(labels, [results[k]["spilled_share"] * 100 for k in keys], color=COLORS[3])
    ax[2].set_title("PV energy spilled, %", loc="left")
    fig.tight_layout()
    fig.savefig(OUT / "fig2_scenarios.png", dpi=160)
    plt.close(fig)

    # 3. Spread sensitivity
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(sens.spread, sens.bess_mwh, "o-", color=COLORS[0], label="BESS energy, MWh")
    ax.plot(sens.spread, sens.bess_mw, "s--", color=COLORS[1], label="BESS power, MW")
    ax.set_xlabel("Average daily spread, top 4 h minus bottom 4 h, USD/MWh")
    ax.set_title("The evening spread sizes the battery", loc="left")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "fig3_spread_sensitivity.png", dpi=160)
    plt.close(fig)

    # 4. Construction schedule
    group_color = {"dev": COLORS[3], "build": COLORS[0], "supply": COLORS[1], "milestone": COLORS[2]}
    fig, ax = plt.subplots(figsize=(11, 4.8))
    for i, (task, start, dur, grp) in enumerate(SCHEDULE):
        ax.barh(i, dur, left=start, color=group_color[grp], height=0.6)
    ax.set_yticks(range(len(SCHEDULE)), [s[0] for s in SCHEDULE], fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlabel("Months from notice to proceed on development")
    ax.set_title("Indicative schedule: critical path runs through the transformer and BESS logistics", loc="left")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(OUT / "fig4_schedule.png", dpi=160)
    plt.close(fig)

    # ---------- tables ----------
    capex = capex_table(base, costs)
    capex.to_csv(OUT / "capex_base.csv", index=False)
    pd.DataFrame(RISKS, columns=["risk", "likelihood", "impact", "mitigation"]).to_csv(OUT / "risk_register.csv", index=False)
    sens.to_csv(OUT / "spread_sensitivity.csv", index=False)
    for (p, c), r in results.items():  # the app re-prices these without re-solving
        r["hourly"].round(4).to_csv(OUT / f"hourly_{p}_{c}.csv.gz")
    summary = {
        f"{p}_{c}": {k: v for k, v in r.items() if k != "hourly"} for (p, c), r in results.items()
    }
    summary["pv_only_plena"] = {k: v for k, v in pv_only.items() if k != "hourly"}
    summary["assumptions"] = {"site": asdict(Site()), "costs": asdict(costs), "market": asdict(mk), "battery": asdict(bat)}
    (OUT / "summary_hybrid.json").write_text(json.dumps(summary, indent=2, default=str))
    print(capex.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
