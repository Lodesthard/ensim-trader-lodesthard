"""Success-based adjustment of the dead zone, separately for buys and sells.

Idea (from the strategy discussion): trade more readily on a product where the
signal has been right lately, be more demanding where it has been wrong. Two
safeguards against the classic traps:
- success = quality of the DECISIONS, not "are we up or down on this product"
  (we started with 1000 units of each, so B is "up" and C "down" without any
  trade). A buy signal at tick k is a success if the price H ticks after the
  execution is above the execution price; a sell signal, if it is below.
- it never increases the order SIZE (no "won, so buy more"); it only moves the
  dead zone, inside bounds, on a sliding window, so a bad streak can not lock a
  product forever and an old streak is forgotten.

    dz_side = clip(BASE + K * (0.5 - hit_rate_side), DZ_MIN, DZ_MAX)
    hit >= 57 % -> 0.10 (min) ; 50 % -> 0.20 ; 30 % -> 0.50 ; 20 % -> 0.65
    (|S| never exceeds ~0.8, so a dead zone near 0.9 stops the product.)

Backtest, 2nd half of the real history (EUR vs never trading):
                       normal signal   inverted signal   breaks at tick 300
    fixed dead zone        +7 941          -4 313               0
    this rule              +7 836          -1 091          +1 587
A cheap insurance: -1.3 % when the signal works, most of the loss avoided when
it stops working. With bounds 0.10-0.45 (first try) it did not protect at all:
the big losing orders have |S| > 0.45.

Signals are recomputed from the price history (deterministic), so the measure
works from the first run, not only once real trades exist.
"""
from signals import DEAD_ZONE, ORDER_GAIN

WINDOW = 48          # ticks looked back (one A cycle, two C cycles)
HORIZON = {"A": 12, "C": 6}  # quarter of the product's cycle
DEFAULT_HORIZON = 6
CANDIDATE = 0.10     # |S| from which a tick counts as a buy/sell signal
MIN_SIGNALS = 5      # fewer judged signals than this -> base dead zone
K = 1.5              # sensitivity of the dead zone to the hit rate
DZ_MIN, DZ_MAX = 0.10, 0.90


def hit_rates(prices, s_hist, product, t=None):
    """Hit rates of past buy and sell signals whose outcome is known at index t.
    Returns {"buy": (hits, n), "sell": (hits, n)}."""
    t = len(prices) - 1 if t is None else t
    h = HORIZON.get(product, DEFAULT_HORIZON)
    out = {"buy": [0, 0], "sell": [0, 0]}
    for k in range(max(0, t - WINDOW), t - h):  # exec at k+1, judged at k+1+h <= t
        s = s_hist[k]
        if abs(s) < CANDIDATE:
            continue
        side = "buy" if s > 0 else "sell"
        move = prices[k + 1 + h] - prices[k + 1]
        out[side][1] += 1
        if (move > 0) == (side == "buy") and move != 0:
            out[side][0] += 1
    return {k: tuple(v) for k, v in out.items()}


def dead_zones(rates):
    """Dead zone for buys and for sells from the hit rates."""
    dz = {}
    for side, (hits, n) in rates.items():
        if n < MIN_SIGNALS:
            dz[side] = DEAD_ZONE
        else:
            dz[side] = min(DZ_MAX, max(DZ_MIN, DEAD_ZONE + K * (0.5 - hits / n)))
    return dz


def adaptive_order(s, dz):
    """Order from the index S with side-specific dead zones (size rule unchanged)."""
    if s > 0 and s >= dz["buy"]:
        return min(1.0, ORDER_GAIN * s)
    if s < 0 and -s >= dz["sell"]:
        return max(-1.0, ORDER_GAIN * s)
    return 0.0


def describe(rates, dz):
    parts = []
    for side, fr_side in (("buy", "achats"), ("sell", "ventes")):
        hits, n = rates[side]
        rate = f"{100 * hits / n:.0f} %" if n else "—"
        parts.append(f"{fr_side} {rate} ({n}) → seuil {dz[side]:.2f}".replace(".", ","))
    return "réussite " + ", ".join(parts)


def adaptive_series(prices, s_series, product, first):
    """Orders and dead zones for every tick of a precomputed S series (backtest)."""
    n = len(prices)
    orders, zones = [0.0] * n, [None] * n
    for t in range(first, n):
        dz = dead_zones(hit_rates(prices, s_series, product, t))
        orders[t] = adaptive_order(s_series[t], dz)
        zones[t] = dz
    return orders, zones
