"""Monte Carlo the league week, with availability resolved by lineup state.

WHAT CHANGED AND WHY IT MATTERED
The previous version multiplied an injured player's score by a Bernoulli. That
means when he sits, his roster slot scores ZERO -- as if the manager stared at an
empty lineup card. Real managers sub. So the old model punished exactly the teams
that are best insulated against injury (deep bench, handcuffs held) and produced a
league table that was largely a ranking of who had the most Questionable tags.

Now: availability is resolved as a LINEUP STATE. For each combination of who's in
and who's out, the lineup is re-solved with the best legal replacement promoted
from the bench, and the simulation draws states in proportion to their probability.
A team starting a doubtful RB with a good handcuff behind him barely moves. A team
starting him with nothing behind him falls off a cliff. That distinction is the
whole point.
"""
from __future__ import annotations
import itertools
import numpy as np
from scipy.stats import norm
from . import priors as PR

FLEX_OK = {"RB", "WR", "TE"}
MAX_ENUM = 6            # 2^6 = 64 lineup states per team

# Boom / bust / neither band, measured on 14,532 player-weeks 2023-25.
# +-24% of projection captures only 27.8% of weeks -- the SMALLEST of the three
# buckets, not the dominant one. Bust is the most common outcome at that width
# (39.9%), because projections run ~9% hot at the median (median actual/proj =
# 0.914) and the distribution is right-skewed. +-45% is where "neither" first
# clears 50% league-wide. See data/outcome_bands.json.
OUTCOME_BAND = 0.45


def _tier(proj, pos, pools):
    c = [(k, pools[k]) for k in pools if k.startswith(pos + "|")]
    if not c:
        return None
    for k, p in sorted(c, key=lambda kv: -kv[1]["ppg_range"][1]):
        if proj >= p["ppg_range"][0]:
            return k
    return sorted(c, key=lambda kv: kv[1]["ppg_range"][0])[0][0]


def _corr_matrix(players, corr):
    n = len(players)
    R = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = players[i], players[j]
            r = 0.0
            if a["nfl_team"] and a["nfl_team"] == b["nfl_team"]:
                r = corr.get("same_team|" + "|".join(sorted([a["pos"], b["pos"]])),
                             {}).get("r", 0.0)
            elif a["nfl_team"] and a["nfl_team"] == b.get("opponent"):
                if b["pos"] == "DEF":
                    r = corr.get(f"vs_opp_def|{a['pos']}|DEF", {}).get("r", 0.0)
                elif a["pos"] == "DEF":
                    r = corr.get(f"vs_opp_def|{b['pos']}|DEF", {}).get("r", 0.0)
            R[i, j] = R[j, i] = r
    w, V = np.linalg.eigh(R)
    if w.min() < 1e-8:
        R = V @ np.diag(np.clip(w, 1e-8, None)) @ V.T
        d = np.sqrt(np.diag(R))
        R = R / np.outer(d, d)
    return R


# ------------------------------------------------------------------ lineups
def resolve_lineup(starters, slots, out_idx, bench, proj, inj, pos_of):
    """Re-solve the lineup around the players who are out.

    Two passes, because that is what a manager actually does:
      1. Re-seat the AVAILABLE original starters optimally. If the RB1 sits, the
         FLEX running back slides up to RB -- nobody leaves a slot empty while a
         legal body sits in a lesser one.
      2. Fill whatever is still open from the bench, best projection first.

    Pass 1 deliberately cannot bench a healthy starter: we are covering holes, not
    second-guessing the manager's lineup. Bench players below 50% to play are not
    used as cover, since nobody plugs one questionable body in for another. If the
    pool runs dry the slot goes dark, which is the honest answer for a roster with
    no depth at that position.
    """
    avail_starters = [p for i, p in enumerate(starters) if i not in out_idx]
    order = [i for i, s in enumerate(slots) if s != "FLEX"] + \
            [i for i, s in enumerate(slots) if s == "FLEX"]

    lineup = [None] * len(slots)
    pool = sorted([p for p in avail_starters if p in proj],
                  key=lambda p: -proj[p])
    used = set()
    for i in order:                                   # pass 1: re-seat starters
        ok = FLEX_OK if slots[i] == "FLEX" else {slots[i]}
        pick = next((p for p in pool if p not in used and pos_of.get(p) in ok), None)
        if pick:
            lineup[i] = pick
            used.add(pick)

    cands = sorted([b for b in bench
                    if b in proj and inj.get(b, {}).get("p_play", 1.0) >= 0.5],
                   key=lambda b: -(proj[b] * inj.get(b, {}).get("p_play", 1.0)))
    for i in order:                                   # pass 2: fill from bench
        if lineup[i] is not None:
            continue
        ok = FLEX_OK if slots[i] == "FLEX" else {slots[i]}
        pick = next((b for b in cands if b not in used and pos_of.get(b) in ok), None)
        lineup[i] = pick
        if pick:
            used.add(pick)
    return lineup


def lineup_states(starters, slots, bench, proj, inj, pos_of, max_enum=MAX_ENUM):
    """[(probability, lineup)] over every meaningful in/out combination."""
    unc = [(i, p) for i, p in enumerate(starters)
           if inj.get(p, {}).get("p_play", 1.0) < 0.999]
    unc.sort(key=lambda x: abs(inj[x[1]]["p_play"] - 0.5))   # most uncertain first
    enum, deferred = unc[:max_enum], [p for _, p in unc[max_enum:]]

    states = []
    for bits in itertools.product([0, 1], repeat=len(enum)):
        prob, out_idx = 1.0, set()
        for b, (i, pid) in zip(bits, enum):
            p = inj[pid]["p_play"]
            if b:
                prob *= (1 - p); out_idx.add(i)
            else:
                prob *= p
        if prob < 1e-5:
            continue
        states.append((prob, resolve_lineup(starters, slots, out_idx, bench,
                                            proj, inj, pos_of)))
    if not states:
        states = [(1.0, list(starters))]
    tot = sum(p for p, _ in states)
    return [(p / tot, l) for p, l in states], deferred


def substitution_plan(starters, slots, bench, proj, inj, pos_of, name_of):
    """Per uncertain starter: who actually replaces him, and what it costs.

    This is the number that answers 'how worried should I be' -- not the player's
    projection, but the DROP from him to his replacement.
    """
    rows = []
    for i, pid in enumerate(starters):
        rec = inj.get(pid, {})
        if rec.get("p_play", 1.0) >= 0.999:
            continue
        base = sum(proj.get(p, 0.0) for p in starters if p in proj)
        after = resolve_lineup(starters, slots, {i}, bench, proj, inj, pos_of)
        lost = base - sum(proj.get(p, 0.0) for p in after if p)
        entered = [p for p in after if p and p not in starters]
        sub = entered[0] if entered else None
        sp = proj.get(pid, 0.0) * rec.get("haircut", 1.0) - lost
        rows.append({
            "player": name_of(pid), "pos": pos_of.get(pid), "slot": slots[i],
            "designation": rec.get("designation"),
            "practice": rec.get("practice"),
            "p_play": round(rec.get("p_play", 1.0), 3),
            "rate_fitted": rec.get("fitted", False),
            "rate_n": rec.get("rate_n"),
            "source": rec.get("source"),
            "note": (rec.get("note") or "")[:160],
            "proj_if_plays": round(proj.get(pid, 0.0) * rec.get("haircut", 1.0), 1),
            "replacement": name_of(sub) if sub else "NOBODY ELIGIBLE",
            "replacement_proj": round(max(sp, 0.0), 1),
            "dropoff": round(lost, 1),
        })
    return sorted(rows, key=lambda r: -r["dropoff"] * (1 - r["p_play"]))


# ------------------------------------------------------------------ simulate
def simulate_week(teams, proj, pos_of, nfl_team, opponent, inj, slots,
                  n_sims=25_000, seed=1, mode="full"):
    """teams: {rid: {"team", "starters" (slot-aligned), "players"}}

    mode="full"     as the week actually stands: tagged players carry their play
                    probability, and the outcome pools include games players left
                    injured mid-game.
    mode="healthy"  everyone suits up and finishes. All play probabilities forced
                    to 1.0, haircuts dropped, and outcome pools censored of
                    early-exit games. This is pure performance variance -- the
                    range if nothing new goes wrong.
    """
    rng = np.random.default_rng(seed)
    P = PR.load()
    corr = P["correlations"]
    pools = P["pools"]
    # Pools are already censored of first-half exits, so "healthy" now differs
    # only in forcing every tagged player to suit up. Second-half exits stay in
    # both -- a Q3 hamstring is a real fantasy outcome you have to live with.
    if mode == "healthy":
        inj = {k: {**v, "p_play": 1.0, "haircut": 1.0} for k, v in inj.items()}

    # every rostered player needs a draw -- bench players can enter lineups
    idx, plist = {}, []
    for t in teams.values():
        for pid in t["players"]:
            if pid in idx or pid not in proj:
                continue
            idx[pid] = len(plist)
            plist.append({"id": pid, "pos": pos_of.get(pid) or "WR",
                          "nfl_team": nfl_team.get(pid), "opponent": opponent.get(pid)})
    n = len(plist)
    U = norm.cdf(np.linalg.cholesky(_corr_matrix(plist, corr))
                 @ rng.standard_normal((n, n_sims)))

    # lineup states first, so we know who is enumerated (no Bernoulli) vs not
    states, deferred = {}, set()
    for rid, t in teams.items():
        bench = [p for p in t["players"] if p not in t["starters"]]
        st, dfr = lineup_states(t["starters"], slots, bench, proj, inj, pos_of)
        states[rid] = st
        deferred |= set(dfr)
    enumerated = {p for rid, t in teams.items()
                  for p in t["starters"]
                  if inj.get(p, {}).get("p_play", 1.0) < 0.999} - deferred

    scores = np.zeros((n, n_sims))
    for i, p in enumerate(plist):
        pid = p["id"]
        key = _tier(proj[pid], p["pos"], pools)
        ratios = np.array(pools[key]["ratios"]) if key else np.array([1.0])
        rec = inj.get(pid, {})
        draw = proj[pid] * np.quantile(ratios, U[i], method="linear") \
            * rec.get("haircut", 1.0)
        if pid not in enumerated:
            pp = rec.get("p_play", 1.0)
            if pp < 1.0:
                draw = draw * (rng.random(n_sims) < pp)
        scores[i] = draw
    scores = np.maximum(scores, -3.0)

    groups = []
    for sl in slots:
        if sl not in groups:
            groups.append(sl)

    totals, used_states, slot_tot = {}, {}, {}
    for rid, st in states.items():
        probs = np.array([p for p, _ in st])
        pick = rng.choice(len(st), size=n_sims, p=probs)
        tot = np.zeros(n_sims)
        gt = {g: np.zeros(n_sims) for g in groups}
        for si, (_, lineup) in enumerate(st):
            m = pick == si
            if not m.any():
                continue
            cols = np.flatnonzero(m)
            rows = [idx[x] for x in lineup if x in idx]
            tot[m] = scores[np.ix_(rows, cols)].sum(axis=0)
            for g in groups:
                r = [idx[x] for sl, x in zip(slots, lineup)
                     if sl == g and x in idx]
                if r:
                    gt[g][m] = scores[np.ix_(r, cols)].sum(axis=0)
        totals[rid] = tot
        slot_tot[rid] = gt
        used_states[rid] = len(st)
    return {"scores": scores, "idx": idx, "totals": totals, "n_sims": n_sims,
            "players": plist, "n_states": used_states, "slots": slot_tot,
            "groups": groups, "states": states}


def matchup_results(sim, pairs, teams):
    out = []
    for a, b in pairs:
        ta, tb = sim["totals"][a], sim["totals"][b]
        marg = ta - tb
        out.append({
            "home_roster_id": a, "away_roster_id": b,
            "home": teams[a]["team"], "away": teams[b]["team"],
            "home_win_prob": round(float((ta > tb).mean()), 4),
            "home_mean": round(float(ta.mean()), 1),
            "away_mean": round(float(tb.mean()), 1),
            "home_range_80": [round(float(np.percentile(ta, 10)), 1),
                              round(float(np.percentile(ta, 90)), 1)],
            "away_range_80": [round(float(np.percentile(tb, 10)), 1),
                              round(float(np.percentile(tb, 90)), 1)],
            "median_margin": round(float(np.median(marg)), 1),
            "p_blowout_30": round(float((np.abs(marg) > 30).mean()), 3),
            "p_nailbiter_5": round(float((np.abs(marg) < 5).mean()), 3),
        })
    return sorted(out, key=lambda m: abs(m["home_win_prob"] - 0.5))


def leverage(sim, pairs, teams, name_of, pos_of, raw_proj=None):
    """swing = P(win | player top quartile) - P(win | bottom quartile).

    sleeper_proj is Rotowire's raw number as served by Sleeper. sim_mean is what
    the simulation actually centres on, which is lower for anyone carrying an
    injury haircut. Showing both makes the adjustment visible instead of hiding
    it inside the model.
    """
    S, idx = sim["scores"], sim["idx"]
    raw_proj = raw_proj or {}
    out = {}
    for a, b in pairs:
        for me, opp in ((a, b), (b, a)):
            won = sim["totals"][me] > sim["totals"][opp]
            rows = []
            for pid in teams[me]["starters"]:
                if pid not in idx:
                    continue
                v = S[idx[pid]]
                bad, good = v <= np.percentile(v, 25), v >= np.percentile(v, 75)
                if bad.sum() < 50 or good.sum() < 50:
                    continue
                rows.append({
                    "name": name_of(pid), "pos": pos_of.get(pid),
                    "sleeper_proj": round(float(raw_proj.get(pid, 0.0)), 1),
                    "proj": round(float(v.mean()), 1),
                    "floor_p10": round(float(np.percentile(v, 10)), 1),
                    "ceiling_p90": round(float(np.percentile(v, 90)), 1),
                    "p_win_if_bust": round(float(won[bad].mean()), 3),
                    "p_win_if_boom": round(float(won[good].mean()), 3),
                    "swing": round(float(won[good].mean() - won[bad].mean()), 3),
                    "p_dud_under_5": round(float((v < 5).mean()), 3),
                    **_outcome_split(v, raw_proj.get(pid, 0.0))})
            rows.sort(key=lambda r: -r["swing"])
            out[me] = rows
    return out


def _outcome_split(v, proj, band=OUTCOME_BAND):
    """Bust / neither / boom against the player's own projection.

    One fixed band for everyone, deliberately. Tier-specific bands would pin
    "neither" near 50% for every player by construction and throw away the very
    thing worth reporting -- with a common band, a high "neither" IS the safety
    measure, and a low one flags a coin-flip start.
    """
    if not proj or proj <= 0:
        return {"p_bust": None, "p_neither": None, "p_boom": None,
                "band": band}
    lo, hi = proj * (1 - band), proj * (1 + band)
    return {"p_bust": round(float((v < lo).mean()), 3),
            "p_neither": round(float(((v >= lo) & (v <= hi)).mean()), 3),
            "p_boom": round(float((v > hi).mean()), 3),
            "band": band}


def team_profile(sim, teams):
    out = []
    for rid, t in teams.items():
        v = sim["totals"][rid]
        out.append({"roster_id": rid, "team": t["team"],
                    "mean": round(float(v.mean()), 1),
                    "floor_p10": round(float(np.percentile(v, 10)), 1),
                    "ceiling_p90": round(float(np.percentile(v, 90)), 1),
                    "sd": round(float(v.std()), 1),
                    "lineup_states": sim["n_states"].get(rid, 1),
                    "p_over_140": round(float((v > 140).mean()), 3),
                    "p_under_95": round(float((v < 95).mean()), 3)})
    return sorted(out, key=lambda x: -x["mean"])


def all_play(sim, teams):
    rids = list(teams)
    M = np.stack([sim["totals"][r] for r in rids])
    ranks = (M[:, None, :] > M[None, :, :]).sum(axis=1)
    return {rids[i]: round(float(ranks[i].mean()) / (len(rids) - 1), 4)
            for i in range(len(rids))}


def win_odds_by_band(sim, a, b, teams):
    """Win odds if a team lands at its floor, its median, or its ceiling.

    The two team totals are near-independent, so P(I win | I score X) is simply
    P(opponent scores under X). That makes this exact rather than a conditional
    estimate: read the opponent's CDF at your own percentile.

    Answers the two questions a preview should: am I dead if I hit my floor, and
    is my ceiling even enough?
    """
    out = {}
    for me, opp in ((a, b), (b, a)):
        v, ov = sim["totals"][me], sim["totals"][opp]
        row = {}
        for label, pct in (("floor", 10), ("expected", 50), ("ceiling", 90)):
            score = float(np.percentile(v, pct))
            row[label] = {"score": round(score, 1),
                          "win_odds": round(float((ov < score).mean()), 3)}
        out[me] = row
    return out


def magic_numbers(sim, a, b, teams):
    """The score each side needs to hit.

    Since the two totals are near-independent, P(I win | I score T) = P(opponent
    scores under T). So the number you need for a 75% win rate is simply the
    opponent's 75th percentile. More useful in a preview than a conditional mean:
    upsets come from nine players each moving a little, so no single player's
    conditional average looks dramatic even when the upset is live.
    """
    out = {}
    for me, opp in ((a, b), (b, a)):
        v, ov = sim["totals"][me], sim["totals"][opp]
        out[me] = {
            "to_win_50": round(float(np.percentile(ov, 50)), 1),
            "to_win_75": round(float(np.percentile(ov, 75)), 1),
            "to_win_90": round(float(np.percentile(ov, 90)), 1),
            "p_reach_50": round(float((v > np.percentile(ov, 50)).mean()), 3),
            "p_reach_75": round(float((v > np.percentile(ov, 75)).mean()), 3),
            "own_median": round(float(np.median(v)), 1),
        }
    return out


# ------------------------------------------------------- matchup detail
def positional_edges(sim, a, b, teams):
    """Slot-group by slot-group: who wins it, by how much, how reliably.

    RB means both RB slots combined, because a matchup is decided by the pair,
    not by which one you happened to list first.
    """
    out = []
    for g in sim["groups"]:
        va, vb = sim["slots"][a][g], sim["slots"][b][g]
        wins = float((va > vb).mean())
        out.append({"slot": g,
                    "home_mean": round(float(va.mean()), 1),
                    "away_mean": round(float(vb.mean()), 1),
                    "edge": round(float(va.mean() - vb.mean()), 1),
                    "home_win_pct": round(wins, 3),
                    "decisive": abs(wins - 0.5) > 0.20})
    return sorted(out, key=lambda r: -abs(r["edge"]))


def path_to_victory(sim, a, b, teams, name_of, pos_of, top_n=4):
    """What actually has to happen for each side to win.

    Conditions every starter's score on the outcome and reports the gap. This is
    the question a preview should answer -- not "who is projected higher" but
    "in the worlds where the underdog wins, what went differently".
    """
    S, idx = sim["scores"], sim["idx"]
    won_a = sim["totals"][a] > sim["totals"][b]
    out = {}
    for me, mask, label in ((a, won_a, "wins"), (b, ~won_a, "wins")):
        rows = []
        for pid in teams[me]["starters"]:
            if pid not in idx:
                continue
            v = S[idx[pid]]
            overall, given = float(v.mean()), float(v[mask].mean())
            rows.append({"name": name_of(pid), "pos": pos_of.get(pid),
                         "overall": round(overall, 1),
                         "when_team_wins": round(given, 1),
                         "delta": round(given - overall, 1)})
        rows.sort(key=lambda r: -r["delta"])
        out[me] = {"win_prob": round(float(mask.mean()), 3),
                   "needs": rows[:top_n],
                   "opponent_must_fade": None}
    # what the opponent has to be held to
    for me, opp, mask in ((a, b, won_a), (b, a, ~won_a)):
        rows = []
        for pid in teams[opp]["starters"]:
            if pid not in idx:
                continue
            v = S[idx[pid]]
            rows.append({"name": name_of(pid), "pos": pos_of.get(pid),
                         "overall": round(float(v.mean()), 1),
                         "when_they_lose": round(float(v[mask].mean()), 1),
                         "delta": round(float(v[mask].mean() - v.mean()), 1)})
        rows.sort(key=lambda r: r["delta"])
        out[me]["opponent_must_fade"] = rows[:top_n]
    return out
