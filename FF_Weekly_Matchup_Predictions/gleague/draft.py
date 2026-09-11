"""Draft value: what each pick cost versus what it's worth now.

Two independent yardsticks, because they answer different questions and
disagreeing with each other is itself informative.

REACH VS MARKET  pick number minus Sleeper's PPR ADP. Purely "did he pay above
                 the room". A +25 reach means he took a player 25 slots earlier
                 than the market. This is a fact about the draft, not a judgement
                 about the player.

VALUE VS NOW     the player's current rest-of-season projection rank at his
                 position, against the positional rank his draft slot implies.
                 This is retrospective and moves every week as projections do.

A player can reach badly and still be right (the market was wrong). A player can
draft at market and be wrong (everyone was wrong together). Reporting both keeps
the commentary honest instead of just relabelling ADP as insight.
"""
from __future__ import annotations
import collections
import urllib.request, json


def picks(draft_id: str) -> list[dict]:
    with urllib.request.urlopen(
            f"https://api.sleeper.app/v1/draft/{draft_id}/picks", timeout=60) as r:
        return json.load(r)


def analyse(draft_id: str, ros_proj: dict, adp: dict, pos_of: dict,
            name_of) -> dict[int, list[dict]]:
    """roster_id -> [pick rows], each with reach and current-value fields."""
    rows = picks(draft_id)

    # positional rank by current rest-of-season projection
    by_pos = collections.defaultdict(list)
    for pid, v in ros_proj.items():
        p = pos_of.get(pid)
        if p:
            by_pos[p].append((v, pid))
    now_rank = {}
    for p, lst in by_pos.items():
        for i, (_, pid) in enumerate(sorted(lst, reverse=True), 1):
            now_rank[pid] = i

    # positional rank implied by draft order (Nth QB off the board, etc.)
    taken = collections.Counter()
    draft_pos_rank = {}
    for r in sorted(rows, key=lambda x: x["pick_no"]):
        pos = (r.get("metadata") or {}).get("position")
        if pos:
            taken[pos] += 1
            draft_pos_rank[r["player_id"]] = taken[pos]

    out = collections.defaultdict(list)
    for r in rows:
        pid = r["player_id"]
        pos = (r.get("metadata") or {}).get("position") or pos_of.get(pid)
        pick = r["pick_no"]
        a = adp.get(pid)
        dpr, npr = draft_pos_rank.get(pid), now_rank.get(pid)
        out[r["roster_id"]].append({
            "player": name_of(pid), "player_id": pid, "pos": pos,
            "pick": pick, "round": r["round"],
            "adp": round(a, 1) if a else None,
            "reach": round(pick - a, 1) if a else None,
            "pos_rank_drafted": dpr,
            "pos_rank_now": npr,
            "pos_rank_move": (dpr - npr) if (dpr and npr) else None,
            "ros_proj": round(ros_proj.get(pid, 0.0), 1),
        })
    for rid in out:
        out[rid].sort(key=lambda x: x["pick"])
    return dict(out)


def commentary(team_picks: list[dict], starters: set, leverage: list[dict],
               availability: list[dict], reach_cut=12, slide_cut=8) -> dict:
    """Turn the numbers into the buckets a pundit column actually needs."""
    SKILL = {"QB", "RB", "WR", "TE"}
    lev = {l["name"]: l for l in leverage}
    inj = {a["player"]: a for a in availability}

    # Watch prefers skill positions. A defence can genuinely swing a week, but
    # "players to watch: the Chargers" is not a sentence, and DEF/K leverage rows
    # stay available in the facts packet for anyone who wants them.
    skill_lev = [l for l in leverage if l.get("pos") in SKILL]
    watch = (skill_lev if len(skill_lev) >= 3 else leverage)[:3]

    # reached hardest, and it hasn't paid off yet
    reaches = [p for p in team_picks
               if p["reach"] is not None and p["reach"] >= reach_cut]
    reaches.sort(key=lambda p: -p["reach"])

    # positional stock has fallen since draft day
    sliding = [p for p in team_picks
               if p["pos_rank_move"] is not None and p["pos_rank_move"] <= -slide_cut]
    sliding.sort(key=lambda p: p["pos_rank_move"])

    # dislike: a starter with a poor middle (volatile) or a real availability cloud
    # Volatility flags apply to skill positions only. Every defence lands inside
    # the band around 46% of the time and every kicker around 55% -- that is the
    # position, not the pick, and flagging it would just list all 24 of them.
    dislike = []
    for p in team_picks:
        if p["player_id"] not in starters:
            continue
        l, a = lev.get(p["player"]), inj.get(p["player"])
        if (p["pos"] in SKILL and l and l.get("p_neither") is not None
                and l["p_neither"] < 0.55):
            dislike.append({**p, "why": f"lands within the band only "
                                        f"{l['p_neither']:.0%} of the time, against "
                                        f"a {l['p_bust']:.0%} chance of busting",
                            "p_neither": l["p_neither"], "p_bust": l.get("p_bust")})
        elif a and a["p_play"] < 0.70 and a["dropoff"] >= 5:
            dislike.append({**p, "why": f"{a['p_play']:.0%} to play and the lineup "
                                        f"loses {a['dropoff']:.1f} if he sits",
                            "p_neither": None, "p_bust": None})
    dislike.sort(key=lambda p: (p["p_neither"] if p["p_neither"] is not None else 1))

    # Upside means a big ceiling, not a noisy one. Kickers and defences post high
    # boom shares purely because their baselines are tiny -- a 25% boom rate on a
    # 7-point projection is not upside, it is a rounding error with good PR.
    # Rank skill positions by ceiling above projection, in points.
    SKILL = {"QB", "RB", "WR", "TE"}
    cand = [l for l in leverage
            if l.get("pos") in SKILL and l.get("p_boom") is not None]
    for l in cand:
        l["ceiling_over_proj"] = round(l["ceiling_p90"] - (l.get("sleeper_proj")
                                                           or l["proj"]), 1)
    upside = sorted(cand, key=lambda l: -l["ceiling_over_proj"])[:3]
    risers = [p for p in team_picks
              if p["pos_rank_move"] is not None and p["pos_rank_move"] >= slide_cut
              and p["round"] >= 6]
    risers.sort(key=lambda p: -p["pos_rank_move"])

    # dedupe across the critical buckets so the same name isn't roasted twice
    seen = set()
    def _uniq(rows, key="player"):
        out = []
        for r in rows:
            n = r.get(key) or r.get("name")
            if n in seen:
                continue
            seen.add(n)
            out.append(r)
        return out

    dislike = _uniq(dislike)[:2]
    reaches = _uniq(reaches)[:2]
    sliding = _uniq(sliding)[:2]
    return {"watch": watch, "reaches": reaches, "sliding": sliding,
            "dislike": dislike, "upside": upside, "risers": risers[:2]}
