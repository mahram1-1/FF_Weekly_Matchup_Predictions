"""Sleeper API client. Public, no auth, no rate limit published (be polite: ~1 req/sec)."""
from __future__ import annotations
import json, time, urllib.request, urllib.parse, functools, pathlib

BASE = "https://api.sleeper.app"
CACHE = pathlib.Path(__file__).parent.parent / ".cache"
CACHE.mkdir(exist_ok=True)


def _get(path: str, params: dict | None = None, cache_key: str | None = None, ttl: int = 900):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    if cache_key:
        f = CACHE / f"{cache_key}.json"
        if f.exists() and time.time() - f.stat().st_mtime < ttl:
            return json.loads(f.read_text())
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    if cache_key:
        (CACHE / f"{cache_key}.json").write_text(json.dumps(data))
    return data


def state() -> dict:
    return _get("/v1/state/nfl", cache_key="state", ttl=1800)


def league(league_id: str) -> dict:
    return _get(f"/v1/league/{league_id}", cache_key=f"league_{league_id}", ttl=3600)


def users(league_id: str) -> list:
    return _get(f"/v1/league/{league_id}/users", cache_key=f"users_{league_id}", ttl=3600)


def rosters(league_id: str) -> list:
    return _get(f"/v1/league/{league_id}/rosters", cache_key=f"rosters_{league_id}", ttl=600)


def matchups(league_id: str, week: int) -> list:
    return _get(f"/v1/league/{league_id}/matchups/{week}",
                cache_key=f"matchups_{league_id}_{week}", ttl=300)


def transactions(league_id: str, week: int) -> list:
    return _get(f"/v1/league/{league_id}/transactions/{week}",
                cache_key=f"txn_{league_id}_{week}", ttl=600)


@functools.lru_cache(maxsize=1)
def players() -> dict:
    """~14MB. Cached hard — Sleeper asks you to pull this at most once a day."""
    return _get("/v1/players/nfl", cache_key="players_nfl", ttl=86400)


POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def projections(season: str, week: int | None = None, positions=POSITIONS) -> list:
    """Rotowire projections, served by Sleeper. week=None returns season totals.

    NOTE: the endpoint accepts a `company=` param (espn, fantasypros, numberfire,
    sportradar, yahoo...) but every provider other than rotowire returns stub
    records with no real stats. Verified 2026-09-07. Do not build on it.
    """
    path = f"/projections/nfl/{season}" + (f"/{week}" if week else "")
    key = f"proj_{season}_{week or 'season'}"
    return _get(path, {"season_type": "regular", "position[]": positions,
                       "order_by": "pts_ppr"}, cache_key=key, ttl=3600)


def stats(season: str, week: int, positions=POSITIONS) -> list:
    """Actual scored stats for a completed week."""
    return _get(f"/stats/nfl/{season}/{week}", {"season_type": "regular",
                "position[]": positions, "order_by": "pts_ppr"},
                cache_key=f"stats_{season}_{week}", ttl=1800)


# ---------- shaping helpers ----------

def team_index(league_id: str) -> dict[int, dict]:
    """roster_id -> {team, manager, owner_id, players, starters, wins, losses, fpts}"""
    us = {u["user_id"]: u for u in users(league_id)}
    out = {}
    for r in rosters(league_id):
        u = us.get(r["owner_id"], {})
        meta = u.get("metadata") or {}
        s = r.get("settings") or {}
        out[r["roster_id"]] = {
            "roster_id": r["roster_id"],
            "team": meta.get("team_name") or u.get("display_name") or f"Team {r['roster_id']}",
            "manager": u.get("display_name", "?"),
            "players": r.get("players") or [],
            "starters": r.get("starters") or [],
            "wins": s.get("wins", 0), "losses": s.get("losses", 0), "ties": s.get("ties", 0),
            "fpts": s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100,
            "waiver_budget_used": s.get("waiver_budget_used", 0),
        }
    return out


def injury_report(player_ids) -> list[dict]:
    P = players()
    out = []
    for pid in player_ids:
        p = P.get(pid) or {}
        if p.get("injury_status"):
            out.append({
                "player_id": pid,
                "name": p.get("full_name") or pid,
                "pos": p.get("position"), "nfl_team": p.get("team"),
                "status": p["injury_status"],
                "body_part": p.get("injury_body_part"),
                "notes": p.get("injury_notes") or "",
                "start_date": p.get("injury_start_date"),
            })
    order = {"Out": 0, "IR": 0, "PUP": 0, "Doubtful": 1, "NA": 1, "Questionable": 2, "Sus": 3}
    return sorted(out, key=lambda x: (order.get(x["status"], 4), x["name"]))
