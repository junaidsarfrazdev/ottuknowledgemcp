# MCP Quality Evaluation

A tiny harness that runs the same questions through Claude Code twice —
once with the `ottu-knowledge` MCP server enabled, once without — and
produces a defensible markdown report comparing answer quality and cost.

The eval is designed so you can hand the report to your manager and
answer the question *"is this MCP worth the tokens it costs?"* with
numbers rather than vibes.

## What it measures

- **Citation recall** — did the answer reference the files / functions
  that a correct answer must cite?
- **Citation accuracy** — of the files the answer did cite, how many
  were real and relevant (vs. hallucinations)?
- **Hallucination count** — how many plausible-but-invented paths were named?
- **Quality 1–5** — a grader's one-shot overall score.
- **Token cost + wall-clock + turns** — the usage from `claude -p --output-format=json`.
- **Cost per correct citation** — the headline economic metric.
  Normalizes raw token cost by the amount of verifiable work delivered.

Both runs get the same filesystem access (`Read`, `Grep`, `Glob`, a
narrow `Bash` allowlist). The only difference is whether
`mcp__ottu-knowledge__*` tools are available. The grader sees answers as
"Answer A" / "Answer B" with randomized assignment per question, so it
cannot tell which run is MCP.

## One-time setup

1. The main project setup must already be done (`bash setup.sh`,
   `python cli.py index`, `.env` pointing at `OTTU_WORKSPACE`).
2. PyYAML is the only extra dep — already added to `requirements.txt`.
   If you haven't re-installed since, do `pip install -r requirements.txt`.
3. Make sure `claude` is on your `PATH` and you're authenticated
   (`claude auth status`).

## Writing questions

Edit `eval/questions.yaml`. The file ships with 5 seed questions based on
the Jazz-checkout flow; **replace these with questions from real tickets /
Slack threads** before running the eval for real. A defensible eval uses
questions you didn't construct to flatter MCP.

For each question, specify the ground truth:

```yaml
- id: short_stable_id
  category: lookup  # or architecture / security / integration / bug_hunt / flow_variant
  question: |
    Full question text the model will see.
  must_cite:
    files:      [ list of file paths a correct answer MUST name ]
    functions:  [ list of function names that must appear ]
    repos:      [ repos that must be referenced ]
  must_mention: [ free-text terms that must appear in the answer ]
  must_not_hallucinate:
    - path/that/does/not/exist.ts
  bonus: [ nice-to-have citations that earn a bonus point ]
```

Ground truth quality matters more than quantity. 15 well-specified
questions beat 50 sloppy ones. Aim for a mix of categories.

## Run

```bash
# 1. Run both modes across all questions
python eval/run_eval.py
#   → eval/results/<UTC-timestamp>/{mcp_on,mcp_off}/<qid>.{json,txt}

# 2. Grade (blind A/B) — uses claude -p as judge
python eval/grade.py --run-dir eval/results/<timestamp>
#   → eval/results/<timestamp>/grades.json
#   → eval/results/<timestamp>/assignments.json

# 3. Build the report
python eval/report.py --run-dir eval/results/<timestamp>
#   → eval/results/<timestamp>/report.md
```

### Useful flags

```bash
# Subset while iterating
python eval/run_eval.py --ids jazz_iframe_creation origin_enforcement

# Resume after interruption (skip already-run questions)
python eval/run_eval.py --resume 2026-04-22T16-42-10Z

# Cap per-question spend
python eval/run_eval.py --max-budget 1.5

# Use a specific model
python eval/run_eval.py --model sonnet
python eval/grade.py --run-dir ... --model opus   # different model for grading
```

## Cost planning

Per question, both runs combined, you'll see something like:
- **MCP-off**: ~$0.05–0.15, 30–60s wall-clock
- **MCP-on**: ~$0.30–1.00, 2–5 min wall-clock
- **Grading**: ~$0.02 per question

So 15 questions ≈ $5–15 in API costs + ~1 hour wall-clock. Set
`--max-budget` per-run if you're worried.

## What the report contains

- Headline table (recall, hallucinations, quality, total tokens, cost,
  **cost per correct citation**, wall-clock)
- Head-to-head winner tally (blind judge)
- Per-category rollup
- Per-question detail table
- 3 qualitative side-by-side examples (the ones with the biggest quality delta)
- Methodology footer

## Defensibility checklist

Before showing to a manager, confirm:

- [ ] Questions are from real tickets, not constructed to favor MCP
- [ ] Ground truth was written before running the eval (not post-hoc)
- [ ] Both runs have the same tool allowlist except MCP
- [ ] Grader is blind (A/B anonymized; `assignments.json` proves it)
- [ ] Raw outputs (`mcp_on/*.txt`, `mcp_off/*.txt`) are kept for spot-checks
- [ ] At least 10–15 questions (smaller samples are anecdotal)

## Files

| Path | Purpose |
|---|---|
| `questions.yaml` | Question bank + ground truth |
| `mcp_on.json` | MCP config used in "MCP enabled" runs (auto-generated) |
| `mcp_off.json` | Empty MCP config used in "MCP disabled" runs |
| `run_eval.py` | Driver — spawns `claude -p` twice per question |
| `grade.py` | Blind judge — scores answers against ground truth |
| `report.py` | Generates the markdown report |
| `results/<ts>/` | Per-run outputs, grades, assignments, report |
