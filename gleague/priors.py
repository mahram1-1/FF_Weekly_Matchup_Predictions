"""Empirical priors, fit from 2023-2025 actuals. Nothing here is assumed.

Rebuild once a season: python3 -c "from gleague import priors; priors.build()"

WHAT GETS FIT
1. Outcome-ratio pools per position and TIER (5 tiers for skill positions), so a
   WR1's floor comes from other WR1s rather than from every WR in the league.
2. Which games count. A player forced out before halftime is not a fantasy
   outcome you could have planned around, so those weeks are dropped. A player
   who made it to Q3 or Q4 and got hurt there IS a real outcome and stays in.
   Last-snap quarter is exact, from nflverse participation joined to play-by-play.
3. Same-game correlations.
4. Play probability and performance haircut by injury designation x practice
   participation.

Requires pyarrow (parquet reads of play-by-play).
"""
from __future__ import annotations
import csv, io, json, collections, itertools, pathlib, urllib.request
import numpy as np

POS = ["QB", "RB", "WR", "TE", "K", "DEF"]
SKILL = ["QB", "RB", "WR", "TE"]
FF_POS = {"QB", "RB", "WR", "TE", "K", "FB", "HB"}
SEASONS = ("2023", "2024", "2025")
TIERS_SKILL, TIERS_OTHER = 5, 3
MIN_ROLE = 0.40          # median offensive snap share to count as every-down
MIN_WEEKS = 6

NV = "https://github.com/nflverse/nflverse-data/releases/download"
DATA = pathlib.Path(__file__).parent.parent / "data"
CACHE = pathlib.Path(__file__).parent.parent / ".cache"
for d in (DATA, CACHE):
    d.mkdir(exist_ok=True)
PRIORS = DATA / "priors.json"


def _bytes(url: str, name: str, ttl_days=30) -> bytes | None:
    import time
    f = CACHE / name
    if f.exists() and time.time() - f.stat().st_mtime < ttl_days * 86400:
        return f.read_bytes()
    try:
        with urllib.request.urlopen(url, timeout=300) as r:
            b = r.read()
        f.write_bytes(b)
        return b
    except Exception as e:
        print(f"  [priors] {name} unavailable ({e})")
        return None


def _sleeper_weekly(season: str, settings: dict | None = None):
    """(pid -> {week: pts}), (pid -> pos), (pid -> {week: (team, opp)})

    If `settings` is given, weeks are scored with the league's own rules rather
    than Sleeper's stock pts_ppr. This matters for defence: under this league's
    settings the good-to-bad DEF spread is 58% wider than stock, so pools fit on
    pts_ppr would understate defensive variance everywhere.
    """
    from .scoring import score
    pts, meta, tm = collections.defaultdict(dict), {}, collections.defaultdict(dict)
    for wk in range(1, 19):
        url = (f"https://api.sleeper.app/stats/nfl/{season}/{wk}?season_type=regular&"
               + "&".join(f"position[]={p}" for p in POS) + "&order_by=pts_ppr")
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                rows = json.load(r)
        except Exception as e:
            print(f"  [priors] {season} wk{wk} failed: {e}")
            continue
        for x in rows:
            s = x.get("stats") or {}
            if s.get("gp") == 1 and s.get("pts_ppr") is not None:
                pid = x["player_id"]
                pts[pid][wk] = score(s, settings) if settings else s["pts_ppr"]
                meta[pid] = (x.get("player") or {}).get("position")
                tm[pid][wk] = (x.get("team"), x.get("opponent"))
    return pts, meta, tm


def _last_snap_quarter(season: str, gsis_to_sleeper: dict) -> dict:
    """(sleeper_id, week) -> quarter of that player's final offensive snap."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("  [priors] pyarrow missing -- cannot detect first-half exits")
        return {}
    pb = _bytes(f"{NV}/pbp/play_by_play_{season}.parquet", f"pbp_{season}.parquet")
    pa_ = _bytes(f"{NV}/pbp_participation/pbp_participation_{season}.parquet",
                 f"part_{season}.parquet")
    if not pb or not pa_:
        return {}
    pbp = pq.read_table(io.BytesIO(pb),
                        columns=["game_id", "play_id", "qtr", "season_type", "week"]).to_pydict()
    qmap = {(g, p): (int(q), int(w))
            for g, p, q, st, w in zip(pbp["game_id"], pbp["play_id"], pbp["qtr"],
                                      pbp["season_type"], pbp["week"])
            if st == "REG" and q is not None}
    part = pq.read_table(io.BytesIO(pa_),
                         columns=["nflverse_game_id", "play_id", "offense_players"]).to_pydict()
    out = {}
    for g, p, players in zip(part["nflverse_game_id"], part["play_id"],
                             part["offense_players"]):
        hit = qmap.get((g, p))
        if not hit or not players:
            continue
        q, wk = hit
        for gsis in players.split(";"):
            sid = gsis_to_sleeper.get(gsis)
            if sid and out.get((sid, wk), 0) < q:
                out[(sid, wk)] = q
    return out


def _snap_share(season: str, pfr_to_sleeper: dict) -> dict:
    b = _bytes(f"{NV}/snap_counts/snap_counts_{season}.csv", f"snaps_{season}.csv")
    if not b:
        return {}
    out = collections.defaultdict(dict)
    for r in csv.DictReader(io.StringIO(b.decode("utf-8", "replace"))):
        if r.get("game_type") != "REG":
            continue
        sid = pfr_to_sleeper.get(r.get("pfr_player_id"))
        if not sid:
            continue
        try:
            out[sid][int(r["week"])] = float(r["offense_pct"])
        except (TypeError, ValueError):
            pass
    return dict(out)


def _play_rates(seasons, gsis_to_sleeper, played, pts, season_mean) -> dict:
    def prac(s):
        s = (s or "").lower()
        return ("DNP" if "did not" in s else "LIM" if "limited" in s
                else "FULL" if "full" in s else "NONE")
    cell = collections.defaultdict(lambda: {"n": 0, "p": 0, "r": []})
    for yr in seasons:
        b = _bytes(f"{NV}/injuries/injuries_{yr}.csv", f"inj_{yr}.csv")
        if not b:
            continue
        for r in csv.DictReader(io.StringIO(b.decode("utf-8", "replace"))):
            if (r.get("season_type") or r.get("game_type")) != "REG":
                continue
            if r.get("position") not in FF_POS:
                continue
            sid = gsis_to_sleeper.get(r.get("gsis_id"))
            wk = int(r["week"])
            if not sid or (yr, wk) not in played:
                continue
            st = (r.get("report_status") or "").strip() or "NoDesignation"
            c = cell[(st, prac(r.get("practice_status")))]
            c["n"] += 1
            if sid in played[(yr, wk)]:
                c["p"] += 1
                if (yr, sid) in season_mean:
                    c["r"].append(pts[(yr, wk, sid)] / season_mean[(yr, sid)])
    out = {}
    for (st, pr), c in cell.items():
        if c["n"] < 25:
            continue
        out[f"{st}|{pr}"] = {"n": c["n"], "p_play": round(c["p"] / c["n"], 4),
                             "haircut": round(float(np.mean(c["r"])), 4)
                             if len(c["r"]) >= 20 else None}
    for st in ("Questionable", "Doubtful", "Out"):
        tot = sum(c["n"] for k, c in cell.items() if k[0] == st)
        pl = sum(c["p"] for k, c in cell.items() if k[0] == st)
        rr = [x for k, c in cell.items() if k[0] == st for x in c["r"]]
        if tot >= 25:
            out[f"{st}|POOLED"] = {"n": tot, "p_play": round(pl / tot, 4),
                                   "haircut": round(float(np.mean(rr)), 4) if len(rr) >= 20 else None}
    return out


def _pool(ratios, mean_ppg):
    a = np.array(ratios)
    return {"ratios": sorted(round(float(x), 4) for x in a),
            "avg": round(float(mean_ppg), 2),
            "floor_p10": round(float(np.percentile(a, 10) * mean_ppg), 2),
            "ceiling_p90": round(float(np.percentile(a, 90) * mean_ppg), 2),
            "cv": round(float(a.std()), 3),
            "p_under_half": round(float((a < 0.5).mean()), 3),
            "p_over_double": round(float((a > 2.0).mean()), 3),
            "n_weeks": len(a)}


def build(seasons=SEASONS, settings: dict | None = None) -> dict:
    from .sources import crosswalk
    xw = crosswalk()
    gsis = {v["gsis_id"]: k for k, v in xw.items() if v.get("gsis_id")}
    pfr = {v["pfr_id"]: k for k, v in xw.items() if v.get("pfr_id")}

    allpts, meta, teamwk, played = {}, {}, {}, collections.defaultdict(set)
    lastq, snaps = {}, {}
    for yr in seasons:
        print(f"  [priors] {yr}...")
        p, m, t = _sleeper_weekly(yr, settings)
        for pid, d in p.items():
            allpts[(yr, pid)] = d
            teamwk[(yr, pid)] = t[pid]
            for wk in d:
                played[(yr, wk)].add(pid)
        meta.update(m)
        for (sid, wk), q in _last_snap_quarter(yr, gsis).items():
            lastq[(yr, sid, wk)] = q
        for sid, d in _snap_share(yr, pfr).items():
            snaps[(yr, sid)] = d

    flat_pts = {(yr, wk, pid): v
                for (yr, pid), d in allpts.items() for wk, v in d.items()}
    season_mean = {k: float(np.mean(list(d.values())))
                   for k, d in allpts.items()
                   if len(d) >= MIN_WEEKS and np.mean(list(d.values())) >= 3}

    # ---- censor first-half exits only -----------------------------------
    censored = kept_2h = kept_full = 0
    by_pos = collections.defaultdict(list)
    for (yr, pid), wks in allpts.items():
        pos = meta.get(pid)
        if pos not in POS or len(wks) < MIN_WEEKS:
            continue
        sp = snaps.get((yr, pid), {})
        live = [v for v in sp.values() if v > 0]
        med = float(np.median(live)) if live else None
        every_down = med is not None and med >= MIN_ROLE

        keep = {}
        for wk, p in wks.items():
            q = lastq.get((yr, pid, wk))
            if every_down and q is not None and q <= 2:
                censored += 1                       # forced out before halftime
                continue
            if q is not None and q == 3 and sp.get(wk, 1.0) < 0.75 * (med or 1):
                kept_2h += 1                        # left in the 2nd half -- counts
            else:
                kept_full += 1
            keep[wk] = p
        if len(keep) < 5:
            continue
        m = float(np.mean(list(keep.values())))
        # DEF can legitimately average low (or negative weeks) under this league's
        # points-allowed and yards-allowed penalties, so it gets a lower floor.
        if m < (1.0 if pos in ("DEF", "K") else 3.0):
            continue
        by_pos[pos].append((m, np.array(list(keep.values())) / m))

    pools = {}
    for pos in POS:
        pl = sorted(by_pos[pos], key=lambda x: -x[0])
        nt = TIERS_SKILL if pos in SKILL else TIERS_OTHER
        if len(pl) < 4 * nt:
            nt = TIERS_OTHER
        if len(pl) < 3 * nt:
            continue
        cut = len(pl) // nt
        for t in range(nt):
            chunk = pl[t * cut: (t + 1) * cut if t < nt - 1 else None]
            pools[f"{pos}|{t}"] = {
                **_pool(np.concatenate([r for _, r in chunk]),
                        float(np.mean([m for m, _ in chunk]))),
                "tier": t + 1, "of_tiers": nt, "n_players": len(chunk),
                "ppg_range": [round(chunk[-1][0], 1), round(chunk[0][0], 1)]}

    # ---- correlations, on within-season z-scores ------------------------
    z, tof, oof = {}, {}, {}
    for (yr, pid), d in allpts.items():
        v = np.array(list(d.values()))
        if len(v) < 8 or v.mean() < 3 or v.std() < 1e-6:
            continue
        z[(yr, pid)] = {w: (d[w] - v.mean()) / v.std() for w in d}
        tof[(yr, pid)] = {w: teamwk[(yr, pid)][w][0] for w in d}
        oof[(yr, pid)] = {w: teamwk[(yr, pid)][w][1] for w in d}
    byteam = collections.defaultdict(list)
    for k in z:
        for w in z[k]:
            if tof[k].get(w):
                byteam[(k[0], tof[k][w], w)].append(k)
    buckets = collections.defaultdict(list)
    for _, plist in byteam.items():
        for a, b in itertools.combinations(plist, 2):
            w = next(iter(set(z[a]) & set(z[b])), None)
            key = "same_team|" + "|".join(sorted([meta[a[1]], meta[b[1]]]))
            for wk in set(z[a]) & set(z[b]):
                if tof[a].get(wk) == tof[b].get(wk):
                    buckets[key].append((z[a][wk], z[b][wk]))
                    break
    defs = [k for k in z if meta.get(k[1]) == "DEF"]
    for k in z:
        if meta.get(k[1]) == "DEF":
            continue
        for wk in z[k]:
            o = oof[k].get(wk)
            for dk in defs:
                if dk[0] == k[0] and o and tof[dk].get(wk) == o and wk in z[dk]:
                    buckets[f"vs_opp_def|{meta[k[1]]}|DEF"].append((z[k][wk], z[dk][wk]))
    corr = {}
    for key, v in buckets.items():
        if len(v) < 200:
            continue
        a = np.array(v)
        r = float(np.corrcoef(a[:, 0], a[:, 1])[0, 1])
        if abs(r) >= 0.05:
            corr[key] = {"r": round(r, 3), "n": len(v)}

    rates = _play_rates(seasons, gsis, played, flat_pts, season_mean)

    out = {"seasons": list(seasons), "pools": pools, "correlations": corr,
           "play_rates": rates,
           "censoring": {"rule": "drop weeks whose last offensive snap was in Q1 or "
                                 "Q2 for every-down players; keep Q3/Q4/OT exits",
                         "censored": censored, "kept_second_half_exit": kept_2h,
                         "kept_full": kept_full,
                         "share_censored": round(censored / max(censored + kept_2h + kept_full, 1), 4)},
           "tiers": {"skill": TIERS_SKILL, "other": TIERS_OTHER},
           "scored_with": "league settings" if settings else "stock pts_ppr"}
    PRIORS.write_text(json.dumps(out))
    print(f"  [priors] censored {censored} first-half exits, kept {kept_2h} "
          f"second-half exits, {kept_full} full games")
    return out


def load(rebuild=False, settings: dict | None = None) -> dict:
    if PRIORS.exists() and not rebuild:
        return json.loads(PRIORS.read_text())
    print("  [priors] building from 2023-2025 actuals (one-time, ~2 min)...")
    return build(settings=settings)
