# hydro-lp-optimizer

Short-term hydraulic production scheduling via Linear Programming (LP).  
Maximizes revenue on the EPEX spot market while respecting physical reservoir constraints.

---

## Installation

```bash
git clone https://github.com/tawousama/hydro-lp-optimizer
cd hydro-dispatch-optimizer
pip install -r requirements.txt
```

---

## Quick start

```python
from hydro_lib import HydroPortfolio, MarketDataGenerator

portfolio = HydroPortfolio.from_profile("grand_lac")
market    = MarketDataGenerator.build_market(horizon_hours=168)
result    = portfolio.optimize(market)

print(HydroPortfolio.summary(result))
```

Output:
```
=======================================================
  RÉSULTATS OPTIMISATION HYDRAULIQUE
=======================================================
  Statut solveur        : Optimal
  Revenu optimal        :      541 504 €
  Revenu stratégie naïve:      427 787 €
  Gain vs naïf          :       +26,6 %
  Production moyenne    :        38.8 MW
  Production max        :       150.0 MW
  Volume final          :       300.0 Mm³
=======================================================
```

---

## API

Start the server:

```bash
uvicorn api:app --reload --port 8000
```

Swagger UI available at `http://localhost:8000/docs`

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/optimize` | Run an optimization, returns hourly schedule |
| `GET`  | `/profiles` | List available plant profiles |
| `GET`  | `/simulate-market` | Preview simulated market data |

### Example request

```bash
curl -X POST http://localhost:8000/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "grand_lac",
    "horizon_hours": 168,
    "base_price": 70.0,
    "mean_inflow": 0.04
  }'
```

---

## Project structure

```
hydro-dispatch-optimizer/
├── solver.py        # LP core — scipy.optimize.linprog (HiGHS)
├── hydro_lib.py     # Business layer — plant profiles, market simulation
├── api.py           # FastAPI REST API
├── requirements.txt
└── README.md
```

---

## Available plant profiles

| Profile | P_max (MW) | V_max (Mm³) | Commitment (MWh/day) |
|---------|-----------|-------------|----------------------|
| `grand_lac` | 150 | 500 | 600 |
| `petite_chute` | 20 | 30 | 80 |
| `alpes_haute_chute` | 400 | 2000 | 2000 |