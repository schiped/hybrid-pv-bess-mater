"""Hybrid PV + BESS explorer.  streamlit run app.py

Sizes and hourly dispatch come from run_hybrid.py. The app re-prices them live
(PPA terms, capex, WACC) with dispatch held fixed; re-run the script for re-optimised sizes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import streamlit as st

from hybrid.model import annuity

ROOT = Path(__file__).parent / "results"
summary = json.loads((ROOT / "summary_hybrid.json").read_text())
risks = pd.read_csv(ROOT / "risk_register.csv")

st.set_page_config(page_title="Hybrid PV + BESS, MATER", layout="wide")
st.title("PV + BESS hybrid in San Juan under MATER")

with st.sidebar:
    priority = st.radio("Dispatch priority", ["plena", "referencial_a"], format_func=lambda x: "Plena" if x == "plena" else "Referencial A")
    coupling = st.radio("Coupling", ["dc", "ac"], format_func=str.upper)
    st.header("Offtake")
    ppa = st.slider("PPA price, USD/MWh", 40.0, 90.0, 60.0, 1.0)
    share = st.slider("Share of reference solar profile under PPA (CfD)", 0.0, 1.0, 0.70, 0.05)
    spot_k = st.slider("Spot price vs base", 0.5, 1.5, 1.0, 0.05)
    st.header("Cost")
    capex_k = st.slider("Capex vs base", 0.7, 1.3, 1.0, 0.05)
    wacc = st.slider("WACC", 0.06, 0.16, 0.10, 0.005)

key = f"{priority}_{coupling}"
base = summary[key]
h = pd.read_csv(ROOT / f"hourly_{key}.csv.gz", index_col=0, parse_dates=True)
spot = h.spot * spot_k
ref = (h.pv_avail / base["sizes"]["pv"] * 1.3).clip(upper=1.0) * summary["assumptions"]["site"]["poi_mw"]
revenue = float((spot * h.export).sum() + (share * ref * (ppa - spot)).sum())  # spot sales + PPA as a CfD
capex = base["capex_musd"] * 1e6 * capex_k
om = base["om_musd_year"] * 1e6
af = annuity(wacc, summary["assumptions"]["costs"]["life_years"])
npv = af * (revenue - om) - capex
energy = float(h.export.sum())

s = base["sizes"]
c = st.columns(5)
c[0].metric("NPV", f"{npv/1e6:.1f} MUSD")
c[1].metric("PV", f"{s['pv']:.0f} MWp")
c[2].metric("BESS", f"{s['pb']:.0f} MW / {s['eb']:.0f} MWh")
c[3].metric("Export", f"{energy/1e3:.0f} GWh/yr")
c[4].metric("LCOE", f"{(capex / af + om) / energy:.1f} USD/MWh")

st.subheader("A week of dispatch")
week = st.slider("Week of the year", 1, 52, 28)
w = h.iloc[(week - 1) * 168 : week * 168]
st.line_chart(w[["pv_avail", "export", "discharge", "charge"]].rename(columns={
    "pv_avail": "PV available (DC)", "export": "Export at POI", "discharge": "Battery discharge", "charge": "Battery charge"}))

left, right = st.columns(2)
with left:
    st.subheader("Scenarios as solved")
    rows = [{"Scenario": k.replace("_", " "), "NPV MUSD": v["npv_musd"], "PV MWp": v["sizes"]["pv"], "BESS MWh": v["sizes"]["eb"],
             "Spilled %": 100 * v["spilled_share"]} for k, v in summary.items() if k != "assumptions"]
    st.dataframe(pd.DataFrame(rows).round(1), hide_index=True)
with right:
    st.subheader("Construction schedule")
    st.image(str(ROOT / "fig4_schedule.png"))

st.subheader("Risk register")
st.dataframe(risks, hide_index=True)
st.caption("Dispatch fixed at the optimum for the base inputs; prices are a merit-order proxy from CAMMESA demand.")
