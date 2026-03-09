"""
hydro_lib.py — Bibliothèque haut niveau pour l'optimisation hydraulique court-terme
Auteur : Tawous AMARA — Projet démo EDF R&D R37

Note sur les unités :
  - Volumes en Mm³ (millions de m³)
  - Puissance en MW, énergie en MWh
  - efficiency en MWh/Mm³  (ex: 1000 MWh/Mm³ = 1 kWh/m³, typique hydraulique)
  - Apports en Mm³/h (ex: 0.1 Mm³/h = 100 000 m³/h, rivière alpine moyenne)
"""

from __future__ import annotations
import numpy as np
from solver import HydroOptimizer, ReservoirParams, MarketData, SolverResult


PLANT_PROFILES = {
    "grand_lac": ReservoirParams(
        volume_min=50.0,       # Mm³
        volume_max=500.0,      # Mm³
        volume_initial=300.0,  # Mm³
        power_min=10.0,        # MW
        power_max=150.0,       # MW
        ramp_max=40.0,         # MW/h
        efficiency=1000.0,     # MWh/Mm³  (1 kWh/m³ typique)
        daily_commitment=600.0, # MWh/jour
    ),
    "petite_chute": ReservoirParams(
        volume_min=2.0,
        volume_max=30.0,
        volume_initial=20.0,
        power_min=1.0,
        power_max=20.0,
        ramp_max=5.0,
        efficiency=800.0,
        daily_commitment=80.0,
    ),
    "alpes_haute_chute": ReservoirParams(
        volume_min=100.0,
        volume_max=2000.0,
        volume_initial=1200.0,
        power_min=20.0,
        power_max=400.0,
        ramp_max=100.0,
        efficiency=1200.0,
        daily_commitment=2000.0,
    ),
}


class MarketDataGenerator:
    """Génère des données de marché simulées réalistes (profil EPEX Spot)."""

    @staticmethod
    def generate_spot_prices(horizon_hours=168, base_price=70.0, seed=42) -> np.ndarray:
        """
        Prix spot horaires avec :
        - Double pic journalier (matin 8-10h, soir 19-21h)
        - Creux nocturne (0-5h) et déjeuner (12-14h)
        - Effet weekend (-20%)
        - Bruit gaussien σ=8 €/MWh
        """
        rng = np.random.default_rng(seed)
        prices = np.zeros(horizon_hours)
        for t in range(horizon_hours):
            h = t % 24
            d = t // 24
            if 8 <= h <= 10:    f = 1.35
            elif 19 <= h <= 21: f = 1.45
            elif 0 <= h <= 5:   f = 0.65
            elif 12 <= h <= 14: f = 0.90
            else:                f = 1.0
            if d % 7 >= 5: f *= 0.80
            prices[t] = max(5.0, base_price * f + rng.normal(0, 8))
        return prices

    @staticmethod
    def generate_inflows(horizon_hours=168, mean_inflow=0.12, seed=42) -> np.ndarray:
        """
        Apports naturels en Mm³/h avec autocorrélation AR(1).
        mean_inflow=0.12 Mm³/h = 120 000 m³/h (rivière alpine courante)
        """
        rng = np.random.default_rng(seed)
        noise = rng.normal(0, mean_inflow * 0.15, horizon_hours)
        inflows = np.zeros(horizon_hours)
        inflows[0] = mean_inflow
        alpha = 0.7
        for t in range(1, horizon_hours):
            inflows[t] = alpha * inflows[t-1] + (1-alpha)*mean_inflow + noise[t]
        return np.maximum(0.0, inflows)

    @staticmethod
    def build_market(horizon_hours=168, base_price=70.0, mean_inflow=0.12, seed=42) -> MarketData:
        return MarketData(
            spot_prices=MarketDataGenerator.generate_spot_prices(horizon_hours, base_price, seed),
            inflows=MarketDataGenerator.generate_inflows(horizon_hours, mean_inflow, seed),
        )


class HydroPortfolio:
    def __init__(self, params: ReservoirParams, dt: float = 1.0):
        self._optimizer = HydroOptimizer(params, dt)
        self._params = params

    @classmethod
    def from_profile(cls, name: str, dt: float = 1.0) -> "HydroPortfolio":
        if name not in PLANT_PROFILES:
            raise ValueError(f"Profil inconnu: {name}. Disponibles: {list(PLANT_PROFILES)}")
        return cls(PLANT_PROFILES[name], dt)

    def optimize(self, market: MarketData) -> SolverResult:
        return self._optimizer.solve(market)

    @staticmethod
    def summary(result: SolverResult) -> str:
        lines = [
            "=" * 55,
            "  RÉSULTATS OPTIMISATION HYDRAULIQUE",
            "=" * 55,
            f"  Statut solveur        : {result.status}",
            f"  Revenu optimal        : {result.total_revenue:>12,.0f} €",
            f"  Revenu stratégie naïve: {result.baseline_revenue:>12,.0f} €",
            f"  Gain vs naïf          : {result.gain_vs_baseline_pct:>+11.1f} %",
            f"  Production moyenne    : {result.production.mean():>10.1f} MW",
            f"  Production max        : {result.production.max():>10.1f} MW",
            f"  Volume final          : {result.volumes[-1]:>10.1f} Mm³",
            "=" * 55,
        ]
        return "\n".join(lines)

    def get_daily_stats(self, result: SolverResult) -> list:
        T = len(result.production)
        return [
            {
                "day": d + 1,
                "energy_mwh": round(float(result.production[d*24:(d+1)*24].sum()), 1),
                "avg_power_mw": round(float(result.production[d*24:(d+1)*24].mean()), 1),
                "peak_power_mw": round(float(result.production[d*24:(d+1)*24].max()), 1),
            }
            for d in range(T // 24)
        ]
