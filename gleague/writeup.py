"""Two documents, one facts packet.

  render_power_rankings(f)  -> the league-wide standings piece
  render_matchups(f)        -> the six previews, with projections

Both have a Claude counterpart that writes the same document with jokes. The rule
is unchanged and non-negotiable: the model receives a finished facts packet and
writes prose about it. It never computes anything. Every number in either
document traces to out/facts_wkNN.json.
"""
from __future__ import annotations
import json, os, urllib.request

_SHARED_RULES = """
HARD RULES — these matter more than the jokes:
1. Every number, name, injury and projection must come from the JSON facts packet.
   Invent nothing. No game results, news or transactions that aren't in the packet.
2. Probabilities are simulated frequencies, not predictions. "Wins 62% of the
   time", never "will win".
3. Floors and ceilings are 10th/90th percentiles of 25,000 simulated weeks. Team
   floor/ceiling includes injury risk; player floor/ceiling is conditional on him
   playing. Don't blur them.
4. Roast the teams, never the people. No jokes about anyone's real life.
5. Markdown. No preamble, no "here is your report".
"""

SYSTEM_POWER = """You write the weekly power rankings for a 12-team full-PPR \
fantasy league among friends. Voice: dry, confident, a little mean in the way \
group chats are mean. A beat writer who gave up on impartiality.
""" + _SHARED_RULES + """
FORMAT:
- Two-sentence cold open on the week's storyline.
- A section per team, in the order given by power_rankings.table, headed with rank,
  team name and record. You may argue with the ordering in prose but do NOT
  reorder. Under each, bullets drawn from that team's `pundit` object:
    * Watch — from pundit.watch. Quote the swing and the neither share.
    * Not buying — from pundit.dislike. The stated reason is in the `why` field.
    * Reached — from pundit.reaches. Pick number vs ADP is the joke.
    * Falling — from pundit.sliding. Positional rank on draft day vs now.
    * Upside — from pundit.upside. Ceiling above projection, in points.
    * The read — one line of your own, but only about numbers present in the packet.
- One-line sign-off.
NEVER give start/sit or roster advice. No "they should bench X", no "Y is a better
play". Describe what the simulation found, not what a manager ought to do.
On boom/bust: "neither" is the majority outcome for most players and that is the
point. A LOW neither share is the flag. Never imply a player is 70/30 to boom.
Reach is pick number minus ADP — a fact about the draft, not proof the pick was
wrong. Say which one you mean.
Under 1400 words."""

SYSTEM_MATCHUPS = """You write the weekly matchup previews for a 12-team \
full-PPR fantasy league among friends. Voice: dry and knowing, like a preview \
column that has watched these managers make the same mistakes for years.
""" + _SHARED_RULES + """
FORMAT — one section per matchup, ordered as given (closest first):
- Header with both teams, records, and projected totals with their 80% ranges.
- Two or three sentences of framing. Lead with the win probability and whether
  the healthy number disagrees — a game that's 62% as it stands but 52% if
  everyone plays is a coin flip wearing a disguise, and that IS the story.
- "Where it's won": pull 2-3 rows from positional_edges. Say who wins each slot
  group and how reliably.
- "The number to hit": use the magic field. "HYPE BEASTS need 120 for a coin flip
  and clear it 31% of the time" is the most readable stat in the packet.
- "What has to happen": use the path field. Note that the deltas are small by
  nature — an upset is nine players each doing a bit more, not one hero game. Say
  that plainly rather than overselling a +2.0. This is the best material you have —
  it says what actually changes in the worlds where each side wins, e.g. "in the
  38% where they win, Chase averages 24.1 instead of 17.8". Use it for the
  underdog especially.
- One line on any availability row that matters for this specific game.
Finish with a short "Game of the week" note on the closest matchup.
Under 1100 words."""


def _call(system: str, facts: dict, model="claude-sonnet-4-6") -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    body = json.dumps({"model": model, "max_tokens": 4000, "system": system,
                       "messages": [{"role": "user", "content":
                                     "Facts packet:\n\n" + json.dumps(facts, indent=1)}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
                                 headers={"content-type": "application/json",
                                          "x-api-key": key,
                                          "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=240) as r:
        return "".join(b.get("text", "") for b in json.load(r).get("content", []))


def power_with_claude(f):    return _call(SYSTEM_POWER, f)
def matchups_with_claude(f): return _call(SYSTEM_MATCHUPS, _matchup_slice(f))


def brief(f: dict) -> dict:
    """A compact facts packet for pasting into a Claude chat.

    The full packet is ~38k tokens, most of it the 180-pick draft list and the
    pundit buckets duplicated per team. This keeps everything either document
    actually renders and drops the raw inputs they were derived from. Nothing is
    recomputed -- it is a projection of the same numbers.
    """
    def team(t):
        return {"roster_id": t["roster_id"], "team": t["team"],
                "manager": t["manager"], "record": t["record"],
                "sim": t["sim"], "sim_healthy": t["sim_healthy"],
                "injury_tax": t["injury_tax"],
                "roster_strength_ros": t["roster_strength_ros"],
                "leverage": t["leverage"][:3],
                "availability": [a for a in t["availability"] if a["dropoff"] >= 3][:2],
                "pundit": {k: v[:2] for k, v in (t.get("pundit") or {}).items()}}

    ln = f["league_notes"]
    return {
        "league": f["league"],
        "method": {k: f["method"][k] for k in
                   ("n_sims", "seed", "scoring", "scoring_note", "outcome_band",
                    "injury_sources", "seasons_fit") if k in f["method"]},
        "power_rankings": f["power_rankings"],
        "teams": [team(t) for t in f["teams"]],
        "matchups": f["matchups"],
        "tier_reference": f.get("tier_reference"),
        "league_notes": {k: ln[k] for k in
                         ("highest_ceiling", "highest_floor", "most_volatile",
                          "closest_matchup", "no_cover") if k in ln},
    }


def _matchup_slice(f: dict) -> dict:
    """Trim the packet for the matchup doc so the model isn't reading rankings."""
    return {k: f[k] for k in ("league", "method", "matchups", "tier_reference")
            if k in f}


# ------------------------------------------------------------ share text
def render_chat(f: dict, repo_url: str = "") -> str:
    """Plain text sized for a league chat.

    The markdown documents are for reading on GitHub, where tables render. Pasted
    into Sleeper, GroupMe or iMessage they become pipe-delimited noise, and 21,000
    characters is far past what anyone scrolls. This is the same numbers in about
    2,000 characters with no markup at all.
    """
    L, out = f["league"], []
    tb = {t["roster_id"]: t for t in f["teams"]}

    out.append(f"{L['name'].upper()} — WEEK {L['week']}")
    out.append(f"{f['method']['n_sims']:,} simulated weeks, league scoring")
    out.append("")
    out.append("POWER RANKINGS")
    for r in f["power_rankings"]["table"]:
        t = tb[r["roster_id"]]; s_ = t["sim"]
        mv = r.get("move")
        arrow = "" if mv in (None, 0) else (f" (+{mv})" if mv > 0 else f" ({mv})")
        out.append(f"{r['rank']:>2}. {t['team']} {t['record']}{arrow} — "
                   f"{s_['mean']:.0f} proj, {s_['floor_p10']:.0f}-{s_['ceiling_p90']:.0f}")

    out.append("")
    out.append("MATCHUPS")
    for g in f["matchups"]:
        hp = g["home_win_prob"]
        tag = " — coin flip" if abs(hp - 0.5) < 0.04 else ""
        out.append(f"{g['home']} {g['home_mean']:.0f} vs {g['away']} "
                   f"{g['away_mean']:.0f} — {hp:.0%}/{1 - hp:.0%}{tag}")

    lev = f["league_notes"].get("top_leverage") or []
    if lev:
        out.append("")
        out.append("WHO DECIDES THE WEEK")
        for r in lev[:5]:
            out.append(f"{r['name']} ({r['pos']}, {r['team']}) — "
                       f"{r['floor_p10']:.0f}-{r['ceiling_p90']:.0f} range, "
                       f"swings their odds {r['swing']:+.0%}")

    ro = [a for t in f["teams"] for a in t.get("availability", [])
          if a["p_play"] < 0.9 and a["dropoff"] >= 4]
    if ro:
        out.append("")
        out.append("INJURY WATCH")
        for a in sorted(ro, key=lambda x: -x["dropoff"])[:5]:
            out.append(f"{a['player']} — {a['designation']}, {a['p_play']:.0%} to play")

    if repo_url:
        out.append("")
        out.append(f"Full breakdown: {repo_url}")
    return "\n".join(out)


# ------------------------------------------------------------ document 1
def _read(t, f):
    """Deterministic pundit lines. Every clause is backed by a number in the packet."""
    ln, bits = f["league_notes"], []
    s_, h, dr = t["sim"], t["sim_healthy"], t.get("draft") or []
    tax = t["injury_tax"]["mean"]

    if t["team"] == ln.get("highest_ceiling"):
        bits.append(f"highest ceiling in the league at {s_['ceiling_p90']:.0f}")
    if t["team"] == ln.get("highest_floor"):
        bits.append(f"the safest floor in the league at {s_['floor_p10']:.0f}")
    if t["team"] == ln.get("most_volatile"):
        bits.append(f"the widest range in the league, {s_['sd']:.0f} points of swing")

    if tax <= -8:
        bits.append(f"the heaviest injury load in the league — {abs(tax):.0f} points "
                    f"of projection sitting behind a designation, and the ranking "
                    f"would read {h['mean']:.0f} if everyone suited up")
    elif tax <= -3:
        bits.append(f"paying {abs(tax):.0f} points to the injury report")
    elif tax >= -0.5 and any(a["p_play"] < 1 for a in t.get("availability", [])) is False:
        bits.append("nothing on the injury report, which is its own kind of edge")

    ap = s_["all_play_pct"]
    if ap >= 0.58:
        bits.append(f"beats a random opponent {ap:.0%} of the time, so only the "
                    f"schedule can save anyone")
    elif ap <= 0.42:
        bits.append(f"beats a random opponent {ap:.0%} of the time")

    nc = [a for a in t.get("availability", [])
          if a["replacement"] == "NOBODY ELIGIBLE"]
    if nc:
        bits.append(f"nothing behind {nc[0]['player']} at {nc[0]['slot']} — that "
                    f"slot goes dark if he sits")

    if dr:
        reaches = [p for p in dr if p["reach"] is not None and p["reach"] > 0]
        total = sum(p["reach"] for p in reaches)
        if total >= 60:
            bits.append(f"drafted {total:.0f} slots ahead of market across "
                        f"{len(reaches)} picks — a room that trusted itself")
        elif reaches and total <= 15:
            bits.append("drafted almost exactly at market, for better or worse")
        risers = [p for p in dr if (p["pos_rank_move"] or 0) >= 12]
        if risers:
            r = max(risers, key=lambda p: p["pos_rank_move"])
            bits.append(f"{r['player']} has climbed {r['pos_rank_move']} spots at "
                        f"{r['pos']} since draft day, taken at {r['pick']}")

    vol = [l for l in t.get("leverage", [])
           if l.get("p_neither") is not None and l["p_neither"] < 0.50]
    if len(vol) >= 2:
        bits.append(f"{len(vol)} of their key starters land inside the band less "
                    f"than half the time — a coin-flip roster by construction")
    return bits


def render_power_rankings(f: dict) -> str:
    L, m, out = f["league"], f["method"], []
    band = m.get("outcome_band", 0.45)
    out.append(f"# {L['name']} — Week {L['week']} Power Rankings")
    out.append(f"*{L['teams']}-team {L['scoring']} · {m['n_sims']:,} simulated weeks "
               f"· priors fit on {'/'.join(m.get('seasons_fit') or [])}*\n")

    tb = {t["roster_id"]: t for t in f["teams"]}
    out.append("| # | ± | Team | Rec | Rating | Proj | Floor–Ceiling | All-play | Injury tax |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for r in f["power_rankings"]["table"]:
        t = tb[r["roster_id"]]; s_ = t["sim"]
        mv = r.get("move")
        arrow = "—" if mv in (None, 0) else (f"▲{mv}" if mv > 0 else f"▼{abs(mv)}")
        out.append(f"| {r['rank']} | {arrow} | {t['team']} | {t['record']} | "
                   f"{r['score']:.1f} | "
                   f"{s_['mean']:.0f} | {s_['floor_p10']:.0f}–{s_['ceiling_p90']:.0f} | "
                   f"{s_['all_play_pct']:.0%} | {t['injury_tax']['mean']:+.1f} |")
    out.append("")

    for r in f["power_rankings"]["table"]:
        t = tb[r["roster_id"]]
        s_, p = t["sim"], t.get("pundit") or {}
        mv = r.get("move")
        arrow = "" if mv in (None, 0) else (f" ▲{mv}" if mv > 0 else f" ▼{abs(mv)}")
        out.append(f"\n---\n\n### {r['rank']}. {t['team']} ({t['record']}){arrow}\n")
        out.append(f"*Projected {s_['mean']:.0f} · floor {s_['floor_p10']:.0f} · "
                   f"ceiling {s_['ceiling_p90']:.0f} · all-play {s_['all_play_pct']:.0%} "
                   f"· injury tax {t['injury_tax']['mean']:+.1f}*\n")

        for l in p.get("watch", [])[:3]:
            sp = l.get("sleeper_proj") or l["proj"]
            out.append(f"- **Watch — {l['name']} ({l['pos']}).** Projected {sp:.1f}, "
                       f"range {l['floor_p10']:.1f}–{l['ceiling_p90']:.1f}. Lands "
                       f"inside the band {l['p_neither']:.0%} of the time. Their win "
                       f"odds move {l['swing']:+.0%} between his bad weeks and his "
                       f"good ones — {l['p_win_if_bust']:.0%} if he busts, "
                       f"{l['p_win_if_boom']:.0%} if he booms.")

        for d in p.get("dislike", [])[:2]:
            out.append(f"- **Not buying — {d['player']} ({d['pos']}).** Taken at "
                       f"{d['pick']}; {d['why']}.")

        for rc in p.get("reaches", [])[:2]:
            mv = rc.get("pos_rank_move")
            if mv is not None and mv > 0:
                tail = (f"In fairness he's climbed to "
                        f"{_ord(rc['pos_rank_now'])} at the position from "
                        f"{_ord(rc['pos_rank_drafted'])}, so the reach is looking "
                        f"less silly than it did on the night.")
            elif mv is not None and mv < 0:
                tail = (f"He's since slipped from {_ord(rc['pos_rank_drafted'])} "
                        f"to {_ord(rc['pos_rank_now'])} at the position.")
            else:
                tail = (f"Still the {_ord(rc['pos_rank_now'])} {rc['pos']} by "
                        f"rest-of-season projection.")
            out.append(f"- **Reached — {rc['player']} ({rc['pos']}).** Pick "
                       f"{rc['pick']} against an ADP of {rc['adp']:.0f}, a "
                       f"{rc['reach']:.0f}-slot reach. {tail}")
        for sl in p.get("sliding", [])[:1]:
            out.append(f"- **Falling — {sl['player']} ({sl['pos']}).** Went "
                       f"{_ord(sl['pos_rank_drafted'])} at his position on draft "
                       f"day, now {_ord(sl['pos_rank_now'])}. That's "
                       f"{abs(sl['pos_rank_move'])} spots of slide before a snap "
                       f"was played.")

        for u in p.get("upside", [])[:2]:
            sp = u.get("sleeper_proj") or u["proj"]
            out.append(f"- **Upside — {u['name']} ({u['pos']}).** Projected {sp:.1f} "
                       f"with a {u['ceiling_p90']:.1f} ceiling, "
                       f"{u['ceiling_over_proj']:+.1f} above the number. Booms "
                       f"{u['p_boom']:.0%} of the time.")
        for rz in p.get("risers", [])[:1]:
            out.append(f"- **Late value — {rz['player']} ({rz['pos']}).** Round "
                       f"{rz['round']}, pick {rz['pick']}, and he's climbed from "
                       f"{_ord(rz['pos_rank_drafted'])} to "
                       f"{_ord(rz['pos_rank_now'])} at the position.")

        read = _read(t, f)
        if read:
            out.append(f"- **The read.** " + "; ".join(read).capitalize() + ".")

    out.append(f"\n---\n*Floor and ceiling are the 10th and 90th percentile of "
               f"25,000 simulated team totals. All-play is the share of simulations "
               f"a team beats a random opponent. Injury tax is the projection lost "
               f"to designations versus everyone suiting up. \"Inside the band\" "
               f"means within ±{band:.0%} of the Sleeper projection — the width at "
               f"which that becomes the majority outcome league-wide. Reach is pick "
               f"number minus Sleeper PPR ADP; positional ranks are by current "
               f"rest-of-season projection and move every week.*")
    out.append(f"\n*Companion document: Week {L['week']} matchup previews.*")
    return "\n".join(out)


def _ord(n):
    if not n:
        return "—"
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# ------------------------------------------------------------ document 2
def render_matchups(f: dict) -> str:
    """Opponents, key players, Sleeper projections, win odds at floor/ceiling."""
    L, m, out = f["league"], f["method"], []
    band = m.get("outcome_band", 0.45)
    out.append(f"# {L['name']} — Week {L['week']} Matchups")
    out.append(f"*{m['n_sims']:,} simulated weeks, scored with the league's own "
               f"settings. Ordered closest first.*\n")

    out.append("| Matchup | Projected | Win odds |")
    out.append("|---|---|---|")
    for g in f["matchups"]:
        out.append(f"| {g['home']} vs {g['away']} | "
                   f"{g['home_mean']:.0f}–{g['away_mean']:.0f} | "
                   f"{g['home_win_prob']:.0%} / {1 - g['home_win_prob']:.0%} |")

    for g in f["matchups"]:
        out.append(f"\n---\n\n## {g['home']} ({g['home_record']}) "
                   f"vs {g['away']} ({g['away_record']})\n")

        b = g.get("bands") or {}
        out.append("**Win odds by outcome**\n")
        out.append("| Team | If they hit their floor | Expected | If they hit their ceiling |")
        out.append("|---|---|---|---|")
        for side in ("home", "away"):
            k = b.get(side) or {}
            if not k:
                continue
            cells = " | ".join(
                f"{k[lab]['score']:.0f} → **{k[lab]['win_odds']:.0%}**"
                for lab in ("floor", "expected", "ceiling"))
            out.append(f"| {g[side]} | {cells} |")
        out.append(f"\n*Floor and ceiling are the 10th and 90th percentile of that "
                   f"team's simulated total. Win odds are the share of simulations "
                   f"the opponent finishes below that score.*")

        for side in ("home", "away"):
            kp = g.get(f"{side}_key_players") or []
            if not kp:
                continue
            out.append(f"\n**{g[side]} — players to watch**\n")
            out.append("| Player | Pos | Proj | Floor | Ceiling | Bust | Neither | Boom |")
            out.append("|---|---|---|---|---|---|---|---|")
            for r in kp:
                sp = r.get("sleeper_proj") or r["proj"]
                bu, ne, bo = r.get("p_bust"), r.get("p_neither"), r.get("p_boom")
                fmt = lambda x: f"{x:.0%}" if x is not None else "—"
                out.append(f"| {r['name']} | {r['pos']} | {sp:.1f} | "
                           f"{r['floor_p10']:.1f} | {r['ceiling_p90']:.1f} | "
                           f"{fmt(bu)} | **{fmt(ne)}** | {fmt(bo)} |")
            av = g.get(f"{side}_availability") or []
            for a in av[:2]:
                out.append(f"\n*{a['player']} is {a['designation']}, "
                           f"{a['p_play']:.0%} to play.*")

    out.append(f"\n---\n*Proj is Rotowire's component projection scored with THIS "
               f"league's settings, not Sleeper's stock pts_ppr. "
               f"Floor and ceiling are the 10th and 90th percentile of that player's "
               f"simulated week, conditional on him playing. Bust / neither / boom "
               f"are shares of simulations landing below, within, and above "
               f"±{band:.0%} of the Sleeper projection — a band measured on 14,532 "
               f"player-weeks from 2023-25 as the point where 'neither' first "
               f"becomes the majority outcome league-wide.*")
    out.append(f"\n*Companion document: Week {L['week']} power rankings.*")
    return "\n".join(out)
