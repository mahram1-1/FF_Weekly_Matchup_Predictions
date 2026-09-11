"""Projection sources. NOTE: superseded as the primary path by simulate.py -- kept
for optional multi-source blending of the projection MEAN only.

The whole point of the "make or break" metric is DISAGREEMENT between forecasters,
so you need >=3 independent sources. Sleeper only ships one (Rotowire), so the
rest come from outside.

Status of each adapter:
  sleeper_rotowire  TESTED, works, no auth.
  espn              WRITTEN, untested here (network-restricted sandbox). Undocumented
                    endpoint, no auth needed. This is the one most likely to break.
  fantasypros       WRITTEN, needs a free API key from https://api.fantasypros.com.
                    Highest-value source: it's a consensus of ~15 analysts, so it
                    anchors the mean instead of adding another single opinion.
  yahoo             NOT WRITTEN. Needs OAuth2 (free dev app + yfpy). Add only if you
                    want a 4th source; the marginal value after 3 is small.

All sources return: {sleeper_player_id: projected_ppr_points}
"""
from __future__ import annotations
import csv, io, json, os, time, urllib.request, pathlib, functools

CACHE = pathlib.Path(__file__).parent.parent / ".cache"
CACHE.mkdir(exist_ok=True)
XWALK_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"


@functools.lru_cache(maxsize=1)
def crosswalk() -> dict:
    """sleeper_id -> {espn_id, yahoo_id, fantasypros_id, gsis_id, name, position}

    Sleeper's own players.json has espn_id/yahoo_id populated for only ~30% of
    rostered players (rookies and recent adds are blank), which is not good enough.
    DynastyProcess maintains this crosswalk and it covered 167/168 non-DEF players
    on this league's rosters when tested.
    """
    f = CACHE / "db_playerids.csv"
    if not f.exists() or time.time() - f.stat().st_mtime > 7 * 86400:
        with urllib.request.urlopen(XWALK_URL, timeout=60) as r:
            f.write_bytes(r.read())
    out = {}
    for row in csv.DictReader(io.StringIO(f.read_text(errors="replace"))):
        sid = row.get("sleeper_id")
        if sid and sid != "NA":
            out[sid] = {k: (row.get(k) if row.get(k) != "NA" else None)
                        for k in ("espn_id", "yahoo_id", "fantasypros_id", "gsis_id",
                                  "pfr_id", "name", "position", "team")}
    return out


# ---------------------------------------------------------------- source 1
def sleeper_rotowire(season: str, week: int) -> dict:
    from . import sleeper
    return {r["player_id"]: r["stats"]["pts_ppr"]
            for r in sleeper.projections(season, week)
            if r.get("stats", {}).get("pts_ppr") is not None}


# ---------------------------------------------------------------- source 2
ESPN_URL = ("https://lm-api-reads.espn.com/apis/v3/games/ffl/seasons/{season}"
            "/segments/0/leagues/{league}?view=kona_player_info")


def espn(season: str, week: int, espn_league_id: str, ppr: bool = True) -> dict:
    """ESPN's own projections. Requires ANY public ESPN league id (yours or a
    random public one) — the player pool comes back regardless of league.

    UNTESTED in this sandbox. If ESPN changes the endpoint, this returns {} and
    the pipeline degrades to fewer sources rather than crashing. Verify before
    trusting output.
    """
    hdr = {"x-fantasy-filter": json.dumps({"players": {
        "filterStatsForTopScoringPeriodIds": {"value": 17},
        "limit": 1500, "sortDraftRanks": {"sortPriority": 1, "sortAsc": True, "value": "PPR"}}})}
    req = urllib.request.Request(ESPN_URL.format(season=season, league=espn_league_id),
                                 headers={**hdr, "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.load(r)
    except Exception as e:
        print(f"  [espn] FAILED: {e}")
        return {}

    xw = crosswalk()
    espn_to_sleeper = {v["espn_id"]: k for k, v in xw.items() if v.get("espn_id")}
    out = {}
    for entry in data.get("players", []):
        p = entry.get("player") or {}
        for s in p.get("stats", []):
            # statSourceId 1 = projection, statSplitTypeId 1 = single week
            if s.get("statSourceId") == 1 and s.get("scoringPeriodId") == week:
                sid = espn_to_sleeper.get(str(p.get("id")))
                if sid:
                    out[sid] = round(s.get("appliedTotal", 0.0), 2)
    return out


# ---------------------------------------------------------------- source 3
def fantasypros(season: str, week: int, api_key: str | None = None) -> dict:
    """FantasyPros consensus (ECR-weighted mean of many analysts).

    Free key: https://api.fantasypros.com  ->  set FANTASYPROS_API_KEY.
    Do NOT scrape the HTML site instead; their ToS prohibits it and you'd be
    building a weekly job on something that can be shut off.
    """
    api_key = api_key or os.getenv("FANTASYPROS_API_KEY")
    if not api_key:
        print("  [fantasypros] skipped: no FANTASYPROS_API_KEY set")
        return {}
    xw = crosswalk()
    fp_to_sleeper = {v["fantasypros_id"]: k for k, v in xw.items() if v.get("fantasypros_id")}
    out = {}
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        url = (f"https://api.fantasypros.com/public/v2/json/nfl/{season}/projections"
               f"?position={pos}&week={week}&scoring=PPR")
        req = urllib.request.Request(url, headers={"x-api-key": api_key})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except Exception as e:
            print(f"  [fantasypros] {pos} FAILED: {e}")
            continue
        for p in data.get("players", []):
            sid = fp_to_sleeper.get(str(p.get("fpid") or p.get("player_id")))
            pts = (p.get("stats") or {}).get("points")
            if sid and pts is not None:
                out[sid] = float(pts)
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------- registry
def collect(season: str, week: int, cfg: dict) -> dict[str, dict]:
    """Returns {source_name: {player_id: points}} for every enabled source."""
    got = {}
    if cfg.get("sleeper", True):
        got["rotowire"] = sleeper_rotowire(season, week)
    if cfg.get("espn_league_id"):
        d = espn(season, week, cfg["espn_league_id"])
        if d:
            got["espn"] = d
    if cfg.get("fantasypros", True):
        d = fantasypros(season, week)
        if d:
            got["fantasypros"] = d
    return {k: v for k, v in got.items() if v}
