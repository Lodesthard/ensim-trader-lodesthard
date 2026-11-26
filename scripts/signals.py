"""Trading signal: per-product weighted combination of same-family methods.

Every method returns a value in [-1, +1] expressed in the same unit
(deviation in standard deviations, divided by 2 and clipped), so weights and
the dead zone mean the same thing for every method and every product.

    S = sum(w_i * s_i) / sum(w_i)          (raw index, in [-1, +1])
    order = clip(ORDER_GAIN * S) if |S| >= dead_zone else 0   (sent to decision.json)
    ORDER_GAIN = 2: S = 0.5 -> order 1.0 -> 100 units (deliberately aggressive)

Families:
- "mean_reversion": price far above its normal level -> sell, far below -> buy.
- "trend": price engaged in a sustained rise -> buy, fall -> sell.
"""
import math

MIN_HISTORY = 48  # longest window used below


def _mean(xs):
    return sum(xs) / len(xs)


def _std(xs):
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _clip(x):
    return max(-1.0, min(1.0, x))


def _ema(xs, alpha):
    level = xs[0]
    for x in xs[1:]:
        level = alpha * x + (1 - alpha) * level
    return level


# ---------------------------------------------------------------- mean reversion
def mr_bollinger(prices, n=48):
    """z-score against the 48-tick moving average (one full cycle of A)."""
    w = prices[-n:]
    sd = _std(w)
    if sd == 0:
        return 0.0
    z = (prices[-1] - _mean(w)) / sd
    return _clip(-z / 2)


def mr_ema(prices, alpha=0.05, n=48):
    """Deviation from a slow exponential smoothing (TD4), in 48-tick std units."""
    sd = _std(prices[-n:])
    if sd == 0:
        return 0.0
    z = (prices[-1] - _ema(prices, alpha)) / sd
    return _clip(-z / 2)


def mr_long_mean(prices):
    """z-score against the mean of the whole observed history (stationary product)."""
    sd = _std(prices)
    if sd == 0:
        return 0.0
    z = (prices[-1] - _mean(prices)) / sd
    return _clip(-z / 2)


# ------------------------------------------------------------------------ trend
def tf_momentum(prices, n=24):
    """24-tick return, normalised by the volatility expected over 24 ticks."""
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(len(prices) - 48, len(prices))]
    sd = _std(rets) * math.sqrt(n)
    if sd == 0:
        return 0.0
    z = math.log(prices[-1] / prices[-1 - n]) / sd
    return _clip(z / 2)


def tf_ma_cross(prices, short=6, long=24):
    """Short MA minus long MA, in 24-tick std units."""
    w = prices[-long:]
    sd = _std(w)
    if sd == 0:
        return 0.0
    z = (_mean(prices[-short:]) - _mean(w)) / sd
    return _clip(z / 2)


def tf_slope(prices, n=48):
    """Slope of a linear regression on the last 48 ticks (TD4 linear projection),
    expressed as the move over the window in std units of the window."""
    w = prices[-n:]
    xs = range(n)
    mx, my = (n - 1) / 2, _mean(w)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, w)) / sxx
    sd = _std(w)
    if sd == 0:
        return 0.0
    z = slope * n / sd
    return _clip(z / 2)


METHODS = {
    "mr_bollinger": mr_bollinger,
    "mr_ema": mr_ema,
    "mr_long_mean": mr_long_mean,
    "tf_momentum": tf_momentum,
    "tf_ma_cross": tf_ma_cross,
    "tf_slope": tf_slope,
}

FAMILIES = {
    "mean_reversion": {"mr_bollinger": 100, "mr_ema": 100, "mr_long_mean": 100},
    "trend": {"tf_momentum": 100, "tf_ma_cross": 100, "tf_slope": 100},
}


# Per-product configuration: (method, parameters, weight). Weight 0 = method
# unsuited to this product. Windows follow the cycle measured on the history
# (autocorrelation of the detrended price): A ~48 ticks, C ~24 ticks, B none.
PRODUCT_CONFIG = {
    # A: stationary (mean 5.01 -> 4.99), clean 48-tick cycle -> mean reversion,
    # windows = one cycle; the long-run mean is meaningful because A has no trend.
    "A": [("mr_bollinger", {"n": 48}, 100),
          ("mr_ema", {"alpha": 0.05, "n": 48}, 100),
          ("mr_long_mean", {}, 100)],
    # B: rising drift, no usable cycle. We already hold 1000 units and cash buys
    # at most ~80 more, so any sell gives up the drift -> no trading.
    "B": [],
    # C: 24-tick cycle on top of a falling level (2.52 -> 1.55) -> mean reversion
    # on windows of one C cycle; long-run mean excluded (level is not stable).
    "C": [("mr_bollinger", {"n": 24}, 100),
          ("mr_ema", {"alpha": 0.08, "n": 24}, 100),
          ("mr_long_mean", {}, 0)],
}

DEAD_ZONE = 0.20
# Order size multiplier. Backtest on the real history, 2nd half, dead zone 0.20:
# gain 0.5 -> +2 287 EUR (max drawdown -14.4 %), 1 -> +4 565 EUR (-11.9 %),
# 2 -> +7 941 EUR (-12.0 %); never trading: max drawdown -16.8 %.
ORDER_GAIN = 2.0


def product_index(prices, product):
    """Raw index S in [-1, +1] for one product, from PRODUCT_CONFIG."""
    cfg = [c for c in PRODUCT_CONFIG.get(product, []) if c[2] > 0]
    if len(prices) <= MIN_HISTORY or not cfg:
        return 0.0
    total = sum(w for _, _, w in cfg)
    return sum(w * METHODS[m](prices, **kw) for m, kw, w in cfg) / total


def raw_index(prices, weights):
    """Weighted mean of method signals, in [-1, +1]. 0 while history is too short."""
    if len(prices) <= MIN_HISTORY:
        return 0.0
    total = sum(weights.values())
    if total == 0:
        return 0.0
    return sum(w * METHODS[m](prices) for m, w in weights.items()) / total


def to_order(index, dead_zone):
    """Dead zone, then x ORDER_GAIN, clipped: index 0.5 -> order 1.0 -> 100 units."""
    if abs(index) < dead_zone:
        return 0.0
    return max(-1.0, min(1.0, ORDER_GAIN * index))
