"""Week-over-week repeatability.

Injury tags change hourly and projections change daily. If you don't freeze the
inputs, you can never rerun last week's report and get last week's report, and
every disagreement in the group chat becomes unresolvable.

Each run writes snapshots/{season}/wk{N}/ containing the exact inputs used plus a
manifest with timestamps and data provenance. `--replay` rebuilds from the frozen
snapshot instead of re-fetching, so a Week 3 report generated in Week 9 is
byte-identical to the original.

The RNG seed is derived from (league, season, week), so the same inputs always
give the same simulation. No "the model changed its mind overnight".
"""
from __future__ import annotations
import hashlib, json, pathlib, datetime as dt

ROOT = pathlib.Path(__file__).parent.parent
SNAP = ROOT / "snapshots"


def seed_for(league_id: str, season: str, week: int) -> int:
    h = hashlib.sha256(f"{league_id}|{season}|{week}".encode()).hexdigest()
    return int(h[:8], 16)


def dir_for(season: str, week: int) -> pathlib.Path:
    d = SNAP / season / f"wk{week:02d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(season: str, week: int, payload: dict, provenance: dict) -> pathlib.Path:
    d = dir_for(season, week)
    for name, obj in payload.items():
        (d / f"{name}.json").write_text(json.dumps(obj))
    manifest = {
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "season": season, "week": week,
        "provenance": provenance,
        "files": {n: len(json.dumps(o)) for n, o in payload.items()},
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return d


def load(season: str, week: int) -> tuple[dict, dict] | tuple[None, None]:
    d = SNAP / season / f"wk{week:02d}"
    if not (d / "manifest.json").exists():
        return None, None
    payload = {p.stem: json.loads(p.read_text())
               for p in d.glob("*.json") if p.stem != "manifest"}
    return payload, json.loads((d / "manifest.json").read_text())


def history() -> list[dict]:
    """Every snapshot on disk, oldest first -- the spine for season-long tracking."""
    out = []
    for m in sorted(SNAP.glob("*/wk*/manifest.json")):
        out.append(json.loads(m.read_text()))
    return out
