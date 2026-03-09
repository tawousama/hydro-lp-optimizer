"""
api.py — API REST FastAPI exposant l'optimiseur hydraulique court-terme
Endpoints pour lancer une optimisation, consulter les profils et les résultats.

Auteur : Tawous AMARA — Projet démo EDF R&D R37

Démarrage :
    uvicorn api:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import numpy as np
from typing import Optional

from solver import ReservoirParams, MarketData
from hydro_lib import HydroPortfolio, MarketDataGenerator, PLANT_PROFILES

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="HydroOptimizer API",
    description=(
        "API d'optimisation de programme de production hydraulique court-terme. "
        "Basée sur une formulation MIP (PuLP/CBC) avec contraintes physiques "
        "de réservoir, rampe de puissance et engagement marché."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schémas Pydantic
# ---------------------------------------------------------------------------

class OptimizeRequest(BaseModel):
    """Corps de la requête d'optimisation."""
    profile: str = Field(
        default="grand_lac",
        description="Profil de centrale prédéfini : grand_lac | petite_chute | alpes_haute_chute",
    )
    horizon_hours: int = Field(
        default=168,
        ge=24,
        le=336,
        description="Horizon d'optimisation en heures (24h à 336h / 14 jours)",
    )
    base_price: float = Field(
        default=70.0,
        ge=0.0,
        description="Prix spot de base en €/MWh pour la simulation de marché",
    )
    mean_inflow: float = Field(
        default=0.8,
        ge=0.0,
        description="Apport hydraulique moyen en Mm³/h",
    )
    seed: int = Field(
        default=42,
        description="Graine aléatoire pour la reproductibilité des données simulées",
    )
    # Optionnel : surcharge des paramètres physiques
    custom_params: Optional[dict] = Field(
        default=None,
        description="Surcharge des paramètres physiques (optionnel). Clés : power_max, volume_max, etc.",
    )


class HourlyPoint(BaseModel):
    hour: int
    production_mw: float
    volume_mm3: float
    spot_price_eur_mwh: float


class DailyStats(BaseModel):
    day: int
    energy_mwh: float
    avg_power_mw: float
    peak_power_mw: float


class OptimizeResponse(BaseModel):
    """Résultats complets de l'optimisation."""
    status: str
    total_revenue_eur: float
    baseline_revenue_eur: float
    gain_vs_baseline_pct: float
    hourly: list[HourlyPoint]
    daily_stats: list[DailyStats]
    profile_used: str
    horizon_hours: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", summary="Sanity check")
def root():
    return {"message": "HydroOptimizer API opérationnelle", "version": "1.0.0"}


@app.get("/profiles", summary="Liste des profils de centrales disponibles")
def list_profiles():
    """Retourne les profils de centrales hydrauliques prédéfinis avec leurs paramètres."""
    result = {}
    for name, p in PLANT_PROFILES.items():
        result[name] = {
            "power_min_mw": p.power_min,
            "power_max_mw": p.power_max,
            "volume_min_mm3": p.volume_min,
            "volume_max_mm3": p.volume_max,
            "volume_initial_mm3": p.volume_initial,
            "ramp_max_mw_per_h": p.ramp_max,
            "efficiency_mwh_per_mm3": p.efficiency,
            "daily_commitment_mwh": p.daily_commitment,
        }
    return result


@app.post("/optimize", response_model=OptimizeResponse, summary="Lance une optimisation")
def optimize(req: OptimizeRequest):
    """
    Lance l'optimisation du programme de production hydraulique.

    - Génère des données de marché simulées (prix spot + apports)
    - Résout le problème MIP avec PuLP/CBC
    - Retourne le programme horaire optimal et les métriques de performance
    """
    # Validation du profil
    if req.profile not in PLANT_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Profil inconnu: '{req.profile}'. Disponibles: {list(PLANT_PROFILES.keys())}",
        )

    # Construction du portfolio
    portfolio = HydroPortfolio.from_profile(req.profile)

    # Surcharge des paramètres si fournie
    if req.custom_params:
        base_params = PLANT_PROFILES[req.profile]
        updated = {
            "volume_min": base_params.volume_min,
            "volume_max": base_params.volume_max,
            "volume_initial": base_params.volume_initial,
            "power_min": base_params.power_min,
            "power_max": base_params.power_max,
            "ramp_max": base_params.ramp_max,
            "efficiency": base_params.efficiency,
            "daily_commitment": base_params.daily_commitment,
            **req.custom_params,
        }
        try:
            custom = ReservoirParams(**updated)
            portfolio = HydroPortfolio(custom)
        except TypeError as e:
            raise HTTPException(status_code=400, detail=f"Paramètres invalides: {e}")

    # Génération des données de marché
    market = MarketDataGenerator.build_market(
        horizon_hours=req.horizon_hours,
        base_price=req.base_price,
        mean_inflow=req.mean_inflow,
        seed=req.seed,
    )

    # Optimisation
    result = portfolio.optimize(market)

    if result.status not in ("Optimal", "Not Solved"):
        raise HTTPException(
            status_code=422,
            detail=f"Le solveur n'a pas trouvé de solution: {result.status}",
        )

    # Construction de la réponse horaire
    hourly = [
        HourlyPoint(
            hour=t,
            production_mw=round(float(result.production[t]), 2),
            volume_mm3=round(float(result.volumes[t]), 3),
            spot_price_eur_mwh=round(float(market.spot_prices[t]), 2),
        )
        for t in range(req.horizon_hours)
    ]

    daily_raw = portfolio.get_daily_stats(result)
    daily_stats = [DailyStats(**d) for d in daily_raw]

    return OptimizeResponse(
        status=result.status,
        total_revenue_eur=round(result.total_revenue, 2),
        baseline_revenue_eur=round(result.baseline_revenue, 2),
        gain_vs_baseline_pct=round(result.gain_vs_baseline_pct, 2),
        hourly=hourly,
        daily_stats=daily_stats,
        profile_used=req.profile,
        horizon_hours=req.horizon_hours,
    )


@app.get("/simulate-market", summary="Prévisualise les données de marché simulées")
def simulate_market(
    horizon_hours: int = 168,
    base_price: float = 70.0,
    mean_inflow: float = 0.8,
    seed: int = 42,
):
    """Génère et retourne des données de marché simulées sans lancer l'optimisation."""
    market = MarketDataGenerator.build_market(horizon_hours, base_price, mean_inflow, seed)
    return {
        "horizon_hours": horizon_hours,
        "price_stats": {
            "min": round(float(market.spot_prices.min()), 2),
            "max": round(float(market.spot_prices.max()), 2),
            "mean": round(float(market.spot_prices.mean()), 2),
        },
        "inflow_stats": {
            "min": round(float(market.inflows.min()), 3),
            "max": round(float(market.inflows.max()), 3),
            "mean": round(float(market.inflows.mean()), 3),
        },
        "spot_prices": [round(float(p), 2) for p in market.spot_prices],
        "inflows": [round(float(i), 3) for i in market.inflows],
    }
