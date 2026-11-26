"""Backtest of the signal in signals.py on the real market history.

Reproduces the organizer's execution rules (exofri/ensim-trade-center,
scripts/market_tick.py):
- the decision written after tick t is read at tick t+1 and executed at the
  price of tick t+1;
- buy = min(100 * d, money / price) units, sell = min(100 * |d|, stock) units;
- products executed in order A, B, C against the same money; no fees.
Our own buy/sell pressure on the price is NOT modelled (see report).

Start: 1000 EUR + 1000 units of each product, like every trader.
Benchmark "hold" = never trade. Excess = strategy value - hold value at the
end of the segment = sum over trades of units * (P_end - P_exec) for buys and
units * (P_exec - P_end) for sells.

Usage: python scripts/backtest.py [market.json]   (default: live URL)
"""
import json
import random
import sys
import urllib.request

from endgame import ENDGAME_TICKS, endgame_order
from signals import DEAD_ZONE, FAMILIES, METHODS, MIN_HISTORY, product_index, to_order

MARKET_URL = ("https://raw.githubusercontent.com/exofri/ensim-trade-center/"
              "refs/heads/main/docs/data/market.json")
RESERVE = 50
DEAD_ZONES = [round(0.05 * i, 2) for i in range(0, 19)]  # 0.00 .. 0.90


def load(path=None):
    if path:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    with urllib.request.urlopen(MARKET_URL, timeout=20) as r:
        return json.load(r)


def budget_guard(d, money, price):
    if d <= 0:
        return d
    if money <= RESERVE:
        return 0.0
    afford = (money - RESERVE) / price
    return d if 100 * d <= afford else afford / 100


def simulate(prices, orders, start, end, products, track=None):
    """orders[p][t] = order decided with prices[:t+1]. Trades from start to end.
    If `track` is a list, one dict per tick is appended (state after execution at index t+1)."""
    money = 1000.0
    stock = {p: 1000.0 for p in products}
    excess = {p: 0.0 for p in products}
    trades = {p: 0 for p in products}
    flows = []  # (product, signed units, exec price)
    for t in range(start, end):
        fills = {}
        for p in products:
            d = budget_guard(orders[p][t], money, prices[p][t])
            px = prices[p][t + 1]
            if d > 0:
                u = min(100 * d, money / px)
                money -= u * px
                stock[p] += u
            elif d < 0:
                u = min(100 * -d, stock[p])
                money += u * px
                stock[p] -= u
                u = -u
            else:
                continue
            if abs(u) > 1e-9:
                trades[p] += 1
                flows.append((p, u, px))
                fills[p] = u
        if track is not None:
            track.append({"i": t + 1, "money": money, "stock": dict(stock), "fills": fills})
    for p, u, px in flows:
        excess[p] += u * (prices[p][end] - px)
    hold = 1000.0 + sum(1000.0 * prices[p][end] for p in products)
    return excess, trades, hold


def endgame_test(prices, ticks, products, final_idx, first, step=12, errors=(0, -4, 4)):
    """Pretend the game ends at index E (several E). Compare the final value of
    the normal strategy with the same strategy + endgame rule in the last ticks.
    `errors` = mistake on the number of remaining ticks (live it is estimated)."""
    base = {p: [to_order(i, DEAD_ZONE) for i in final_idx[p]] for p in products}
    n = len(ticks)
    rows = []
    for E in range(first + 4 * ENDGAME_TICKS, n, step):
        ref, _, _ = simulate(prices, base, first, E, products)
        row = {"end_tick": ticks[E]}
        for err in errors:
            orders = {p: base[p][:] for p in products}
            for t in range(E - ENDGAME_TICKS, E):
                remaining = max(1, E - t + err)
                for p in products:
                    orders[p][t], _, _ = endgame_order(p, ticks[: t + 1], prices[p][: t + 1],
                                                       base[p][t], remaining)
            ex, _, _ = simulate(prices, orders, first, E, products)
            row[err] = {p: ex[p] - ref[p] for p in products}
        rows.append(row)
    return rows


def main():
    market = load(sys.argv[1] if len(sys.argv) > 1 else None)
    products = market["products"]
    prices = {p: [row[p] for row in market["history"]] for p in products}
    n = len(prices[products[0]])
    first = MIN_HISTORY          # first index with enough history (0-based)
    mid = (first + n - 1) // 2
    segments = {"H1": (first, mid), "H2": (mid, n - 1), "ALL": (first, n - 1)}

    # 0. Cycle period: autocorrelation of price / centred 48-tick mean.
    print("0) Cycle period (autocorrelation of the detrended price)")
    for p in products:
        x = prices[p]
        d = [x[t] / (sum(x[t - 24:t + 24]) / 48) - 1 for t in range(24, n - 24)]
        md = sum(d) / len(d)
        var = sum((a - md) ** 2 for a in d)
        ac = {lag: sum((d[i] - md) * (d[i + lag] - md) for i in range(len(d) - lag)) / var
              for lag in range(1, 80)}
        best = max((lag for lag in ac if lag >= 8), key=ac.get)
        print(f"  {p}: peak at {best} ticks (ac {ac[best]:+.2f}), "
              f"half-period {best // 2} ticks (ac {ac[best // 2]:+.2f})")
    print("  real cycle = strong positive peak AND strong negative half-period\n")

    # Precompute every method's signal once per tick.
    sig = {p: {m: [0.0] * n for m in METHODS} for p in products}
    for p in products:
        for t in range(first, n):
            hist = prices[p][: t + 1]
            for m, f in METHODS.items():
                sig[p][m][t] = f(hist)

    def index(p, weights, t):
        tot = sum(weights.values())
        return sum(w * sig[p][m][t] for m, w in weights.items()) / tot

    zero = {p: [0.0] * n for p in products}

    def solo(p, order_series, seg):
        orders = dict(zero)
        orders[p] = order_series
        ex, tr, hold = simulate(prices, orders, *segments[seg], products)
        return ex[p], tr[p], hold

    print(f"History: {n} ticks, H1 = ticks {first+1}-{mid+1}, H2 = ticks {mid+1}-{n}\n")

    # 1. Each method alone, no dead zone (order = s / 2).
    print("1) Each method alone, no dead zone -- excess vs hold (EUR), per half")
    print(f"{'method':<14}" + "".join(f"{p+' H1':>10}{p+' H2':>10}" for p in products))
    for m in METHODS:
        row = f"{m:<14}"
        for p in products:
            series = [to_order(s, 0) for s in sig[p][m]]
            row += f"{solo(p, series, 'H1')[0]:>10.0f}{solo(p, series, 'H2')[0]:>10.0f}"
        print(row)

    # 2. Families (equal weights 100) x dead zone.
    results = {}
    for fam, w in FAMILIES.items():
        for p in products:
            idx = [index(p, w, t) if t >= first else 0.0 for t in range(n)]
            for dz in DEAD_ZONES:
                series = [to_order(i, dz) for i in idx]
                results[(fam, p, dz)] = {seg: solo(p, series, seg) for seg in segments}
            results[(fam, p, "maxabs")] = max(abs(i) for i in idx)
    for fam in FAMILIES:
        print(f"\n2) Family '{fam}' (weights 100 each) -- excess EUR / trades")
        print(f"{'dz':>5}" + "".join(f"{p+' H1':>13}{p+' H2':>13}" for p in products))
        for dz in DEAD_ZONES:
            row = f"{dz:>5.2f}"
            for p in products:
                for seg in ("H1", "H2"):
                    ex, tr, _ = results[(fam, p, dz)][seg]
                    row += f"{ex:>8.0f}/{tr:<4}"
            print(row)
        print("max |S| reached: " + ", ".join(
            f"{p} {results[(fam, p, 'maxabs')]:.2f}" for p in products))

    # 3. Random trader baseline (uniform(-1, 1), same execution), 300 runs.
    rng = random.Random(0)
    print("\n3) random_trader baseline, mean excess over 300 runs (EUR)")
    for seg in ("H1", "H2"):
        tot = {p: 0.0 for p in products}
        for _ in range(300):
            orders = {p: [rng.uniform(-1, 1) for _ in range(n)] for p in products}
            ex, _, _ = simulate(prices, orders, *segments[seg], products)
            for p in products:
                tot[p] += ex[p] / 300
        print(f"  {seg}: " + ", ".join(f"{p} {tot[p]:.0f}" for p in products))

    # 4. Final per-product config (signals.PRODUCT_CONFIG), all products trading
    #    together against the same money, x dead zone.
    final_idx = {p: [product_index(prices[p][: t + 1], p) if t >= first else 0.0
                     for t in range(n)] for p in products}
    print("\n4) PRODUCT_CONFIG, all products together -- excess EUR / trades")
    print("max |S| reached: " + ", ".join(
        f"{p} {max(abs(i) for i in final_idx[p]):.2f}" for p in products))
    print(f"{'dz':>5}" + "".join(f"{p:>12}" for p in products)
          + f"{'total':>9}{'% hold':>8}   (H1 | H2)")
    for dz in DEAD_ZONES:
        orders = {p: [to_order(i, dz) for i in final_idx[p]] for p in products}
        cells = {}
        for seg in ("H1", "H2"):
            ex, tr, hold = simulate(prices, orders, *segments[seg], products)
            tot = sum(ex.values())
            cells[seg] = ("".join(f"{ex[p]:>7.0f}/{tr[p]:<4}" for p in products)
                          + f"{tot:>9.0f}{100 * tot / hold:>7.1f}%")
        print(f"{dz:>5.2f}{cells['H1']}   |{cells['H2']}")

    # 5. Endgame rule: pretend the game ends at several past ticks.
    ticks = [row["t"] for row in market["history"]]
    rows = endgame_test(prices, ticks, products, final_idx, first)
    print(f"\n5) Endgame rule (last {ENDGAME_TICKS} ticks) -- extra EUR vs normal strategy")
    print(f"{'end':>5}" + "".join(f"{p:>8}" for p in products)
          + f"{'total':>9}{'err -4':>9}{'err +4':>9}")
    for r in rows:
        tot = {e: sum(r[e].values()) for e in (0, -4, 4)}
        print(f"{r['end_tick']:>5}" + "".join(f"{r[0][p]:>8.0f}" for p in products)
              + f"{tot[0]:>9.0f}{tot[-4]:>9.0f}{tot[4]:>9.0f}")
    for e in (0, -4, 4):
        tots = [sum(r[e].values()) for r in rows]
        print(f"  error {e:+d} ticks: mean {sum(tots) / len(tots):+.0f} EUR, "
              f"positive {sum(t > 0 for t in tots)}/{len(tots)}")


if __name__ == "__main__":
    main()
