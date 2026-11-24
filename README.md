# ENSIM Trade Center — trader `lodesthard`

Trader side of TP2 (ENSIM Trade Center). The organizer's market reads
[`decision.json`](decision.json) every tick:

```
https://raw.githubusercontent.com/Lodesthard/ensim-trader-lodesthard/main/decision.json
```

Each value is the demand for one product in `[-1, +1]`: positive buys
`100 × value` units, negative sells `100 × |value|`, `0` holds.

Status: registered with all-zero demand (no price history yet). The signal
(`scripts/algorithm.py`), its schedule and the dashboard come once the
organizer provides `MARKET_URL` and `HOLDINGS_URL`.
