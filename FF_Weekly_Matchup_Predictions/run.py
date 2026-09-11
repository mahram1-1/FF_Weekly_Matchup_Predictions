#!/usr/bin/env python3
"""Weekly runner.  python3 run.py [week] [--replay] [--claude]

Writes two documents:
  out/power_wkNN.md     league-wide power rankings
  out/matchups_wkNN.md  the six previews with projections
plus out/facts_wkNN.json, the audit trail both are built from.
"""
import json, sys, pathlib
from gleague import sleeper, facts, writeup

ROOT = pathlib.Path(__file__).parent


def load_config() -> dict:
    """config.yaml is the only place the league id lives."""
    f = ROOT / "config.yaml"
    try:
        import yaml
        return yaml.safe_load(f.read_text())
    except ImportError:                      # minimal fallback, no dependency
        cfg, cur = {}, None
        for line in f.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            k, _, v = line.partition(":")
            v = v.split("#")[0].strip().strip('"')
            if not v:
                cur = k.strip(); cfg[cur] = {}
            elif line.startswith(" ") and cur:
                cfg[cur][k.strip()] = ({"true": True, "false": False}
                                       .get(v.lower(), int(v) if v.isdigit() else v))
            else:
                cfg[k.strip()] = v
        return cfg


def main():
    cfg = load_config()
    league = str(cfg["league_id"])
    src = cfg.get("sources") or {}
    sim = cfg.get("simulation") or {}

    args = sys.argv[1:]
    replay, use_claude = "--replay" in args, "--claude" in args
    nums = [a for a in args if a.isdigit()]
    week = int(nums[0]) if nums else sleeper.state()["week"]

    print(f"Week {week}{' (replay)' if replay else ''} · league {league}")
    f = facts.build(league, week, src,
                    n_sims=int(sim.get("n_sims", 25000)), replay=replay)
    out = pathlib.Path("out"); out.mkdir(exist_ok=True)
    (out / f"facts_wk{week:02d}.json").write_text(json.dumps(f, indent=1, default=str))

    (out / f"facts_wk{week:02d}_brief.json").write_text(
        json.dumps(writeup.brief(f), indent=1, default=str))

    docs = {f"power_wk{week:02d}.md": writeup.render_power_rankings(f),
            f"matchups_wk{week:02d}.md": writeup.render_matchups(f)}
    for name, body in docs.items():
        (out / name).write_text(body)
        print(f"  -> out/{name}")
    print(f"  -> out/facts_wk{week:02d}.json")
    print(f"  -> out/facts_wk{week:02d}_brief.json  (paste this into a Claude chat)")

    if use_claude:
        print("  note: --claude bills the Claude API, which is separate from a "
              "Pro/Max subscription. Omit it and paste the brief packet into a "
              "chat instead.")
        for name, fn in ((f"power_wk{week:02d}_claude.md", writeup.power_with_claude),
                         (f"matchups_wk{week:02d}_claude.md", writeup.matchups_with_claude)):
            try:
                (out / name).write_text(fn(f))
                print(f"  -> out/{name}")
            except Exception as e:
                print(f"  {name} skipped: {e}")


if __name__ == "__main__":
    main()
