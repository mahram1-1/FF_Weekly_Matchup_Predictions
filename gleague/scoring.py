"""Score projections with THIS league's settings, not Sleeper's stock pts_ppr.

Sleeper's projection rows carry a `pts_ppr` convenience field computed with
standard full-PPR scoring. Every league that has touched its scoring settings
gets a wrong number from it, silently, and the error is largest exactly where
nobody checks: defence and kicker.

Sleeper scores a player by summing stat x setting over matching keys, so that is
what this does. The projection rows already carry every component we need,
including the tier indicators (pts_allow_14_20, yds_allow_400_449, fgm_40_49)
which arrive as expected values between 0 and 1.
"""
from __future__ import annotations

# Keys present in projection rows that are summaries, not scoreable events.
NON_SCORING = {"gp", "pts_ppr", "pts_half_ppr", "pts_std", "cmp_pct",
               "pass_att", "pass_cmp", "pass_inc", "pass_sack", "rush_att",
               "rec_tgt", "fga", "xpa", "fgm", "fgm_yds", "yds_allow",
               "def_kr_yd", "def_pr_yd", "pr_yd", "kr_yd"}


def score(stats: dict, settings: dict) -> float:
    """Expected fantasy points under the league's own scoring settings."""
    total = 0.0
    for k, v in stats.items():
        if k in NON_SCORING or k.startswith(("adp", "pos_adp")):
            continue
        mult = settings.get(k)
        if mult:
            total += v * mult
    # yardage-over-threshold bonuses are keyed on a derived quantity
    if settings.get("fgm_yds_over_30") and stats.get("fgm_yds"):
        over = max(stats["fgm_yds"] - 30.0 * stats.get("fgm", 0.0), 0.0)
        total += over * settings["fgm_yds_over_30"]
    return total


def compare(rows: list[dict], settings: dict) -> dict:
    """player_id -> {league, stock, delta} for every row with a projection."""
    out = {}
    for r in rows:
        st = r.get("stats") or {}
        if st.get("pts_ppr") is None:
            continue
        lg = score(st, settings)
        out[r["player_id"]] = {"league": round(lg, 2),
                               "stock": round(st["pts_ppr"], 2),
                               "delta": round(lg - st["pts_ppr"], 2),
                               "pos": (r.get("player") or {}).get("position")}
    return out


def league_points(rows: list[dict], settings: dict) -> dict:
    """player_id -> league-scored projection. The number the sim should centre on."""
    return {pid: v["league"] for pid, v in compare(rows, settings).items()}
