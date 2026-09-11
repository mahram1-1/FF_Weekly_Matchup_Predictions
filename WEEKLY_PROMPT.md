# Weekly prompt

Paste one of these into a new chat in this Project. Pick the version that matches
how the repo reaches the container.

Why this matters: the container filesystem resets between chats. The code,
`data/priors.json` and especially `snapshots/` do not carry over. Without prior
snapshots the week-over-week ± column is blank and `--replay` cannot rebuild past
weeks. Project knowledge is injected as context, not written to disk, so it
cannot be run directly — the files have to arrive as a repo clone or an upload.

---

## Version A — repo on GitHub (recommended)

Best option. `github.com` is reachable from the container, snapshots live in git
history, and the audit trail survives.

```
Run the weekly fantasy report for Glenn's Gleague 2.0.

Repo: https://github.com/<USER>/gleague-agent
League ID: 1399633932319752192

Steps:
1. Clone the repo into /home/claude and pip install numpy scipy pyarrow.
2. Run `python3 run.py` with no week argument so it picks up the current NFL week
   from Sleeper's state endpoint. If it is Tuesday or Wednesday, that is the
   post-waiver run; if Sunday morning, that is the post-inactives run.
3. Present out/power_wkNN.md and out/matchups_wkNN.md.
4. Commit the new snapshots/ directory contents and tell me the exact git commands
   to push, or hand me the snapshot files to commit myself. Do not push for me.

Before you trust the output, verify and report on all of these:
- method.scoring_delta_worst — confirm defences are still being league-scored.
  If every delta is 0.0, league scoring silently failed and the numbers are wrong.
- method.injury_sources — which source answered. If this is empty or shows only
  "sleeper", say so plainly. ESPN returned HTTP 403 previously and fails soft,
  which looks identical to "no injuries this week".
- Whether nflverse has published injuries_2026.csv yet. Until it does, every
  Questionable player gets the pooled 63.0% with no practice detail. Once it
  publishes, practice granularity kicks in (Questionable+Full 73.4%,
  Questionable+DNP 46.5%) and the numbers will shift. Flag the week that changes.
- Whether the league's scoring_settings have changed since the priors were built.
  If they have, rebuild priors before reporting:
  `python3 -c "from gleague import priors, sleeper; priors.build(settings=sleeper.league('1399633932319752192')['scoring_settings'])"`
- Any starter whose tier assignment moved between pools since last week. A player
  near a tier boundary can hop pools week to week and make his floor/ceiling look
  jumpy for no real reason.
- Any team whose rank moved 4+ spots, with the reason in one line.

Then summarise in chat: the biggest ranking movers, the closest matchup, the
largest injury tax, and anything above that looked wrong. Be direct about what
you are unsure of. If a data source failed, lead with that rather than burying it.

Do not give start/sit or roster advice in either document.
```

---

## Version B — upload the folder

Use when the repo is not on GitHub. Zip the project folder and attach it.

```
Run the weekly fantasy report for Glenn's Gleague 2.0. The repo is attached.

League ID: 1399633932319752192

Steps:
1. Unzip the attachment into /home/claude and pip install numpy scipy pyarrow.
2. Confirm snapshots/ came through and list which weeks are present. If prior
   weeks are missing, say so — the ± movement column will be blank and I should
   know that before I send the report out.
3. Run `python3 run.py` with no week argument.
4. Present out/power_wkNN.md and out/matchups_wkNN.md, and give me the new
   snapshots/ directory back as a file so I can keep it for next week.

[then paste the same "Before you trust the output" block from Version A]
```

---

## Version C — one specific past week

```
Rebuild Week N of Glenn's Gleague 2.0 exactly as it was originally generated.

Repo: https://github.com/<USER>/gleague-agent

Run `python3 run.py N --replay`. This reads the frozen snapshot rather than
re-fetching, so it should reproduce the original byte for byte. Confirm the
manifest timestamp it replayed from, and tell me if the snapshot for that week
is missing.
```

---

## Free path — the prose versions without an API key

A Claude Pro/Max subscription does **not** include Claude API or Console access;
they are billed separately. `--claude` calls the API and costs money. You do not
need it.

The pipeline itself is free: Sleeper and nflverse are public, and both markdown
documents are generated deterministically by local Python. The only thing the API
adds is the same facts rewritten with jokes — and you can get that from a normal
Pro chat instead, since you are already sitting in one.

After any run, `out/facts_wkNN_brief.json` (~17k tokens, about half the full
packet) is sized for pasting. Use this follow-up in the same chat:

```
Now write the two prose documents from the facts packet you just produced.

Read the SYSTEM_POWER and SYSTEM_MATCHUPS constants in gleague/writeup.py and
follow them exactly — they are the system prompts the API path would have used.
Use out/facts_wkNN_brief.json as your only source of numbers.

Write the power rankings first, then the matchup previews, as two separate
artifacts I can copy out. You do no arithmetic: every number must already exist
in the packet. Do not reorder the rankings. No start/sit or roster advice.
```

If you are not in the chat that produced the files, attach
`facts_wkNN_brief.json` and `gleague/writeup.py` and use the same wording.

**Only use `--claude` if you want unattended GitHub Actions runs** that produce
prose with nobody watching. That needs `ANTHROPIC_API_KEY` as a repo secret and
bills the API. The workflow in `.github/workflows/weekly.yml` deliberately omits
it.

---

## What changes as the season goes

| Week | What shifts |
|---|---|
| 1 | Records all 0-0, ± column blank, rankings are pure roster quality |
| 2 | ± column populates from the Week 1 snapshot |
| 2+ | nflverse `injuries_2026.csv` should exist; practice detail starts feeding play rates |
| 2–7 | Ranking weights shift from projection-based toward results-based; by Week 7 actual scoring carries 60% |
| ~6 | Worth blending in-season actuals into the priors so a player's own shape starts overriding his tier's |

## Standing facts the prompt does not need to restate

These are in the repo and the README. Don't re-explain them in the weekly prompt:

- Projections are Rotowire components scored with the league's own settings, not
  Sleeper's stock `pts_ppr`. Defence differs by −2.3 points on average.
- Boom / bust / neither uses a fixed ±45% band, which is where "neither" first
  becomes the majority outcome league-wide. A low "neither" share is the flag.
- Floor and ceiling are p10/p90 of 25,000 simulated team totals. Team floor is not
  the sum of player floors.
- Ratings are points per week, not z-scores.
- Availability is lineup-state enumeration with substitution, not a multiplier.
