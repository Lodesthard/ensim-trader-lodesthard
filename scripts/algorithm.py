"""Trader lodesthard -- writes decision.json and appends docs/data/log.csv.

Pipeline for each product (see signals.py and endgame.py):
1. raw index S in [-1, +1] = weighted mean of same-family methods;
2. dead zone: |S| < DEAD_ZONE -> 0, else order = 2 x S, clipped (S = 0.5 -> 100 units);
   with a success-based dead zone per side (adaptive.py): the dead zone for
   buys (resp. sells) rises when recent buy (sell) signals were wrong;
3. endgame rule in the last ticks before the end (1 Dec 2026, 15:00 Paris);
4. stock/budget layer: never sell more than held, never spend below the reserve
   (cash is shared by A, B, C, executed in that order like the organizer does).
"""
import csv
import json
import os
import time

import requests

import adaptive
from endgame import END_UTC, estimate_remaining_ticks, endgame_order, forecast
from signals import PRODUCT_CONFIG, product_index

MARKET_URL = "https://raw.githubusercontent.com/exofri/ensim-trade-center/refs/heads/main/docs/data/market.json"
HOLDINGS_URL = "https://raw.githubusercontent.com/exofri/ensim-trade-center/refs/heads/main/traders/lodesthard/holdings.json"
LEADERBOARD_URL = "https://raw.githubusercontent.com/exofri/ensim-trade-center/refs/heads/main/docs/data/leaderboard.json"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISION_PATH = os.path.join(ROOT, "decision.json")
LOG_PATH = os.path.join(ROOT, "docs", "data", "log.csv")
BASELINES = ["random_trader", "random_buyer", "random_seller"]

MIN_MONEY_RESERVE = 50  # never let a buy signal spend below this much money


def fetch_json(url):
    resp = requests.get(url, proxies={"http": None, "https": None}, timeout=(10, 20))
    resp.raise_for_status()
    return resp.json()


def fr(x, d=2):
    return f"{x:+.{d}f}".replace(".", ",").replace("-", "−")


def compute_signal(prices, product):
    """Raw index S in [-1, +1] for one product (0 if no method is configured)."""
    return product_index(prices, product)


def apply_budget_guard(raw_signal, money, price, reserve=MIN_MONEY_RESERVE):
    # A buy signal (positive) that would spend into the reserve gets scaled down,
    # not silently zeroed out -- you still act on the signal, just smaller. A sell
    # signal is never touched here: selling only ever adds money, never risks it.
    if raw_signal <= 0:
        return raw_signal
    if money <= reserve:
        return 0.0
    max_affordable_units = (money - reserve) / price
    desired_units = 100 * raw_signal
    if desired_units <= max_affordable_units:
        return raw_signal
    return round(max_affordable_units / 100, 3)


def stock_guard(order, stock):
    """Never ask to sell more than we hold."""
    if order < 0 and 100 * -order > stock:
        return -stock / 100
    return order


def signal_reason(product, s, order, dz):
    if not PRODUCT_CONFIG.get(product):
        return "aucune méthode retenue (hausse lente, vendre ferait rater la hausse) → conserver"
    if order == 0:
        zone = f"−{dz['sell']:.2f} / +{dz['buy']:.2f}".replace(".", ",")
        return f"S = {fr(s)} dans la zone morte ({zone}) → conserver"
    where = "en dessous" if s > 0 else "au-dessus"
    side = "achat" if order > 0 else "vente"
    return f"S = {fr(s)} : prix {where} de son niveau normal → {side} {abs(order) * 100:.0f} u."


def decide(market, holdings, now=None):
    """Return (decision dict, per-product details, endgame info)."""
    products = market["products"]
    history = market["history"]
    ticks = [row["t"] for row in history]
    remaining, gap_h, hours_left = estimate_remaining_ticks(
        [row["timestamp"] for row in history], now=now)

    money = holdings["money"]
    decision, details = {}, {}
    for p in products:
        prices = [row[p] for row in history]
        s = compute_signal(prices, p)
        # recompute the recent signal history to judge its recent decisions
        n = len(prices)
        lookback = adaptive.WINDOW + max(adaptive.HORIZON.values()) + 2
        s_hist = [0.0] * n
        for t in range(max(0, n - lookback), n):
            s_hist[t] = compute_signal(prices[: t + 1], p)
        rates = adaptive.hit_rates(prices, s_hist, p)
        dz = adaptive.dead_zones(rates)
        order = adaptive.adaptive_order(s, dz)
        reason = signal_reason(p, s, order, dz)
        if PRODUCT_CONFIG.get(p):
            reason += f" [{adaptive.describe(rates, dz)}]"
        fc = forecast(p, ticks, prices, remaining)
        order, phase, end_reason = endgame_order(p, ticks, prices, order, remaining, fc)
        if end_reason:
            reason = end_reason
        order = stock_guard(order, holdings["products"][p])
        guarded = apply_budget_guard(order, money, prices[-1])
        if guarded != order:
            reason += " Quantité réduite (argent disponible)."
        if guarded > 0:
            money -= 100 * guarded * prices[-1]  # cash left for the next product
        decision[p] = round(guarded, 3)
        details[p] = {"S": s, "order": decision[p], "phase": phase, "reason": reason,
                      "price": prices[-1], "forecast": fc, "dead_zone": dz, "hit_rates": rates}
    info = {"remaining_ticks": remaining, "median_gap_h": gap_h, "hours_left": hours_left,
            "end_utc": END_UTC.isoformat()}
    return decision, details, info


def append_log(market, holdings, leaderboard, details, info):
    """One row per market tick (the workflow runs more often than the market)."""
    products = market["products"]
    last = market["history"][-1]
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    exists = os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 0
    if exists:
        with open(LOG_PATH, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if rows and rows[-1]["tick"] == str(last["t"]):
            return False
    values = {e["id"]: e["total_value"] for e in (leaderboard or {}).get("leaderboard", [])}
    total = holdings["money"] + sum(holdings["products"][p] * last[p] for p in products)
    row = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tick": last["t"]}
    row.update({f"price_{p}": last[p] for p in products})
    row["money"] = round(holdings["money"], 2)
    row.update({f"stock_{p}": round(holdings["products"][p], 2) for p in products})
    row["total_value"] = round(total, 2)
    row.update({b: values.get(b, "") for b in BASELINES})
    row.update({f"S_{p}": round(details[p]["S"], 3) for p in products})
    row.update({f"order_{p}": details[p]["order"] for p in products})
    row["phase"] = "fin" if any(details[p]["phase"] == "fin" for p in products) else "normal"
    row["remaining_ticks"] = round(info["remaining_ticks"], 1)
    row.update({f"reason_{p}": details[p]["reason"] for p in products})
    with open(LOG_PATH, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            w.writeheader()
        w.writerow(row)
    return True


def main():
    market = fetch_json(MARKET_URL)
    holdings = fetch_json(HOLDINGS_URL)
    try:
        leaderboard = fetch_json(LEADERBOARD_URL)
    except requests.RequestException:
        leaderboard = None  # log still written, baselines left empty

    decision, details, info = decide(market, holdings)
    decision["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(DECISION_PATH, "w", encoding="utf-8") as f:
        json.dump(decision, f, indent=2)
    logged = append_log(market, holdings, leaderboard, details, info)

    print(f"Wrote decision.json: {decision}")
    print(f"~{info['remaining_ticks']:.1f} ticks left ({info['hours_left']:.0f} h, "
          f"1 tick / {info['median_gap_h']:.1f} h)")
    for p, d in details.items():
        print(f"  {p}: {d['reason']}")
    print("log.csv: new row" if logged else "log.csv: tick already logged")


if __name__ == "__main__":
    main()
