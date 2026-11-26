"""Rebuild the total value of every trader, tick by tick, since tick 1.

The organizer's public leaderboard (docs/data/leaderboard.json) only shows the
current tick, but it is committed at every market tick in the organizer's repo
(exofri/ensim-trade-center). Walking that git history gives the trajectory of
the three reference traders (random_trader, random_buyer, random_seller), of
our own trader and of the leader, from the first tick on.

Output: docs/data/reference_history.csv
    tick, timestamp, lodesthard, random_trader, random_buyer, random_seller,
    leader, leader_name, rank, n_traders

Usage: python scripts/reference_history.py [path/to/existing/clone]
(without argument the repo is cloned into a temporary folder, ~1 MB)
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

ORG_REPO = "https://github.com/exofri/ensim-trade-center.git"
ME = "lodesthard"
BASELINES = ["random_trader", "random_buyer", "random_seller"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "reference_history.csv")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def show_json(repo, commit, path):
    try:
        return json.loads(git(repo, "show", f"{commit}:{path}"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def rebuild(repo):
    commits = git(repo, "log", "--reverse", "--format=%H", "--",
                  "docs/data/leaderboard.json").split()
    rows, seen = [], set()
    for c in commits:
        lb = show_json(repo, c, "docs/data/leaderboard.json")
        state = show_json(repo, c, "market/state.json")
        if not lb or not state or state.get("t") in seen:
            continue
        seen.add(state["t"])
        board = lb.get("leaderboard", [])
        values = {e["id"]: e["total_value"] for e in board}
        students = [e for e in board if not e.get("baseline")]
        leader = max(students, key=lambda e: e["total_value"]) if students else None
        rank = next((i + 1 for i, e in enumerate(board) if e["id"] == ME), "")
        rows.append({
            "tick": state["t"], "timestamp": lb.get("generated_at", ""),
            ME: values.get(ME, ""), **{b: values.get(b, "") for b in BASELINES},
            "leader": leader["total_value"] if leader else "",
            "leader_name": leader.get("name", leader["id"]) if leader else "",
            "rank": rank, "n_traders": len(board),
        })
    return rows


def main():
    if len(sys.argv) > 1:
        repo = sys.argv[1]
        git(repo, "pull", "-q")
        rows = rebuild(repo)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "org")
            subprocess.run(["git", "clone", "-q", ORG_REPO, repo], check=True)
            rows = rebuild(repo)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    mine = [r for r in rows if r[ME] != ""]
    print(f"Wrote {OUT}: {len(rows)} ticks ({rows[0]['tick']}-{rows[-1]['tick']}), "
          f"{ME} present from tick {mine[0]['tick'] if mine else '-'}")


if __name__ == "__main__":
    main()
