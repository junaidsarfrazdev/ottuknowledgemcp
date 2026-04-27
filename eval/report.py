"""Aggregate raw runs + grades into a markdown report.

Usage:
    python eval/report.py --run-dir eval/results/<timestamp>
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def _load(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _usage_totals(raw: dict) -> dict:
    usage = raw.get("usage") or {}
    return {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_create": int(usage.get("cache_creation_input_tokens") or 0),
        "cost_usd": float(raw.get("total_cost_usd") or 0.0),
        "turns": int(raw.get("num_turns") or 0),
        "wall_s": float(raw.get("_wall_clock_s") or 0.0),
        "exit_code": int(raw.get("_exit_code") or 0),
        "error": raw.get("_error"),
    }


def _pct(num: float, den: float) -> str:
    if den <= 0:
        return "—"
    return f"{100.0 * num / den:.0f}%"


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _ratio(a: float, b: float) -> str:
    if a <= 0:
        return "—"
    return f"{b / a:.1f}×"


def _fmt_int(x: float) -> str:
    return f"{int(x):,}"


def _grade_metrics(grade_side: dict, gt: dict) -> dict:
    files = gt.get("must_cite", {}).get("files") or []
    funcs = gt.get("must_cite", {}).get("functions") or []
    terms = gt.get("must_mention") or []
    correct_f = int(grade_side.get("correct_file_citations") or 0)
    correct_fn = int(grade_side.get("correct_function_citations") or 0)
    correct_t = int(grade_side.get("correct_term_citations") or 0)
    total_cites = int(grade_side.get("total_file_citations") or 0)
    halluc = len(grade_side.get("hallucinations") or [])
    quality = int(grade_side.get("quality_1_5") or 0)
    return {
        "file_recall": _safe_div(correct_f, len(files)),
        "function_recall": _safe_div(correct_fn, len(funcs)),
        "term_recall": _safe_div(correct_t, len(terms)),
        "citation_accuracy": _safe_div(correct_f, total_cites) if total_cites else 0.0,
        "hallucinations": halluc,
        "quality": quality,
        "correct_citations_total": correct_f + correct_fn + correct_t,
    }


def build_report(run_dir: Path, questions_file: Path) -> str:
    meta = _load(run_dir / "metadata.json", {}) or {}
    grades = _load(run_dir / "grades.json", {}) or {}
    assignments = _load(run_dir / "assignments.json", {}) or {}
    questions = yaml.safe_load(questions_file.read_text()).get("questions") or []
    q_by_id = {q["id"]: q for q in questions}

    rows = []
    for qid, grade in grades.items():
        if "_error" in grade:
            rows.append({"qid": qid, "error": grade["_error"]})
            continue
        mapping = assignments.get(qid, {"A": "mcp_on", "B": "mcp_off"})
        q = q_by_id.get(qid, {})

        side_on = grade["A"] if mapping["A"] == "mcp_on" else grade["B"]
        side_off = grade["A"] if mapping["A"] == "mcp_off" else grade["B"]

        on_raw = _load(run_dir / "mcp_on" / f"{qid}.json", {}) or {}
        off_raw = _load(run_dir / "mcp_off" / f"{qid}.json", {}) or {}

        rows.append(
            {
                "qid": qid,
                "category": q.get("category", "?"),
                "question": (q.get("question") or "").strip(),
                "on": _grade_metrics(side_on, q),
                "off": _grade_metrics(side_off, q),
                "on_usage": _usage_totals(on_raw),
                "off_usage": _usage_totals(off_raw),
                "winner": grade.get("winner"),
                "winner_reason": grade.get("winner_reason"),
                "on_notes": side_on.get("notes"),
                "off_notes": side_off.get("notes"),
                "on_halluc": side_on.get("hallucinations") or [],
                "off_halluc": side_off.get("hallucinations") or [],
            }
        )

    # Aggregate
    on_metrics = [r for r in rows if "error" not in r]
    agg: dict[str, dict] = {"on": {}, "off": {}}
    for side in ("on", "off"):
        if not on_metrics:
            continue
        agg[side]["file_recall"] = statistics.mean(r[side]["file_recall"] for r in on_metrics)
        agg[side]["function_recall"] = statistics.mean(r[side]["function_recall"] for r in on_metrics)
        agg[side]["term_recall"] = statistics.mean(r[side]["term_recall"] for r in on_metrics)
        agg[side]["citation_accuracy"] = statistics.mean(
            r[side]["citation_accuracy"] for r in on_metrics
        )
        agg[side]["hallucinations"] = statistics.mean(r[side]["hallucinations"] for r in on_metrics)
        agg[side]["quality"] = statistics.mean(r[side]["quality"] for r in on_metrics)
        agg[side]["correct_citations_total"] = sum(
            r[side]["correct_citations_total"] for r in on_metrics
        )
        usage_key = f"{side}_usage"
        agg[side]["tokens_input"] = sum(r[usage_key]["input"] for r in on_metrics)
        agg[side]["tokens_output"] = sum(r[usage_key]["output"] for r in on_metrics)
        agg[side]["tokens_cache_read"] = sum(r[usage_key]["cache_read"] for r in on_metrics)
        agg[side]["tokens_cache_create"] = sum(r[usage_key]["cache_create"] for r in on_metrics)
        agg[side]["cost_usd"] = sum(r[usage_key]["cost_usd"] for r in on_metrics)
        agg[side]["turns"] = statistics.mean(r[usage_key]["turns"] for r in on_metrics)
        agg[side]["wall_s"] = statistics.mean(r[usage_key]["wall_s"] for r in on_metrics)

    wins = {"mcp_on": 0, "mcp_off": 0, "tie": 0}
    for r in on_metrics:
        w = r.get("winner")
        mapping = assignments.get(r["qid"], {"A": "mcp_on", "B": "mcp_off"})
        if w in ("A", "B"):
            wins[mapping[w]] += 1
        else:
            wins["tie"] += 1

    out: list[str] = []
    out.append(f"# MCP Quality Evaluation — {meta.get('started_at', 'unknown')}")
    out.append("")
    out.append(f"- Run directory: `{run_dir}`")
    out.append(f"- Workspace: `{meta.get('workspace')}`")
    out.append(f"- Model: `{meta.get('model') or 'default'}`")
    out.append(f"- Questions scored: **{len(on_metrics)}** / {len(rows)}")
    if rows and len(on_metrics) < len(rows):
        errs = [r for r in rows if "error" in r]
        out.append(f"- Grader errors: {len(errs)} — see grades.json")
    out.append("")

    out.append("## Headline")
    out.append("")
    out.append("| Metric | No MCP | With MCP | Delta |")
    out.append("|---|---|---|---|")
    if on_metrics:
        on = agg["on"]
        off = agg["off"]
        out.append(
            f"| File-citation recall | {off['file_recall']*100:.0f}% | "
            f"{on['file_recall']*100:.0f}% | {(on['file_recall']-off['file_recall'])*100:+.0f}pp |"
        )
        out.append(
            f"| Function-citation recall | {off['function_recall']*100:.0f}% | "
            f"{on['function_recall']*100:.0f}% | {(on['function_recall']-off['function_recall'])*100:+.0f}pp |"
        )
        out.append(
            f"| Term recall | {off['term_recall']*100:.0f}% | "
            f"{on['term_recall']*100:.0f}% | {(on['term_recall']-off['term_recall'])*100:+.0f}pp |"
        )
        out.append(
            f"| Citation accuracy (correct/cited) | {off['citation_accuracy']*100:.0f}% | "
            f"{on['citation_accuracy']*100:.0f}% | "
            f"{(on['citation_accuracy']-off['citation_accuracy'])*100:+.0f}pp |"
        )
        out.append(
            f"| Hallucinations / Q | {off['hallucinations']:.2f} | "
            f"{on['hallucinations']:.2f} | {on['hallucinations']-off['hallucinations']:+.2f} |"
        )
        out.append(
            f"| Quality (1–5 avg) | {off['quality']:.2f} | {on['quality']:.2f} | "
            f"{on['quality']-off['quality']:+.2f} |"
        )
        tokens_off = off["tokens_input"] + off["tokens_output"] + off["tokens_cache_read"] + off["tokens_cache_create"]
        tokens_on = on["tokens_input"] + on["tokens_output"] + on["tokens_cache_read"] + on["tokens_cache_create"]
        out.append(
            f"| Total tokens | {_fmt_int(tokens_off)} | {_fmt_int(tokens_on)} | "
            f"{_ratio(tokens_off, tokens_on)} |"
        )
        out.append(
            f"| Total USD cost | ${off['cost_usd']:.3f} | ${on['cost_usd']:.3f} | "
            f"{_ratio(off['cost_usd'], on['cost_usd'])} |"
        )
        cost_per_off = _safe_div(off["cost_usd"], off["correct_citations_total"])
        cost_per_on = _safe_div(on["cost_usd"], on["correct_citations_total"])
        out.append(
            f"| **Cost per correct citation** | ${cost_per_off:.4f} | ${cost_per_on:.4f} | "
            f"{_ratio(cost_per_off, cost_per_on)} |"
        )
        out.append(f"| Avg turns | {off['turns']:.1f} | {on['turns']:.1f} | {_ratio(off['turns'], on['turns'])} |")
        out.append(f"| Avg wall-clock | {off['wall_s']:.1f}s | {on['wall_s']:.1f}s | {_ratio(off['wall_s'], on['wall_s'])} |")
    out.append("")

    out.append("## Head-to-head (blind judge)")
    out.append("")
    total_wins = wins["mcp_on"] + wins["mcp_off"] + wins["tie"]
    out.append(
        f"- **With MCP** won: **{wins['mcp_on']}** / {total_wins}  "
        f"({_pct(wins['mcp_on'], total_wins)})"
    )
    out.append(
        f"- **No MCP** won: **{wins['mcp_off']}** / {total_wins}  "
        f"({_pct(wins['mcp_off'], total_wins)})"
    )
    out.append(f"- Ties: **{wins['tie']}** / {total_wins}")
    out.append("")

    # Per-category rollup
    cats: dict[str, list[dict]] = {}
    for r in on_metrics:
        cats.setdefault(r["category"], []).append(r)
    if cats:
        out.append("## Per-category rollup")
        out.append("")
        out.append("| Category | N | Δ file-recall | Δ quality | MCP wins |")
        out.append("|---|---|---|---|---|")
        for cat, cat_rows in sorted(cats.items()):
            drec = statistics.mean(r["on"]["file_recall"] - r["off"]["file_recall"] for r in cat_rows)
            dq = statistics.mean(r["on"]["quality"] - r["off"]["quality"] for r in cat_rows)
            cat_wins = sum(
                1 for r in cat_rows
                if (r.get("winner") in ("A", "B"))
                and (assignments.get(r["qid"], {}).get(r["winner"]) == "mcp_on")
            )
            out.append(f"| {cat} | {len(cat_rows)} | {drec*100:+.0f}pp | {dq:+.2f} | {cat_wins}/{len(cat_rows)} |")
        out.append("")

    # Per-question table
    out.append("## Per-question detail")
    out.append("")
    out.append("| ID | Category | MCP quality | Non-MCP quality | MCP recall | Non-MCP recall | Winner | MCP halluc | Non-MCP halluc |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "error" in r:
            out.append(f"| {r['qid']} | — | — | — | — | — | GRADER ERR | — | — |")
            continue
        mapping = assignments.get(r["qid"], {"A": "mcp_on", "B": "mcp_off"})
        winner_mode = mapping.get(r["winner"], "tie") if r["winner"] in ("A", "B") else "tie"
        out.append(
            f"| `{r['qid']}` | {r['category']} | "
            f"{r['on']['quality']}/5 | {r['off']['quality']}/5 | "
            f"{r['on']['file_recall']*100:.0f}% | {r['off']['file_recall']*100:.0f}% | "
            f"**{winner_mode}** | {r['on']['hallucinations']} | {r['off']['hallucinations']} |"
        )
    out.append("")

    # Qualitative
    out.append("## Qualitative examples")
    out.append("")
    # Show the top delta-quality question as the headline example
    ex_rows = sorted(
        on_metrics,
        key=lambda r: (r["on"]["quality"] - r["off"]["quality"]),
        reverse=True,
    )
    for r in ex_rows[:3]:
        mapping = assignments.get(r["qid"], {"A": "mcp_on", "B": "mcp_off"})
        out.append(f"### `{r['qid']}` — {r['category']}")
        out.append("")
        out.append(f"> {r['question']}")
        out.append("")
        out.append(
            f"- **MCP** quality {r['on']['quality']}/5, file recall {r['on']['file_recall']*100:.0f}%, "
            f"hallucinations {r['on']['hallucinations']}"
        )
        if r["on_notes"]:
            out.append(f"  - grader: *{r['on_notes']}*")
        out.append(
            f"- **No MCP** quality {r['off']['quality']}/5, file recall {r['off']['file_recall']*100:.0f}%, "
            f"hallucinations {r['off']['hallucinations']}"
        )
        if r["off_notes"]:
            out.append(f"  - grader: *{r['off_notes']}*")
        if r.get("winner_reason"):
            out.append(f"- **Winner**: {mapping.get(r['winner'], 'tie')} — {r['winner_reason']}")
        out.append("")

    out.append("## Methodology")
    out.append("")
    out.append(
        "- Each question is answered twice by Claude Code via `claude -p` with "
        "identical filesystem access (same workspace, same tool allowlist). The "
        "only difference is whether `mcp__ottu-knowledge__*` tools are available."
    )
    out.append(
        "- Answers are graded by a fresh Claude session (no conversation memory), "
        "given the ground truth and both answers as **Answer A** / **Answer B** — "
        "randomly assigned per question so the grader cannot tell which is MCP."
    )
    out.append(
        "- Raw outputs are kept in `mcp_on/` and `mcp_off/` subdirs. A/B→mode "
        "mapping is in `assignments.json`. Scores in `grades.json`."
    )
    out.append(
        "- **Cost per correct citation** is the headline economic metric: "
        "it normalizes token cost by the amount of *verifiable* work delivered."
    )
    out.append("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--questions", default=str(HERE / "questions.yaml"))
    ap.add_argument("--out", default=None, help="Output path (default: <run-dir>/report.md).")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    out_path = Path(args.out) if args.out else run_dir / "report.md"
    text = build_report(run_dir, Path(args.questions))
    out_path.write_text(text)
    print(f"Report written: {out_path}")
    print()
    print(text[:1800])


if __name__ == "__main__":
    main()
