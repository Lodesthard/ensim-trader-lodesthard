# ENSIM Trade Center — trader `lodesthard`

Trader side of TP2 (ENSIM Trade Center). The organizer's market reads
[`decision.json`](decision.json) every tick:

```
https://raw.githubusercontent.com/Lodesthard/ensim-trader-lodesthard/main/decision.json
```

Each value is the demand for one product in `[-1, +1]`: positive buys
`100 × value` units, negative sells `100 × |value|`, `0` holds.

## How it decides

Every 30 minutes `.github/workflows/auto_decide.yml` runs `scripts/algorithm.py`:

1. **Signal** (`scripts/signals.py`): per product, weighted mean of same-family
   methods → index S in [-1, +1]. A: mean reversion on its 48-tick cycle;
   C: mean reversion on its 24-tick cycle; B: no method (slow rise, selling loses it).
2. **Dead zone**: |S| < 0.20 → hold, else order = 2 × S, clipped to ±1 (S = 0.5 → 100 units).
3. **Endgame** (`scripts/endgame.py`): in the last 8 ticks before
   1 Dec 2026 15:00 (Paris), a trend + cycle model forecasts the final price of
   A and C; sell near the remaining peak if above it, buy near the remaining
   trough if below it (5 % gap → 100 units). Idle cash goes into B during the last 6 ticks.
4. **Guards**: never sell more than held, never spend below a 50 € reserve.

Each new market tick appends one row to `docs/data/log.csv`, then
`scripts/build_dashboard.py` regenerates `docs/data/dashboard_data.js`.

## Dashboard

GitHub Pages: `docs/index.html` (performance vs baselines, risk, signal per
product, decision log, method and backtest, endgame countdown).

## Backtest

```
python scripts/backtest.py            # live history
python scripts/backtest.py market.json
```

Reproduces the organizer's execution rules (scripts/market_tick.py). Results
and limits are shown in the dashboard's "Méthode" section.
