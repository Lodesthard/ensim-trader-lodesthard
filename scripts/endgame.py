"""End-of-game rule (game ends Tuesday 1 December 2026, 15:00 Paris = 14:00 UTC).

At the end, the score is money + stock x final price. Cash earns nothing and a
unit bought late has no time to come back, so during the last ENDGAME_TICKS:

- A and C (cycle + trend, model fits the history with R^2 > 0.9): the
  forecast final price replaces the mean-reversion signal.
  * sell only if the current price is above the forecast final price AND close
    to the best price still expected before the end (don't sell on the way up);
  * buy only if the current price is below the forecast final price AND close
    to the lowest price still expected (the rebound must happen before the end);
  * size grows with the expected gain: 10 % gap -> 100 units;
  * otherwise hold (any other trade would lose money by the end).
- B (slow rise, no reliable cycle): in the last PARK_TICKS, idle cash is parked
  in B in small slices, because B rises on average and cash does not.

Forecast model, per product with a cycle of period P (from signals.PRODUCT_CONFIG):
    price(t) = a + b*t + (c + d*t)*sin(2*pi*t/P) + (e + f*t)*cos(2*pi*t/P)
which is the expanded form of (level + drift*t) * (1 + amplitude*sin(...)).
Fitted by least squares in pure Python (the workflow only installs requests).
"""
import math
from datetime import datetime, timedelta, timezone

END_UTC = datetime(2026, 12, 1, 14, 0, tzinfo=timezone.utc)
ENDGAME_TICKS = 12      # endgame starts when this many ticks (or fewer) remain
TICK_UNCERTAINTY = 3    # +/- ticks around the estimated last tick
NEAR_PEAK = 0.97        # sell only if price >= 97 % of the best price still expected
                        # (buy only if price <= lowest expected / 0.97)
FULL_SELL_GAP = 0.10    # 10 % above the forecast final price -> sell 100 units
MIN_GAP = 0.01          # below +1 % expected gain, not worth selling
MIN_R2 = 0.90           # model must explain 90 % of the price variance
PARK_TICKS = 6          # park idle cash in B during the last 6 ticks
PARK_ORDER = 0.3        # 30 units of B per tick at most
PACE_WINDOW_H = 72      # market pace measured over the last 72 hours
PERIODS = {"A": 48, "C": 24}


# ------------------------------------------------------------------ model fit
def _features(t, period):
    w = 2 * math.pi * t / period
    s, c = math.sin(w), math.cos(w)
    return [1.0, t, s, c, t * s, t * c]


def _solve(a, b):
    """Gaussian elimination with partial pivoting (small dense system)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            return None
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for k in range(col, n + 1):
                m[r][k] -= f * m[col][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def fit_model(ticks, prices, period):
    """Least-squares fit; returns (coefficients, R^2) or (None, 0)."""
    k = 6
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for t, y in zip(ticks, prices):
        f = _features(t, period)
        for i in range(k):
            xty[i] += f[i] * y
            for j in range(k):
                xtx[i][j] += f[i] * f[j]
    coef = _solve(xtx, xty)
    if coef is None:
        return None, 0.0
    mean = sum(prices) / len(prices)
    ss_tot = sum((y - mean) ** 2 for y in prices)
    ss_res = sum((y - predict(coef, t, period)) ** 2 for t, y in zip(ticks, prices))
    return coef, (1 - ss_res / ss_tot) if ss_tot else 0.0


def predict(coef, t, period):
    return sum(c * f for c, f in zip(coef, _features(t, period)))


# ----------------------------------------------------------- remaining ticks
def estimate_remaining_ticks(timestamps, now=None, end=END_UTC):
    """Ticks left before the end, from the market's pace over the last 72 hours
    (the pace speeds up and slows down; the backtest shows that under-estimating
    the remaining ticks costs more than over-estimating them, so we use the
    recent pace rather than a long median).
    Returns (remaining_ticks, hours_per_tick, hours_left)."""
    now = now or datetime.now(timezone.utc)
    ts = [datetime.fromisoformat(x.replace("Z", "+00:00")) for x in timestamps]
    recent = [x for x in ts if x > ts[-1] - timedelta(hours=PACE_WINDOW_H)]
    if len(recent) >= 4:
        gap = (recent[-1] - recent[0]).total_seconds() / 3600 / (len(recent) - 1)
    else:  # too few recent ticks: median gap of the last 48
        gaps = sorted((b - a).total_seconds() / 3600 for a, b in zip(ts[-49:], ts[-48:]))
        gap = gaps[len(gaps) // 2] if gaps else 1.0
    hours_left = max(0.0, (end - now).total_seconds() / 3600)
    return hours_left / gap if gap > 0 else 0.0, gap, hours_left


# --------------------------------------------------------------- the rule
def forecast(product, ticks, prices, remaining):
    """Final-price forecast for one product, or None if no reliable model."""
    period = PERIODS.get(product)
    if period is None or len(prices) < 2 * period:
        return None
    coef, r2 = fit_model(ticks, prices, period)
    if coef is None or r2 < MIN_R2:
        return None
    t_now = ticks[-1]
    t_end = t_now + max(1, round(remaining))
    window = range(t_end - TICK_UNCERTAINTY, t_end + TICK_UNCERTAINTY + 1)
    final = sum(predict(coef, t, period) for t in window) / len(window)
    future = [predict(coef, t, period) for t in range(t_now + 1, t_end + TICK_UNCERTAINTY + 1)]
    return {"final": final, "best_ahead": max(future) if future else final,
            "worst_ahead": min(future) if future else final, "r2": r2, "t_end": t_end}


def endgame_order(product, ticks, prices, base_order, remaining, fc=None):
    """Return (order, phase, reason). Outside the endgame: base order unchanged."""
    if remaining > ENDGAME_TICKS:
        return base_order, "normal", None
    left = f"fin de partie (~{remaining:.0f} ticks restants)"
    if product == "B":
        if remaining <= PARK_TICKS:
            return PARK_ORDER, "fin", (f"{left} : l'argent ne rapporte rien, B monte en moyenne "
                                       f"→ placement de l'argent inutilisé en B (≤ {PARK_ORDER * 100:.0f} u.)")
        return 0.0, "fin", f"{left} : B conservé (hausse lente)"
    fc = fc if fc is not None else forecast(product, ticks, prices, remaining)
    if fc is None:
        return min(base_order, 0.0), "fin", f"{left} : modèle peu fiable, achats bloqués"
    price = prices[-1]
    gap = price / fc["final"] - 1          # > 0: price above the expected final price
    info = (f"prix {price:.4f} vs final prévu {fc['final']:.4f} ({gap * 100:+.1f} %)"
            ).replace(".", ",")
    if gap > MIN_GAP:
        if price >= NEAR_PEAK * fc["best_ahead"]:
            order = -min(1.0, gap / FULL_SELL_GAP)
            return order, "fin", f"{left} : {info}, proche du sommet restant → vente {abs(order) * 100:.0f} u."
        return 0.0, "fin", f"{left} : {info}, sommet encore à venir → attendre"
    up = fc["final"] / price - 1           # > 0: price below the expected final price
    if up > MIN_GAP:
        if price <= fc["worst_ahead"] / NEAR_PEAK:
            order = min(1.0, up / FULL_SELL_GAP)
            return order, "fin", f"{left} : {info}, proche du creux restant → achat {order * 100:.0f} u."
        return 0.0, "fin", f"{left} : {info}, creux encore à venir → attendre"
    return 0.0, "fin", f"{left} : {info}, écart trop faible → conserver"
