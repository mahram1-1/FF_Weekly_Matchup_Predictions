"""Deterministic analysis. Every number the writeup uses is computed here.

Design rule: the LLM never does arithmetic. It receives a finished facts packet
and writes prose about it. This is the difference between an agent that's funny
and right, and an agent that's funny and confidently wrong about Bijan's floor.
"""
from __future__ import annotations
import math, statistics as st
from collections import defaultdict

FLEX_OK = {"RB", "WR", "TE"}


# ---------------------------------------------------------------- lineups
def optimal_lineup(player_ids, points: dict, pos_of: dict, slots: list) -> tuple[float, list]:
    """Greedy-with-backtrack best legal lineup. Slots like [QB,RB,RB,WR,WR,TE,FLEX,K,DEF]."""
    avail = sorted([p for p in player_ids if p in points],
                   key=lambda p: -points.get(p, 0))
    used, filled = set(), []
    # fill strict slots first, FLEX last, so a stud WR isn't wasted in FLEX
    order = [s for s in slots if s != "FLEX"] + [s for s in slots if s == "FLEX"]
    for slot in order:
        ok = FLEX_OK if slot == "FLEX" else {slot}
        for p in avail:
            if p not in used and pos_of.get(p) in ok:
                used.add(p); filled.append((slot, p)); break
    return round(sum(points.get(p, 0) for _, p in filled), 2), filled


def lineup_efficiency(team, points, pos_of, slots):
    """How much did the manager leave on the bench? Pure coaching signal."""
    started = round(sum(points.get(p, 0) for p in team["starters"]), 2)
    best, _ = optimal_lineup(team["players"], points, pos_of, slots)
    return {"started": started, "optimal": best,
            "left_on_bench": round(best - started, 2),
            "efficiency": round(started / best, 4) if best else 1.0}


# ---------------------------------------------------------------- record quality
def all_play(weekly_scores: dict[int, dict[int, float]]) -> dict[int, dict]:
    """weekly_scores[week][roster_id] = points.

    All-play record = your record if you played EVERY team every week. In a 12-team
    league, actual W-L through week 6 is roughly half schedule luck. This isn't.
    """
    w = defaultdict(lambda: {"w": 0, "l": 0, "t": 0})
    for _, scores in weekly_scores.items():
        for rid, pts in scores.items():
            for orid, opts in scores.items():
                if rid == orid:
                    continue
                if pts > opts: w[rid]["w"] += 1
                elif pts < opts: w[rid]["l"] += 1
                else: w[rid]["t"] += 1
    for rid, r in w.items():
        n = r["w"] + r["l"] + r["t"]
        r["pct"] = round((r["w"] + 0.5 * r["t"]) / n, 4) if n else 0.0
    return dict(w)


def luck_index(teams, ap, n_teams):
    """Actual wins minus all-play expected wins. Positive = fortunate schedule."""
    out = {}
    for rid, t in teams.items():
        played = t["wins"] + t["losses"] + t["ties"]
        exp = ap.get(rid, {}).get("pct", 0.5) * played
        out[rid] = round((t["wins"] + 0.5 * t["ties"]) - exp, 2)
    return out


def recency_weighted_pf(weekly_scores, half_life=3.0):
    """Exponential decay — week 8 tells you more about week 9 than week 1 does."""
    if not weekly_scores:
        return {}
    latest = max(weekly_scores)
    num, den = defaultdict(float), defaultdict(float)
    for wk, scores in weekly_scores.items():
        w = 0.5 ** ((latest - wk) / half_life)
        for rid, pts in scores.items():
            num[rid] += w * pts; den[rid] += w
    return {rid: round(num[rid] / den[rid], 2) for rid in num if den[rid]}


# ---------------------------------------------------------------- power model
def _z(d: dict) -> dict:
    vals = list(d.values())
    if len(vals) < 2:
        return {k: 0.0 for k in d}
    m, s = st.mean(vals), st.pstdev(vals)
    return {k: (v - m) / s if s else 0.0 for k, v in d.items()}


def power_rankings(teams, weekly_scores, roster_strength, week, sim_allplay=None,
                   sim_mean=None, games=17):
    """Composite in POINTS PER WEEK, not z-scores.

    Z-scoring each component was wrong here. Rest-of-season roster strength spans
    only ~80 points across the league (about 4.7 per week) while the weekly
    simulation spans ~11 points per week. Standardising both to unit variance gave
    the 4.7-point spread equal weight with the 11-point one, and produced tables
    where 12th place projected higher than 8th. Blending in points keeps each
    component's real scale, and the output is readable: the composite is a
    points-per-week estimate you can sanity-check by eye.
    """
    maturity = min(max(week - 1, 0), 6) / 6.0
    W = {"roster": 0.40 - 0.22 * maturity,   # rest-of-season roster quality
         "sim":    0.60 - 0.38 * maturity,   # this week, injury-adjusted
         "actual": 0.60 * maturity}          # realised scoring, once it exists
    tot = sum(W.values()) or 1.0
    W = {k: v / tot for k, v in W.items()}

    ap = all_play(weekly_scores)
    rec_pf = recency_weighted_pf(weekly_scores)
    sim_mean = sim_mean or {}
    ppw = {}
    for r in teams:
        roster_ppw = roster_strength.get(r, 0.0) / games
        sim_ppw = sim_mean.get(r, roster_ppw)
        act_ppw = rec_pf.get(r, sim_ppw)
        ppw[r] = round(W["roster"] * roster_ppw + W["sim"] * sim_ppw
                       + W["actual"] * act_ppw, 2)

    ranked = sorted(ppw, key=lambda r: -ppw[r])
    return {
        "weights": {k: round(v, 3) for k, v in W.items()},
        "units": "points per week",
        "table": [{"rank": i + 1, "roster_id": r, "team": teams[r]["team"],
                   "score": ppw[r],
                   "components": {
                       "roster_ppw": round(roster_strength.get(r, 0.0) / games, 1),
                       "sim_ppw": round(sim_mean.get(r, 0.0), 1),
                       "actual_ppw": round(rec_pf.get(r, 0.0), 1) if rec_pf else None},
                   "all_play_sim": (sim_allplay or {}).get(r),
                   "all_play_actual": ap.get(r)} for i, r in enumerate(ranked)],
    }


# ---------------------------------------------------------------- disagreement
def projection_spread(player_ids, sources: dict[str, dict], min_sources=2):
    """Per-player forecast disagreement across sources.

    sd     = raw points of disagreement (what actually swings a matchup)
    cv     = sd / mean (normalises so it doesn't just surface every stud)
    Use sd for "make or break", cv for "nobody knows what this guy is".
    """
    out = {}
    for pid in player_ids:
        vals = {s: d[pid] for s, d in sources.items() if pid in d}
        if len(vals) < min_sources:
            continue
        v = list(vals.values())
        mean = st.mean(v)
        sd = st.pstdev(v) if len(v) > 1 else 0.0
        out[pid] = {"by_source": {k: round(x, 2) for k, x in vals.items()},
                    "mean": round(mean, 2), "sd": round(sd, 2),
                    "range": round(max(v) - min(v), 2),
                    "cv": round(sd / mean, 3) if mean > 1 else 0.0,
                    "n_sources": len(v),
                    "high": max(vals, key=vals.get), "low": min(vals, key=vals.get)}
    return out


def make_or_break(team, spread, pos_of, top_n=2):
    """Starters whose uncertainty most threatens the team's projected total."""
    rows = [{"player_id": p, "pos": pos_of.get(p), **spread[p]}
            for p in team["starters"] if p in spread]
    rows.sort(key=lambda r: -r["sd"])
    team_sd = round(math.sqrt(sum(r["sd"] ** 2 for r in rows)), 2)  # independent errors
    return rows[:top_n], team_sd


# ---------------------------------------------------------------- matchups
def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def matchup_outlook(pairs, proj_total, team_sd, teams, weekly_sigma=26.0):
    """Win probability from projected margin and combined uncertainty.

    weekly_sigma is the real-world week-to-week noise of a fantasy lineup (~24-28
    pts in full PPR). It dwarfs forecaster disagreement, which is exactly why
    "my projection says I win by 9" means much less than people think — and why
    this model reports 60/40 where Sleeper's would say 70/30.
    """
    out = []
    for a, b in pairs:
        pa, pb = proj_total.get(a, 0), proj_total.get(b, 0)
        sd = math.sqrt(weekly_sigma ** 2 * 2 + team_sd.get(a, 0) ** 2 + team_sd.get(b, 0) ** 2)
        wp = _phi((pa - pb) / sd) if sd else 0.5
        out.append({"home": teams[a]["team"], "away": teams[b]["team"],
                    "home_roster_id": a, "away_roster_id": b,
                    "proj": [round(pa, 2), round(pb, 2)],
                    "margin": round(pa - pb, 2),
                    "home_win_prob": round(wp, 3),
                    "coin_flip": abs(wp - 0.5) < 0.04,
                    "combined_forecast_sd": round(
                        math.sqrt(team_sd.get(a, 0) ** 2 + team_sd.get(b, 0) ** 2), 2)})
    return sorted(out, key=lambda m: abs(m["home_win_prob"] - 0.5))
