"""The two confirmers.

Most of what a reviewer should check is arithmetic and consistency, which code
does reliably and an LLM does not. So these run deterministically against the
facts packet and the rendered document, and return a pass/fail list. The LLM
confirmer prompts in writeup.py sit on top and catch what code cannot: prose that
overstates a number, a claim that does not follow, a tonal contradiction.

Severity:
  FAIL  the document states something untrue or self-contradictory. Do not send.
  WARN  defensible but worth a human glance.
"""
from __future__ import annotations
import re

FAIL, WARN, OK = "FAIL", "WARN", "OK"


def _chk(results, cond, sev, msg):
    results.append({"status": OK if cond else sev, "check": msg})
    return cond


# ------------------------------------------------------------------ shared
def _numbers_in(md: str) -> set:
    """Every number appearing in the rendered document."""
    return {float(x) for x in re.findall(r"-?\d+\.?\d*", md)}


def _contradictions(doc_players: dict) -> list:
    """Any player appearing in both a positive and a negative role."""
    bad = []
    for name, roles in doc_players.items():
        # "cut" and "added" are roster events with no opinion attached -- a
        # waiver pickup can legitimately also be the riskiest starter on the team.
        pos = {r for r in roles if r in ("pro", "watch", "upside", "riser")}
        neg = {r for r in roles if r in ("con", "dislike", "reach", "falling")}
        if pos and neg:
            bad.append({"player": name, "positive": sorted(pos), "negative": sorted(neg)})
    return bad


# ------------------------------------------------- 3. power rankings confirmer
def check_power(f: dict, md: str, live_rosters: dict | None = None) -> dict:
    r = []
    tb = {t["roster_id"]: t for t in f["teams"]}
    table = f["power_rankings"]["table"]

    _chk(r, len(table) == f["league"]["teams"], FAIL,
         f"all {f['league']['teams']} teams ranked")
    _chk(r, len({x["roster_id"] for x in table}) == len(table), FAIL,
         "no team appears twice")

    scores = [x["score"] for x in table]
    _chk(r, scores == sorted(scores, reverse=True), FAIL,
         "rank order matches rating, descending")

    bad_move = [x["team"] for x in table
                if x.get("prev_rank") and x.get("move") != x["prev_rank"] - x["rank"]]
    _chk(r, not bad_move, FAIL, f"movement arrows match prev_rank − rank {bad_move or ''}")

    bad_band = [t["team"] for t in f["teams"]
                if not (t["sim"]["floor_p10"] < t["sim"]["mean"] < t["sim"]["ceiling_p90"])]
    _chk(r, not bad_band, FAIL, f"floor < projection < ceiling for every team {bad_band or ''}")

    bad_tax = [t["team"] for t in f["teams"]
               if abs(t["injury_tax"]["mean"] - (t["sim"]["mean"] - t["sim_healthy"]["mean"])) > 0.15]
    _chk(r, not bad_tax, FAIL, f"injury tax equals as-it-stands minus if-all-play {bad_tax or ''}")

    bad_tax_sign = [t["team"] for t in f["teams"] if t["injury_tax"]["mean"] > 0.15]
    _chk(r, not bad_tax_sign, FAIL,
         f"no team scores HIGHER with injuries than without {bad_tax_sign or ''}")

    bad_ap = [t["team"] for t in f["teams"] if not 0 <= t["sim"]["all_play_pct"] <= 1]
    _chk(r, not bad_ap, FAIL, f"all-play is a valid probability {bad_ap or ''}")

    # contradiction scan across every bucket the document renders
    seen = {}
    for t in f["teams"]:
        p = t.get("pundit") or {}
        for bucket, role in (("watch", "watch"), ("upside", "upside"),
                             ("risers", "riser"), ("dislike", "dislike"),
                             ("reaches", "reach"), ("sliding", "falling")):
            for row in p.get(bucket, []):
                nm = row.get("player") or row.get("name")
                if nm:
                    seen.setdefault(nm, set()).add(role)
        for nm, v in (t.get("verdicts") or {}).items():
            seen.setdefault(nm, set()).add(v["verdict"])
    clash = _contradictions(seen)
    _chk(r, not clash, FAIL,
         "no player is praised and criticised in the same document"
         + (f" — {[c['player'] for c in clash]}" if clash else ""))

    # every named player is actually rostered by the team naming him
    if live_rosters:
        stale = []
        for t in f["teams"]:
            owned = live_rosters.get(t["roster_id"], set())
            p = t.get("pundit") or {}
            for bucket in ("reaches", "sliding", "risers", "dislike"):
                for row in p.get(bucket, []):
                    if row.get("player_id") and row["player_id"] not in owned:
                        stale.append(f"{t['team']}/{row['player']}")
        _chk(r, not stale, FAIL, f"no dropped player is discussed as current {stale or ''}")

    nums = _numbers_in(md)
    _chk(r, all(any(abs(n - t["sim"]["mean"]) < 1 for n in nums) for t in f["teams"][:3]),
         WARN, "projections in the prose trace to the facts packet")

    fails = [x for x in r if x["status"] == FAIL]
    return {"document": "power rankings", "checks": r,
            "failed": len(fails), "warned": len([x for x in r if x["status"] == WARN]),
            "verdict": "DO NOT SEND" if fails else "OK to send",
            "contradictions": clash}


# ----------------------------------------------------- 4. matchup confirmer
def check_matchups(f: dict, md: str, live_rosters: dict | None = None) -> dict:
    r = []
    ms = f["matchups"]
    n = f["league"]["teams"]

    _chk(r, len(ms) == n // 2, FAIL, f"{n // 2} matchups for {n} teams")
    rids = [m["home_roster_id"] for m in ms] + [m["away_roster_id"] for m in ms]
    _chk(r, len(set(rids)) == n, FAIL, "every team appears exactly once")

    bad_p = [m["home"] for m in ms if not 0 <= m["home_win_prob"] <= 1]
    _chk(r, not bad_p, FAIL, f"win probabilities are valid {bad_p or ''}")

    # direction: the higher projection should not be the underdog
    flipped = [f"{m['home']} vs {m['away']}" for m in ms
               if (m["home_mean"] - m["away_mean"]) * (m["home_win_prob"] - 0.5) < -0.5]
    _chk(r, not flipped, FAIL, f"the higher projection is the favourite {flipped or ''}")

    bad_rng = [m["home"] for m in ms
               if not (m["home_range_80"][0] < m["home_mean"] < m["home_range_80"][1])]
    _chk(r, not bad_rng, FAIL, f"floor < projection < ceiling per team {bad_rng or ''}")

    # band identity: odds must rise from floor to expected to ceiling
    bad_band, bad_sum = [], []
    for m in ms:
        b = m.get("bands") or {}
        for side in ("home", "away"):
            k = b.get(side)
            if not k:
                continue
            o = [k["floor"]["win_odds"], k["expected"]["win_odds"], k["ceiling"]["win_odds"]]
            s = [k["floor"]["score"], k["expected"]["score"], k["ceiling"]["score"]]
            if o != sorted(o) or s != sorted(s):
                bad_band.append(f"{m[side]}")
        if b.get("home") and b.get("away"):
            tot = b["home"]["expected"]["win_odds"] + b["away"]["expected"]["win_odds"]
            if abs(tot - 1.0) > 0.04:
                bad_sum.append(f"{m['home']} vs {m['away']} = {tot:.2f}")
    _chk(r, not bad_band, FAIL,
         f"win odds rise from floor to ceiling {bad_band or ''}")
    _chk(r, not bad_sum, FAIL,
         f"both sides' odds at their medians sum to ~100% {bad_sum or ''}")

    # boom + neither + bust = 1 for every named player
    bad_split, bad_range = [], []
    for m in ms:
        for side in ("home", "away"):
            for p in m.get(f"{side}_key_players", []):
                tri = [p.get("p_bust"), p.get("p_neither"), p.get("p_boom")]
                if all(x is not None for x in tri) and abs(sum(tri) - 1.0) > 0.02:
                    bad_split.append(f"{p['name']} = {sum(tri):.2f}")
                if p.get("floor_p10") is not None and p.get("ceiling_p90") is not None \
                        and p["floor_p10"] > p["ceiling_p90"]:
                    bad_range.append(p["name"])
    _chk(r, not bad_split, FAIL, f"bust + neither + boom = 100% per player {bad_split or ''}")
    _chk(r, not bad_range, FAIL, f"player floor <= ceiling {bad_range or ''}")

    coin = [f"{m['home']} vs {m['away']}" for m in ms
            if abs(m["home_win_prob"] - 0.5) >= 0.04 and "coin flip" in md
            and f"{m['home']} {m['home_mean']:.0f} vs" in md
            and "coin flip" in md.split(m["home"])[-1][:200]]
    _chk(r, not coin, WARN, f"'coin flip' only on genuinely close games {coin or ''}")

    # a player must not be a key player for one team and criticised for the other
    seen = {}
    for m in ms:
        for side in ("home", "away"):
            for p in m.get(f"{side}_key_players", []):
                seen.setdefault(p["name"], set()).add("watch")
            for a in m.get(f"{side}_availability", []):
                seen.setdefault(a["player"], set()).add("dislike")
    clash = _contradictions(seen)
    _chk(r, not clash, WARN,
         "no player is both a key player and an injury concern in the same preview"
         + (f" — {[c['player'] for c in clash]}" if clash else ""))

    if live_rosters:
        wrong = []
        for m in ms:
            for side, key in (("home", "home_roster_id"), ("away", "away_roster_id")):
                owned = live_rosters.get(m[key], set())
                for p in m.get(f"{side}_key_players", []):
                    if p.get("player_id") and p["player_id"] not in owned:
                        wrong.append(f"{m[side]}/{p['name']}")
        _chk(r, not wrong, FAIL, f"named players are on the roster they're credited to {wrong or ''}")

    fails = [x for x in r if x["status"] == FAIL]
    return {"document": "matchup predictions", "checks": r,
            "failed": len(fails), "warned": len([x for x in r if x["status"] == WARN]),
            "verdict": "DO NOT SEND" if fails else "OK to send",
            "contradictions": clash}


def render(reports: list[dict]) -> str:
    out = ["# Validation report\n"]
    for rep in reports:
        icon = "❌" if rep["failed"] else ("⚠️" if rep["warned"] else "✅")
        out.append(f"## {icon} {rep['document']} — {rep['verdict']}")
        out.append(f"*{rep['failed']} failed, {rep['warned']} warnings, "
                   f"{len(rep['checks'])} checks run*\n")
        for c in rep["checks"]:
            mark = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}[c["status"]]
            out.append(f"- {mark} {c['check']}")
        if rep.get("contradictions"):
            out.append("\n**Contradictions found:**")
            for c in rep["contradictions"]:
                out.append(f"- **{c['player']}** appears as {', '.join(c['positive'])} "
                           f"and as {', '.join(c['negative'])}")
        out.append("")
    return "\n".join(out)
