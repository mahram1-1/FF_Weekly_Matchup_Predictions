"""One verdict per player. Pros and cons cannot overlap, by construction.

The obvious way to build a pros-and-cons column is to run several independent
filters -- high ceiling, high bust rate, injury risk, draft reach -- and print
whatever each returns. That reliably produces a document praising Bijan's ceiling
four lines above a warning about his bust rate, because both are true and the
filters never spoke to each other.

So instead every player is scored once on every signal, the strongest signal wins,
and the player lands in exactly one bucket with exactly one reason. A validator
can then assert disjointness rather than hope for it.

Signal strength is the absolute z-like magnitude of the thing being flagged, so a
player who is mildly safe but wildly injury-prone is filed as a con, which is what
a reader needs to know.
"""
from __future__ import annotations

SKILL = {"QB", "RB", "WR", "TE"}


def _signals(pl, lev, inj, pick, league):
    """[(verdict, strength, reason)] -- every true thing about this player."""
    out = []
    pos = lev.get("pos")
    neither = lev.get("p_neither")
    boom, bust = lev.get("p_boom"), lev.get("p_bust")
    ceil, proj = lev.get("ceiling_p90"), (lev.get("sleeper_proj") or lev.get("proj") or 0)

    # --- availability. Strongest con available: a body that may not play.
    if inj and inj.get("p_play", 1.0) < 0.90 and inj.get("dropoff", 0) >= 4:
        out.append(("con", 3.0 + inj["dropoff"] / 10,
                    f"{inj['designation']}, {inj['p_play']:.0%} to play — the lineup "
                    f"loses {inj['dropoff']:.1f} if he sits"))
    if inj and inj.get("replacement") == "NOBODY ELIGIBLE":
        out.append(("con", 4.0,
                    f"nothing eligible behind him at {inj.get('slot')} — that slot "
                    f"scores zero if he sits"))

    # --- volatility, skill positions only. Every DEF sits near 46% neither and
    #     every K near 55%; that is the position, not the player.
    if pos in SKILL and neither is not None:
        if neither < 0.55:
            out.append(("con", 2.0 + (0.55 - neither) * 8,
                        f"lands inside the band only {neither:.0%} of the time "
                        f"against a {bust:.0%} bust rate"))
        elif neither >= 0.70:
            out.append(("pro", 1.5 + (neither - 0.70) * 8,
                        f"one of the safest starts in the league — inside the band "
                        f"{neither:.0%} of the time"))

    # --- ceiling, in points above the projection rather than as a rate
    if pos in SKILL and ceil and proj:
        over = ceil - proj
        if over >= 12:
            out.append(("pro", 1.5 + over / 12,
                        f"a {ceil:.0f}-point ceiling, {over:+.0f} over his "
                        f"projection, and he booms {boom:.0%} of the time"))

    # --- draft outcome
    if pick:
        mv = pick.get("pos_rank_move")
        if mv is not None and mv <= -8:
            out.append(("con", 1.0 + abs(mv) / 15,
                        f"taken at {pick['pick']} and has slid from "
                        f"{pick['pos_rank_drafted']} to {pick['pos_rank_now']} at "
                        f"his position"))
        if mv is not None and mv >= 8 and pick.get("round", 0) >= 6:
            out.append(("pro", 1.0 + mv / 15,
                        f"round {pick['round']} pick who has climbed from "
                        f"{pick['pos_rank_drafted']} to {pick['pos_rank_now']} at "
                        f"his position"))
        if pick.get("reach") is not None and pick["reach"] >= 12 and (mv or 0) < 0:
            out.append(("con", 1.0 + pick["reach"] / 40,
                        f"a {pick['reach']:.0f}-slot reach at pick {pick['pick']} "
                        f"that has not paid off yet"))
    return out


def classify(leverage, availability, picks, rostered, starters) -> dict:
    """name -> {verdict, strength, reason}. At most one entry per player."""
    inj = {a["player"]: a for a in availability}
    pk = {p["player"]: p for p in picks if p["player_id"] in rostered}
    out = {}
    for l in leverage:
        name = l["name"]
        sig = _signals(name, l, inj.get(name), pk.get(name), None)
        if not sig:
            continue
        verdict, strength, reason = max(sig, key=lambda s: s[1])
        out[name] = {"verdict": verdict, "strength": round(strength, 2),
                     "reason": reason, "pos": l.get("pos"),
                     "proj": l.get("sleeper_proj") or l.get("proj"),
                     "floor": l.get("floor_p10"), "ceiling": l.get("ceiling_p90"),
                     "p_neither": l.get("p_neither"), "swing": l.get("swing")}
    return out


def team_notes(t, league) -> dict:
    """Team-level pros and cons, separate from any individual player."""
    s, h = t["sim"], t["sim_healthy"]
    pros, cons = [], []

    if t["team"] == league.get("highest_ceiling"):
        pros.append(f"highest ceiling in the league at {s['ceiling_p90']:.0f}")
    if t["team"] == league.get("highest_floor"):
        pros.append(f"the safest floor in the league at {s['floor_p10']:.0f}")
    if s["all_play_pct"] >= 0.58:
        pros.append(f"beats a random opponent {s['all_play_pct']:.0%} of the time")
    if t["injury_tax"]["mean"] >= -0.5:
        pros.append("nothing meaningful on the injury report")

    if t["team"] == league.get("most_volatile"):
        cons.append(f"the widest range in the league, {s['sd']:.0f} points of swing")
    if t["injury_tax"]["mean"] <= -3:
        cons.append(f"paying {abs(t['injury_tax']['mean']):.0f} points to the injury "
                    f"report — this reads {h['mean']:.0f} if everyone suits up")
    if s["all_play_pct"] <= 0.42:
        cons.append(f"beats a random opponent only {s['all_play_pct']:.0%} of the time")
    gone = (t.get("pundit") or {}).get("cut") or []
    if gone:
        c = gone[0]
        cons.append(f"round {c['round']} pick {c['player']} is already off the roster")
    return {"pros": pros, "cons": cons}
