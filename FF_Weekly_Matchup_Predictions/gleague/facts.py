"""Facts packet. Simulation-driven, snapshot-backed, LLM does zero arithmetic."""
from __future__ import annotations
from collections import defaultdict
from . import sleeper, priors, injuries, snapshot, sources, scoring, draft as DR, analytics as A, simulate as S


def build(league_id: str, week: int, cfg: dict, n_sims=25_000, replay=False) -> dict:
    lg = sleeper.league(league_id); season = lg["season"]
    slots = [s for s in lg["roster_positions"] if s != "BN"]
    tr = sleeper.team_index(league_id); P = sleeper.players()
    seed = snapshot.seed_for(league_id, season, week)

    def nm(pid):
        p = P.get(pid) or {}
        return p.get("full_name") or p.get("last_name") or pid

    pos_of = {pid: p.get("position") for pid, p in P.items()}
    nfl_team = {pid: p.get("team") for pid, p in P.items()}
    for t in tr.values():
        for pid in t["players"]:
            if not pid.isdigit():
                pos_of[pid], nfl_team[pid] = "DEF", pid

    snap, manifest = snapshot.load(season, week) if replay else (None, None)
    if snap:
        proj, opponent, inj = snap["projections"], snap["opponents"], snap["injuries"]
        print(f"  replaying snapshot from {manifest['captured_at']}")
    else:
        rows = sleeper.projections(season, week)
        # Score with THIS league's settings, not Sleeper's stock pts_ppr.
        proj = scoring.league_points(rows, lg["scoring_settings"])
        stock = {r["player_id"]: r["stats"]["pts_ppr"] for r in rows
                 if (r.get("stats") or {}).get("pts_ppr") is not None}
        scoring_delta = sorted(
            ({"player": nm(p), "pos": (pos_of.get(p) or "DEF"),
              "league": round(proj[p], 1), "stock": round(stock[p], 1),
              "delta": round(proj[p] - stock[p], 1)}
             for p in proj if p in stock),
            key=lambda d: d["delta"])
        opponent = {r["player_id"]: r.get("opponent") for r in rows}
        rostered = [p for t in tr.values() for p in t["players"]]
        inj = injuries.resolve(rostered, season, week, P, sources.crosswalk(),
                               use_espn=cfg.get("use_espn", True))

    # a rostered player with no projection but a real designation still needs an
    # entry, so the state machine can bench him rather than silently zero the slot
    for pid, rec in inj.items():
        if pid not in proj and rec.get("p_play", 1.0) > 0:
            rec["p_play"] = 0.0
            rec["note"] = (rec.get("note") or "") + " [no projection issued]"

    teams = {rid: {"team": t["team"],
                   "starters": [p if p in proj else None for p in t["starters"]],
                   "players": [p for p in t["players"] if p in proj]}
             for rid, t in tr.items()}
    for rid, t in teams.items():
        t["starters"] = [p for p in t["starters"] if p]

    priors.load(settings=lg["scoring_settings"])
    sim = S.simulate_week(teams, proj, pos_of, nfl_team, opponent, inj, slots,
                          n_sims=n_sims, seed=seed, mode="full")
    sim_h = S.simulate_week(teams, proj, pos_of, nfl_team, opponent, inj, slots,
                            n_sims=n_sims, seed=seed, mode="healthy")
    prof = {t["roster_id"]: t for t in S.team_profile(sim, teams)}
    prof_h = {t["roster_id"]: t for t in S.team_profile(sim_h, teams)}
    sim_ap = S.all_play(sim, teams)
    res_h = S.matchup_results(sim_h, [], teams)

    d = defaultdict(list)
    for m in sleeper.matchups(league_id, week):
        d[m.get("matchup_id")].append(m["roster_id"])
    pairs = [tuple(v) for v in d.values() if len(v) == 2]
    results = S.matchup_results(sim, pairs, teams)
    results_h = S.matchup_results(sim_h, pairs, teams)
    wp_h = {(m["home_roster_id"], m["away_roster_id"]): m["home_win_prob"]
            for m in results_h}
    for m in results:
        m["home_win_prob_healthy"] = wp_h.get(
            (m["home_roster_id"], m["away_roster_id"]), m["home_win_prob"])
    lev = S.leverage(sim, pairs, teams, nm, pos_of, raw_proj=proj)

    detail = {}
    for a, b in pairs:
        ptv = (S.path_to_victory(sim, a, b, teams, nm, pos_of)
               if cfg.get("matchup_detail") == "full" else None)
        bands = S.win_odds_by_band(sim, a, b, teams)
        d = {"bands": {"home": bands[a], "away": bands[b]}}
        if cfg.get("matchup_detail") == "full":
            mn = S.magic_numbers(sim, a, b, teams)
            d.update({"edges": S.positional_edges(sim, a, b, teams),
                      "path": {"home": ptv[a], "away": ptv[b]},
                      "magic": {"home": mn[a], "away": mn[b]}})
        detail[(a, b)] = d

    ros_rows = sleeper.projections(season, None)
    ros = scoring.league_points(ros_rows, lg["scoring_settings"])
    # season rows carry adp_ppr (redraft, full PPR). 999 is Sleeper's "undrafted".
    adp = {r["player_id"]: r["stats"]["adp_ppr"] for r in ros_rows
           if (r.get("stats") or {}).get("adp_ppr")
           and r["stats"]["adp_ppr"] < 999}

    try:
        dpicks = DR.analyse(lg["draft_id"], ros, adp, pos_of, nm)
    except Exception as e:
        print(f"  [draft] unavailable ({e})")
        dpicks = {}
    roster_strength = {rid: A.optimal_lineup(t["players"], ros, pos_of, slots)[0]
                       for rid, t in tr.items()}

    weekly = {}
    for w in range(1, week):
        try:
            s = {m["roster_id"]: m.get("points") or 0.0
                 for m in sleeper.matchups(league_id, w)}
            if any(s.values()):
                weekly[w] = s
        except Exception:
            pass
    power = A.power_rankings(tr, weekly, roster_strength, week, sim_allplay=sim_ap,
                             sim_mean={r: prof[r]["mean"] for r in prof})

    prev, _ = snapshot.load(season, week - 1) if week > 1 else (None, None)
    prev_rank = (prev or {}).get("rankings") or {}
    for row in power["table"]:
        pr = prev_rank.get(str(row["roster_id"]))
        row["prev_rank"] = pr
        row["move"] = (pr - row["rank"]) if pr else None

    blocks = []
    for rid, t in tr.items():
        starters = teams[rid]["starters"]
        bench = [p for p in teams[rid]["players"] if p not in starters]
        subs = S.substitution_plan(starters, slots, bench, proj, inj, pos_of, nm)
        # NOTE: no start/sit suggestions are produced. The substitution plan
        # describes what the SIMULATION does when a tagged player sits; it is not
        # advice to the manager and is not rendered in the rankings document.
        p_, ph = prof[rid], prof_h[rid]
        tp = dpicks.get(rid, [])
        pundit = DR.commentary(tp, set(starters), lev.get(rid, []), subs) if tp else {}
        blocks.append({
            "roster_id": rid, "team": t["team"], "manager": t["manager"],
            "record": f"{t['wins']}-{t['losses']}" + (f"-{t['ties']}" if t["ties"] else ""),
            "sim": {"mean": p_["mean"], "floor_p10": p_["floor_p10"],
                    "ceiling_p90": p_["ceiling_p90"], "sd": p_["sd"],
                    "lineup_states": p_["lineup_states"],
                    "p_over_140": p_["p_over_140"], "p_under_95": p_["p_under_95"],
                    "all_play_pct": sim_ap[rid]},
            "sim_healthy": {"mean": ph["mean"], "floor_p10": ph["floor_p10"],
                            "ceiling_p90": ph["ceiling_p90"], "sd": ph["sd"]},
            "injury_tax": {"mean": round(p_["mean"] - ph["mean"], 1),
                           "floor": round(p_["floor_p10"] - ph["floor_p10"], 1),
                           "ceiling": round(p_["ceiling_p90"] - ph["ceiling_p90"], 1)},
            "roster_strength_ros": roster_strength[rid],
            "leverage": lev.get(rid, [])[:4],
            "draft": tp,
            "pundit": pundit,
            "availability": subs,
            "no_cover": [s_ for s_ in subs if s_["replacement"] == "NOBODY ELIGIBLE"],
        })

    bl = {b["roster_id"]: b for b in blocks}
    for m in results:
        a, b_ = m["home_roster_id"], m["away_roster_id"]
        d = detail.get((a, b_)) or detail.get((b_, a)) or {}
        m["bands"] = d.get("bands", {})
        for opt in ("edges", "path", "magic"):
            if opt in d:
                m["positional_edges" if opt == "edges" else opt] = d[opt]
        for side, rid in (("home", a), ("away", b_)):
            t = bl[rid]
            m[f"{side}_key_players"] = t["leverage"][:3]
            m[f"{side}_availability"] = [v for v in t["availability"]
                                         if v["dropoff"] >= 3][:2]
            m[f"{side}_injury_tax"] = t["injury_tax"]["mean"]
            m[f"{side}_record"] = t["record"]

    all_lev = [dict(l, team=b["team"]) for b in blocks for l in b["leverage"]]
    all_lev.sort(key=lambda r: -r["swing"])
    all_subs = [dict(s_, team=b["team"]) for b in blocks for s_ in b["availability"]]
    src_mix = {}
    for r in inj.values():
        if r.get("designation"):
            src_mix[r["source"]] = src_mix.get(r["source"], 0) + 1

    facts = {
        "league": {"name": lg["name"], "id": league_id, "season": season,
                   "week": week, "teams": lg["total_rosters"],
                   "scoring": "full PPR", "slots": slots},
        "method": {
            "n_sims": n_sims, "seed": seed,
            "distributions": "empirical outcome ratios from 2025 weekly actuals, "
                             "tiered by position and projection level",
            "correlation": "Gaussian copula on measured 2025 correlations "
                           "(QB-WR +0.30, QB-oppDEF -0.34, DEF-K +0.25)",
            "availability": "lineup-state enumeration: every in/out combination is "
                            "re-solved with the best legal bench replacement "
                            "promoted, then drawn in proportion to its probability",
            "play_rates": "fitted from 1,831 fantasy-position player-weeks joining "
                          "the 2025 official NFL injury report to Sleeper's gp flag",
            "injury_sources": src_mix,
            "scoring": "league settings, not Sleeper's stock pts_ppr",
            "scoring_note": "skill positions match Sleeper default within 0.1 pt; "
                            "K is -0.3 on average; DEF is -2.3 and reorders, with "
                            "the good-to-bad spread 58% wider than stock",
            "scoring_delta_worst": (scoring_delta[:6] if not replay else None),
            "floor_ceiling": "10th and 90th percentile of the simulated team "
                             "total. Two variants are reported: 'as it stands' "
                             "includes each tagged player's chance of sitting and "
                             "the historical chance of leaving a game early; "
                             "'if everyone plays' forces all play probabilities to "
                             "1.0 and draws from outcome pools censored of "
                             "early-exit games (snap share below half the player's "
                             "own median).",
            "censoring": priors.load().get("censoring"),
            "outcome_band": S.OUTCOME_BAND,
            "outcome_band_basis": "measured on 14,532 player-weeks 2023-25; "
                                  "+-24% of projection covers only 27.8% of weeks "
                                  "(bust 39.9%, boom 32.2%), so 24% is the "
                                  "smallest bucket not the dominant one. +-45% is "
                                  "where 'neither' first clears 50% league-wide.",
            "seasons_fit": priors.load().get("seasons"),
        },
        "tier_reference": [
            {"pool": k, "pos": k.split("|")[0], "tier": v["tier"],
             "of_tiers": v["of_tiers"], "ppg_range": v["ppg_range"],
             "n_players": v["n_players"], "n_weeks": v["n_weeks"],
             "floor_p10": v["floor_p10"], "avg": v["avg"],
             "ceiling_p90": v["ceiling_p90"], "cv": v["cv"],
             "p_under_half": v["p_under_half"]}
            for k, v in sorted(priors.load()["pools"].items())
            if k.split("|")[0] in ("QB", "RB", "WR", "TE")],
        "power_rankings": power,
        "matchups": results,
        "teams": sorted(blocks, key=lambda b: -b["sim"]["mean"]),
        "league_notes": {
            "highest_ceiling": max(blocks, key=lambda b: b["sim"]["ceiling_p90"])["team"],
            "highest_floor": max(blocks, key=lambda b: b["sim"]["floor_p10"])["team"],
            "most_volatile": max(blocks, key=lambda b: b["sim"]["sd"])["team"],
            "top_leverage": all_lev[:6],
            "biggest_dropoffs": sorted(all_subs, key=lambda s_: -s_["dropoff"])[:8],
            "no_cover": [s_ for s_ in all_subs
                         if s_["replacement"] == "NOBODY ELIGIBLE"],
            "closest_matchup": results[0] if results else None,
            "biggest_injury_tax": sorted(
                blocks, key=lambda b: b["injury_tax"]["mean"])[:4],
        },
    }
    if not replay:
        snapshot.save(season, week,
                      {"projections": proj, "opponents": opponent, "injuries": inj,
                       "rankings": {str(r["roster_id"]): r["rank"]
                                    for r in power["table"]}},
                      {"projections": "sleeper/rotowire",
                       "injuries": src_mix or {"none": 0},
                       "priors": "2025 actuals", "seed": seed})
    return facts
