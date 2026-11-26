"""Build docs/data/dashboard_data.js for docs/index.html.

Everything the dashboard shows that is not a live feed comes from here, computed
with the same code as the trader (signals.py) and the backtest (backtest.py):
signal index per product, indicators, simulated trades and portfolio, risk
series, backtest tables. The file is a JS bundle (window.DASH = {...}) so the
page also works when opened from disk (file://), not only on GitHub Pages.

Usage: python scripts/build_dashboard.py [market.json]   (default: live URL)
"""
import csv
import json
import math
import os
import random
import sys
import time

from adaptive import adaptive_series, describe, hit_rates, dead_zones
from backtest import adaptive_test, endgame_test, load, simulate
from endgame import (END_UTC, ENDGAME_TICKS, PARK_TICKS, PERIODS, TICK_UNCERTAINTY,
                     estimate_remaining_ticks, fit_model, forecast, predict)
from signals import (DEAD_ZONE, FAMILIES, METHODS, MIN_HISTORY, PRODUCT_CONFIG,
                     product_index, to_order)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "dashboard_data.js")
LOG = os.path.join(ROOT, "docs", "data", "log.csv")
DEAD_ZONES = [round(0.05 * i, 2) for i in range(0, 19)]

LABELS = {
    "mr_bollinger": "Bollinger (z-score)",
    "mr_ema": "Écart au lissage exponentiel",
    "mr_long_mean": "Écart à la moyenne historique",
    "tf_momentum": "Momentum 24 ticks",
    "tf_ma_cross": "Croisement MM 6/24",
    "tf_slope": "Pente de régression 48",
}
FAMILY_OF = {m: f for f, ws in FAMILIES.items() for m in ws}
WHY = {
    "A": "Niveau stable (moyenne 5,01 → 4,99) et cycle net de 48 ticks : on achète le creux, "
         "on vend le sommet. Fenêtres = un cycle complet.",
    "B": "Hausse continue sans cycle exploitable. On détient déjà 1000 unités et 1000 € n'en "
         "achètent qu'environ 80 de plus : toute vente fait rater la hausse. Aucune méthode retenue.",
    "C": "Cycle très net de 24 ticks sur un niveau qui baisse (2,52 → 1,55) : retour à la moyenne "
         "sur des fenêtres d'un cycle C, moyenne historique exclue (niveau instable).",
}


def r(x, d=4):
    return None if x is None else round(x, d)


def std(xs):
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def cycle_period(x):
    n = len(x)
    d = [x[t] / (sum(x[t - 24:t + 24]) / 48) - 1 for t in range(24, n - 24)]
    md = sum(d) / len(d)
    var = sum((a - md) ** 2 for a in d)
    ac = {lag: sum((d[i] - md) * (d[i + lag] - md) for i in range(len(d) - lag)) / var
          for lag in range(1, 80)}
    best = max((lag for lag in ac if lag >= 8), key=ac.get)
    return {"peak": best, "ac_peak": r(ac[best], 2), "ac_half": r(ac[best // 2], 2),
            "is_cycle": ac[best] > 0.5 and ac[best // 2] < -0.5}


def fr(x, d=2):
    return f"{x:+.{d}f}".replace(".", ",").replace("-", "−")


def reason(p, s, contribs, units, order, dz):
    side = "vente" if units < 0 else "achat"
    where = "au-dessus" if s < 0 else "en dessous"
    parts = ", ".join(f"{LABELS[m].split(' (')[0]} {fr(v)}" for m, v, _ in contribs)
    seuil = f"{dz['sell' if s < 0 else 'buy']:.2f}".replace(".", ",")
    txt = f"S = {fr(s)} (|S| ≥ seuil {seuil}) : prix {where} de son niveau normal [{parts}] → {side} {abs(units):.0f} u."
    if abs(units) + 0.5 < 100 * abs(order):
        txt += " Quantité réduite (argent ou stock insuffisant)."
    return txt


def main():
    market = load(sys.argv[1] if len(sys.argv) > 1 else None)
    products = market["products"]
    hist = market["history"]
    n = len(hist)
    prices = {p: [row[p] for row in hist] for p in products}
    first = MIN_HISTORY
    mid = (first + n - 1) // 2
    segs = {"H1": (first, mid), "H2": (mid, n - 1)}

    # ---------------------------------------------------------------- signals
    sig = {p: {m: [0.0] * n for m in METHODS} for p in products}
    for p in products:
        for t in range(first, n):
            h = prices[p][: t + 1]
            for m, f in METHODS.items():
                sig[p][m][t] = f(h)

    cfg_sig = {}  # signals of the configured methods (custom windows)
    S, orders, zones = {}, {}, {}
    for p in products:
        cfg_sig[p] = {}
        for m, kw, w in PRODUCT_CONFIG.get(p, []):
            cfg_sig[p][m] = [METHODS[m](prices[p][: t + 1], **kw) if t >= first else 0.0
                             for t in range(n)]
        S[p] = [product_index(prices[p][: t + 1], p) if t >= first else 0.0 for t in range(n)]
        orders[p], zones[p] = adaptive_series(prices[p], S[p], p, first)

    # ------------------------------------------------- simulated live strategy
    track = []
    simulate(prices, orders, first, n - 1, products, track=track)
    strat, hold, trades = [], [], []
    for st in track:
        i = st["i"]
        strat.append(st["money"] + sum(st["stock"][p] * prices[p][i] for p in products))
        hold.append(1000.0 + sum(1000.0 * prices[p][i] for p in products))
        for p, u in st["fills"].items():
            k = i - 1  # decision index
            contribs = [(m, cfg_sig[p][m][k], w) for m, _, w in PRODUCT_CONFIG[p] if w > 0]
            trades.append({"tick": hist[i]["t"], "ts": hist[i]["timestamp"], "p": p,
                           "units": r(u, 1), "price": r(prices[p][i]), "S": r(S[p][k], 3),
                           "order": r(orders[p][k], 3),
                           "reason": reason(p, S[p][k], contribs, u, orders[p][k], zones[p][k])})
    sim_ticks = [hist[st["i"]]["t"] for st in track]

    def drawdown(v):
        peak, out = -1e18, []
        for x in v:
            peak = max(peak, x)
            out.append(100 * (x / peak - 1))
        return out

    # ------------------------------------------------------------ indicators
    indicators, vol = {}, {}
    for p in products:
        nb = next((kw.get("n", 48) for m, kw, w in PRODUCT_CONFIG.get(p, [])
                   if m == "mr_bollinger"), 48)
        ma, up, lo = [], [], []
        for t in range(n):
            if t + 1 < nb:
                ma.append(None), up.append(None), lo.append(None)
                continue
            w = prices[p][t + 1 - nb: t + 1]
            m_, sd = sum(w) / nb, std(w)
            ma.append(r(m_)), up.append(r(m_ + 2 * sd)), lo.append(r(m_ - 2 * sd))
        indicators[p] = {"window": nb, "ma": ma, "upper": up, "lower": lo}
        rets = [None] + [math.log(prices[p][t] / prices[p][t - 1]) for t in range(1, n)]
        vol[p] = [None if t < 24 else r(100 * std(rets[t - 23: t + 1]), 3) for t in range(n)]

    # ------------------------------------------------------- backtest tables
    zero = {p: [0.0] * n for p in products}

    def solo(p, series, seg):
        o = dict(zero)
        o[p] = series
        ex, tr, _ = simulate(prices, o, *segs[seg], products)
        return {"excess": r(ex[p], 1), "trades": tr[p]}

    methods_table = []
    for m in METHODS:
        row = {"method": m, "label": LABELS[m], "family": FAMILY_OF[m]}
        for p in products:
            series = [to_order(s, 0) for s in sig[p][m]]
            row[p] = {seg: solo(p, series, seg) for seg in segs}
        methods_table.append(row)

    families_table = []
    for fam, ws in FAMILIES.items():
        row = {"family": fam}
        for p in products:
            idx = [sum(w * sig[p][m][t] for m, w in ws.items()) / sum(ws.values())
                   for t in range(n)]
            row[p] = {seg: solo(p, [to_order(i, DEAD_ZONE) for i in idx], seg) for seg in segs}
        families_table.append(row)

    sweep = []
    for dz in DEAD_ZONES:
        o = {p: [to_order(s, dz) for s in S[p]] for p in products}
        row = {"dz": dz}
        for seg in segs:
            ex, tr, hv = simulate(prices, o, *segs[seg], products)
            row[seg] = {"excess": r(sum(ex.values()), 1), "trades": sum(tr.values()),
                        "pct": r(100 * sum(ex.values()) / hv, 2),
                        "by": {p: r(ex[p], 1) for p in products}}
        sweep.append(row)

    rng = random.Random(0)
    rand = {}
    for seg in segs:
        tot = {p: 0.0 for p in products}
        for _ in range(300):
            o = {p: [rng.uniform(-1, 1) for _ in range(n)] for p in products}
            ex, _, _ = simulate(prices, o, *segs[seg], products)
            for p in products:
                tot[p] += ex[p] / 300
        rand[seg] = {p: r(tot[p], 1) for p in products}

    # -------------------------------------------------------------- endgame
    remaining, pace_h, hours_left = estimate_remaining_ticks([row["timestamp"] for row in hist])
    ticks = [row["t"] for row in hist]
    eg_products = {}
    for p in products:
        fc = forecast(p, ticks, prices[p], remaining)
        entry = {"forecast": None, "curve": []}
        if fc:
            coef, _ = fit_model(ticks, prices[p], PERIODS[p])
            entry["forecast"] = {k: r(v, 4) for k, v in fc.items()}
            entry["curve"] = [[t, r(predict(coef, t, PERIODS[p]))]
                              for t in range(ticks[-1] - 24, fc["t_end"] + TICK_UNCERTAINTY + 1)]
        eg_products[p] = entry
    eg_rows = endgame_test(prices, ticks, products, S, first)
    endgame = {
        "end_utc": END_UTC.isoformat(), "remaining_ticks": r(remaining, 1),
        "hours_per_tick": r(pace_h, 2), "hours_left": r(hours_left, 1),
        "endgame_ticks": ENDGAME_TICKS, "park_ticks": PARK_TICKS, "products": eg_products,
        "test": [{"end_tick": row["end_tick"],
                  **{f"err{e}": {p: r(v, 1) for p, v in row[e].items()} for e in (0, -4, 4)}}
                 for row in eg_rows],
    }

    # --------------------------------------------------------------- real log
    log = []
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8", newline="") as f:
            log = list(csv.DictReader(f))

    last = n - 1
    current = {}
    for p in products:
        contribs = [{"method": m, "label": LABELS[m], "weight": w,
                     "params": kw, "value": r(cfg_sig[p][m][last], 3)}
                    for m, kw, w in PRODUCT_CONFIG.get(p, [])]
        rates = hit_rates(prices[p], S[p], p)
        dz = dead_zones(rates)
        current[p] = {"S": r(S[p][last], 3), "order": r(orders[p][last], 3),
                      "dead_zone": {k: r(v, 3) for k, v in dz.items()},
                      "hit_rates": {k: list(v) for k, v in rates.items()},
                      "adaptive": describe(rates, dz) if PRODUCT_CONFIG.get(p) else "",
                      "methods": contribs, "why": WHY.get(p, ""),
                      "family": "mean_reversion" if PRODUCT_CONFIG.get(p) else "none",
                      "cycle": cycle_period(prices[p])}

    data = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "market_generated_at": market.get("generated_at"),
        "products": products,
        "dead_zone": DEAD_ZONE,
        "halves": {"H1": [hist[first]["t"], hist[mid]["t"]], "H2": [hist[mid]["t"], hist[-1]["t"]]},
        "ticks": [row["t"] for row in hist],
        "timestamps": [row["timestamp"] for row in hist],
        "prices": {p: [r(x) for x in prices[p]] for p in products},
        "S": {p: [r(x, 3) for x in S[p]] for p in products},
        "indicators": indicators,
        "vol24": vol,
        "sim": {"ticks": sim_ticks, "strategy": [r(x, 2) for x in strat],
                "hold": [r(x, 2) for x in hold],
                "dd_strategy": [r(x, 3) for x in drawdown(strat)],
                "dd_hold": [r(x, 3) for x in drawdown(hold)], "trades": trades},
        "current": current,
        "endgame": endgame,
        "dead_zones": {p: [None if z is None else [r(z["sell"], 3), r(z["buy"], 3)] for z in zones[p]]
                       for p in products},
        "backtest": {"adaptive": {k: {kk: r(vv, 1) for kk, vv in v.items()}
                                  for k, v in adaptive_test(prices, products, S, first, mid, n).items()},
                     "methods": methods_table, "families": families_table,
                     "sweep": sweep, "random": rand},
        "log": log,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("// Generated by scripts/build_dashboard.py -- do not edit by hand.\n")
        f.write("window.DASH = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print(f"Wrote {OUT} ({os.path.getsize(OUT) // 1024} KB, {len(trades)} simulated trades, "
          f"{len(log)} log rows)")


if __name__ == "__main__":
    main()
