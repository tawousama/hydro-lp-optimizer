"""
solver.py — Optimiseur de programme de production hydraulique court-terme
Résolution par programmation linéaire (LP) via scipy.optimize.linprog.

Auteur : Tawous AMARA — Projet démo EDF R&D R37

Variables x = [P_0..P_{T-1}, V_1..V_T]  (longueur 2T)

Formulation :
    max  Σ_t  prix(t) · P(t) · dt
    s.t.
        V(t+1) = V(t) + inflow(t)·dt - P(t)·dt/η    [bilan hydraulique]
        P_min ≤ P(t) ≤ P_max
        V_min ≤ V(t) ≤ V_max
        |P(t+1)-P(t)| ≤ ramp_max                     [rampe de puissance]
        Σ_{t∈jour} P(t)·dt ≥ daily_commitment          [engagement marché]
        V(T) ≥ V_initial                               [durabilité du stock]
"""

from dataclasses import dataclass
import numpy as np
from scipy.optimize import linprog


@dataclass
class ReservoirParams:
    """Paramètres physiques du réservoir et de la turbine."""
    volume_min: float          # Mm³
    volume_max: float          # Mm³
    volume_initial: float      # Mm³
    power_min: float           # MW
    power_max: float           # MW
    ramp_max: float            # MW/h
    efficiency: float          # MWh / Mm³
    daily_commitment: float    # MWh/jour


@dataclass
class MarketData:
    """Prix spot horaires et apports hydrauliques naturels."""
    spot_prices: np.ndarray    # €/MWh, shape (T,)
    inflows: np.ndarray        # Mm³/h, shape (T,)


@dataclass
class SolverResult:
    """Résultats de l'optimisation."""
    status: str
    total_revenue: float
    production: np.ndarray
    volumes: np.ndarray
    timestamps: list
    baseline_revenue: float = 0.0
    gain_vs_baseline_pct: float = 0.0


class HydroOptimizer:
    """
    Optimise le programme de production hydraulique sur T pas de temps.
    La contrainte de durabilité (V_T ≥ V_initial) force le solveur à ne consommer
    que les apports naturels, rendant la décision de timing réellement utile.
    """

    def __init__(self, params: ReservoirParams, dt: float = 1.0):
        self.p = params
        self.dt = dt

    def solve(self, market: MarketData) -> SolverResult:
        T = len(market.spot_prices)
        p, dt = self.p, self.dt
        n = 2 * T  # x = [P_0..P_{T-1}, V_1..V_T]

        # ── Objectif ─────────────────────────────────────────────────────────
        c = np.zeros(n)
        c[:T] = -market.spot_prices * dt  # minimiser -revenu

        # ── Bornes ────────────────────────────────────────────────────────────
        bounds = [(p.power_min, p.power_max)] * T + [(p.volume_min, p.volume_max)] * T

        # ── Contraintes d'égalité : bilan hydraulique ─────────────────────────
        # t=0 : P[0]·dt/η + V[1] = V_initial + inflow[0]·dt
        # t>0 : P[t]·dt/η - V[t] + V[t+1] = inflow[t]·dt
        A_eq = np.zeros((T, n))
        b_eq = np.zeros(T)

        for t in range(T):
            A_eq[t, t] = dt / p.efficiency     # P[t]·dt/η
            A_eq[t, T + t] = 1.0              # +V[t+1]
            if t == 0:
                b_eq[t] = p.volume_initial + market.inflows[t] * dt
            else:
                A_eq[t, T + t - 1] = -1.0     # -V[t]
                b_eq[t] = market.inflows[t] * dt

        # ── Contraintes d'inégalité ────────────────────────────────────────────
        rows, rhs = [], []

        # Rampe montante : P[t+1] - P[t] ≤ ramp_max
        for t in range(T - 1):
            r = np.zeros(n); r[t+1] = 1.0; r[t] = -1.0
            rows.append(r); rhs.append(p.ramp_max)

        # Rampe descendante : P[t] - P[t+1] ≤ ramp_max
        for t in range(T - 1):
            r = np.zeros(n); r[t] = 1.0; r[t+1] = -1.0
            rows.append(r); rhs.append(p.ramp_max)

        # Engagement journalier : -Σ P[t]·dt ≤ -commitment
        spd = int(24 / dt)
        for d in range(T // spd):
            r = np.zeros(n)
            for t in range(d * spd, (d + 1) * spd):
                r[t] = -dt
            rows.append(r); rhs.append(-p.daily_commitment)

        # Durabilité du stock : -V[T] ≤ -V_initial  (i.e. V[T] ≥ V_initial)
        r = np.zeros(n)
        r[2*T - 1] = -1.0   # -V[T] ≤ -V_initial
        rows.append(r); rhs.append(-p.volume_initial)

        A_ub = np.array(rows)
        b_ub = np.array(rhs)

        # ── Résolution ────────────────────────────────────────────────────────
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                      bounds=bounds, method="highs")

        if res.success:
            status = "Optimal"
            production = np.clip(res.x[:T], p.power_min, p.power_max)
            volumes = np.concatenate([[p.volume_initial], res.x[T:2*T]])
            total_revenue = float(-res.fun)
        else:
            status = res.message
            production = np.zeros(T)
            volumes = np.full(T + 1, p.volume_initial)
            total_revenue = 0.0

        # ── Stratégie naïve (production constante à max disponible) ──────────
        # Naïf : distribue le même budget eau uniformément sur l'horizon
        total_energy_budget = (
            sum(market.inflows) * dt * p.efficiency  # énergie des apports
        )
        naive_constant = min(p.power_max, total_energy_budget / (T * dt))
        naive_prod = np.full(T, naive_constant)
        baseline_revenue = float(np.sum(market.spot_prices * naive_prod * dt))
        gain_pct = (
            (total_revenue - baseline_revenue) / abs(baseline_revenue) * 100
            if baseline_revenue != 0 else 0.0
        )

        return SolverResult(
            status=status,
            total_revenue=total_revenue,
            production=production,
            volumes=volumes,
            timestamps=[f"H{t:03d}" for t in range(T)],
            baseline_revenue=baseline_revenue,
            gain_vs_baseline_pct=gain_pct,
        )