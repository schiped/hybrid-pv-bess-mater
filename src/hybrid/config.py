"""Default assumptions for a Cuyo PV + BESS hybrid selling under MATER. All editable."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Site:
    name: str = "San Juan (Cuyo), single-axis tracker"
    poi_mw: float = 100.0  # export capacity granted with dispatch priority
    priority: str = "plena"  # "plena" or "referencial_a"
    ref_a_curtailment: float = 0.08  # energy a PV-only plant loses under Referencial A
    congestion_quantile: float = 0.55  # congestion hits hours above this solar output quantile


@dataclass
class Costs:
    pv_usd_per_wp: float = 0.48  # modules, trackers, BOS, DC side
    inverter_usd_per_wac: float = 0.05
    bess_usd_per_kwh: float = 150.0  # DC block + BOS
    pcs_usd_per_kw: float = 60.0  # AC-coupled: own power conversion
    dcdc_usd_per_kw: float = 40.0  # DC-coupled: DC/DC converter, shares the PV inverter
    pv_om_usd_per_kwp_year: float = 10.0
    bess_om_share: float = 0.02
    wacc: float = 0.10
    life_years: int = 25
    max_dc_ac: float = 1.6


@dataclass
class Market:
    ppa_usd_mwh: float = 60.0  # MATER PPA with a GUMA/GUDI, settled as a contract for differences
    ppa_share: float = 0.70  # share of the reference solar profile covered by the PPA
    spot_share_of_cmg: float = 1.0  # FSA: 1 = full marginal price for new generation
    # Long-run monthly CMg, about 72 USD/MWh on average with a winter premium. The 2026
    # levels (85-165) carry the Q2-2026 fuel price spike and would overstate a 25-year value.
    monthly_cmg_usd: dict[int, float] = field(
        default_factory=lambda: {
            1: 65, 2: 65, 3: 65, 4: 70, 5: 80, 6: 95,
            7: 95, 8: 85, 9: 70, 10: 60, 11: 60, 12: 65,
        }
    )
    elasticity: float = 3.0


@dataclass
class Battery:
    eta_charge: float = 0.95
    eta_discharge: float = 0.95
    min_hours: float = 1.0
    max_hours: float = 6.0
