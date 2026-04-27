"""Score raw eval outputs against ground truth using Claude-as-judge.

Blind: each question's two answers are relabeled "Answer A" / "Answer B"
(random coin flip per question) so the grader cannot tell which is MCP.

Usage:
    python eval/grade.py --run-dir eval/results/<timestamp>
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent


GRADER_SYSTEM = """You are a strict, impartial grader evaluating AI answers against a known ground-truth.

You return ONLY valid JSON matching the provided schema. No preamble, no postamble, no code fences.
"""

GRADER_PROMPT_TMPL = """Grade two answers to the same engineering question.

You have the ground-truth citations (files, functions, terms) the answer MUST cover,
and a list of plausible-sounding things that are actually hallucinations.

Output JSON ONLY. Do not include any markdown, backticks, or commentary.

QUESTION:
{question}

GROUND TRUTH (must be cited / mentioned in a correct answer):
  files:              {must_files}
  functions:          {must_functions}
  repos:              {must_repos}
  terms/phrases:      {must_mention}

BONUS (nice-to-have but not required):
  {bonus}

HALLUCINATIONS (if any of these appear, flag them — they do NOT exist in the codebase):
  {must_not_hallucinate}

--- ANSWER A ---
{answer_a}

--- ANSWER B ---
{answer_b}

Return this JSON schema exactly:

{{
  "A": {{
    "cited_files":        [ list of file paths the answer actually names ],
    "cited_functions":    [ list of functions actually named ],
    "cited_terms":        [ must-mention terms actually present in the text ],
    "bonus_hits":         [ bonus items actually present ],
    "hallucinations":     [ any must_not_hallucinate items named, OR plausible-looking but invented paths/functions ],
    "correct_file_citations":      integer count of ground-truth files cited,
    "correct_function_citations":  integer count of ground-truth functions cited,
    "correct_term_citations":      integer count of ground-truth terms cited,
    "total_file_citations":        integer count of files cited (correct + wrong + hallucinated),
    "quality_1_5":        integer 1-5 (5 = great, 1 = useless/wrong),
    "notes":              "one-sentence grader note"
  }},
  "B": {{ ...same shape... }},
  "winner":               "A" | "B" | "tie",
  "winner_reason":        "one-sentence comparative note"
}}
"""


def _load_questions(path: Path) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text())
    return {q["id"]: q for q in data.get("questions") or []}


def _fmt_list(xs) -> str:
    if not xs:
        return "(none)"
    return ", ".join(str(x) for x in xs)


def _load_answer(run_dir: Path, mode: str, qid: str) -> tuple[str, dict]:
    json_path = run_dir / mode / f"{qid}.json"
    txt_path = run_dir / mode / f"{qid}.txt"
    if not txt_path.exists():
        return "", {}
    text = txt_path.read_text()
    raw = json.loads(json_path.read_text()) if json_path.exists() else {}
    return text, raw


def _parse_grader_json(text: str) -> dict:
    """Grader is instructed to return pure JSON, but strip fences if present."""
    t = text.strip()
    if t.startswith("```"):
        m = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", t, flags=re.DOTALL)
        if m:
            t = m.group(1)
    return json.loads(t)


def _grade_one(
    question: dict,
    answer_a: str,
    answer_b: str,
    model: str | None,
    timeout_s: int,
    max_retries: int = 3,
) -> dict:
    """Grade one question. Retries transient failures (timeout, non-zero exit,
    unparseable JSON) with exponential backoff so a single hiccup doesn't tank
    the whole run."""
    mc = question.get("must_cite") or {}
    prompt = GRADER_PROMPT_TMPL.format(
        question=question["question"].strip(),
        must_files=_fmt_list(mc.get("files")),
        must_functions=_fmt_list(mc.get("functions")),
        must_repos=_fmt_list(mc.get("repos")),
        must_mention=_fmt_list(question.get("must_mention")),
        bonus=_fmt_list(question.get("bonus")),
        must_not_hallucinate=_fmt_list(question.get("must_not_hallucinate")),
        answer_a=answer_a or "(empty answer — run failed or produced no output)",
        answer_b=answer_b or "(empty answer — run failed or produced no output)",
    )

    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--append-system-prompt",
        GRADER_SYSTEM,
        "--allowed-tools",
        "",  # grader needs no tools
        "--permission-mode",
        "bypassPermissions",
    ]
    if model:
        cmd += ["--model", model]

    last_err: dict | None = None
    delay = 2.0
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            last_err = {
                "_error": "grader_timeout",
                "_attempt": attempt,
                "_timeout_s": timeout_s,
                "_elapsed": time.time() - t0,
                "_stdout": (exc.stdout or "")[:2000] if isinstance(exc.stdout, str) else "",
                "_stderr": (exc.stderr or "")[:2000] if isinstance(exc.stderr, str) else "",
            }
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            return last_err
        elapsed = time.time() - t0

        if proc.returncode != 0:
            last_err = {
                "_error": f"grader_exit_{proc.returncode}",
                "_attempt": attempt,
                "_stdout": proc.stdout[:2000],
                "_stderr": proc.stderr[:2000],
                "_elapsed": elapsed,
            }
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            return last_err

        try:
            outer = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last_err = {
                "_error": "grader_stdout_not_json",
                "_attempt": attempt,
                "_stdout": proc.stdout[:4000],
                "_stderr": proc.stderr[:2000],
                "_elapsed": elapsed,
            }
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            return last_err

        body = outer.get("result") or ""
        try:
            parsed = _parse_grader_json(body)
        except (json.JSONDecodeError, AttributeError) as e:
            last_err = {
                "_error": f"grader_json_parse: {e}",
                "_attempt": attempt,
                "_body": body[:4000],
                "_elapsed": elapsed,
            }
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            return last_err

        parsed["_elapsed"] = elapsed
        parsed["_attempts"] = attempt
        parsed["_grader_meta"] = {
            "num_turns": outer.get("num_turns"),
            "cost_usd": outer.get("total_cost_usd"),
        }
        return parsed

    # Should be unreachable — every failure path either retries or returns above.
    return last_err or {"_error": "grader_unknown_failure"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-dir", required=True, help="eval/results/<timestamp> dir.")
    ap.add_argument("--questions", default=str(HERE / "questions.yaml"))
    ap.add_argument("--model", default=None, help="Grader model (e.g. opus, sonnet).")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42, help="Seed for A/B shuffle.")
    ap.add_argument("--ids", nargs="*", help="Grade only these question IDs.")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        raise SystemExit(f"Run dir not found: {run_dir}")

    questions_by_id = _load_questions(Path(args.questions))
    rng = random.Random(args.seed)
    grades = {}
    assignments = {}  # qid -> {"A": "mcp_on"|"mcp_off", "B": ...}

    qids = args.ids or sorted(
        p.stem
        for p in (run_dir / "mcp_on").glob("*.txt")
        if (run_dir / "mcp_off" / f"{p.stem}.txt").exists()
    )
    if not qids:
        raise SystemExit("No paired (mcp_on + mcp_off) outputs found to grade.")

    for i, qid in enumerate(qids, 1):
        if qid not in questions_by_id:
            print(f"[{i}/{len(qids)}] {qid}  ⚠ no ground truth in questions.yaml — skipping")
            continue
        q = questions_by_id[qid]
        ans_on, _ = _load_answer(run_dir, "mcp_on", qid)
        ans_off, _ = _load_answer(run_dir, "mcp_off", qid)
        if not ans_on and not ans_off:
            print(f"[{i}/{len(qids)}] {qid}  both answers empty — skipping")
            continue

        # Randomize A/B assignment per question
        if rng.random() < 0.5:
            mapping = {"A": "mcp_on", "B": "mcp_off"}
            ans_a, ans_b = ans_on, ans_off
        else:
            mapping = {"A": "mcp_off", "B": "mcp_on"}
            ans_a, ans_b = ans_off, ans_on
        assignments[qid] = mapping

        print(f"[{i}/{len(qids)}] grading {qid}  (A={mapping['A']}, B={mapping['B']})…", flush=True)
        g = _grade_one(q, ans_a, ans_b, model=args.model, timeout_s=args.timeout)
        grades[qid] = g
        if "_error" in g:
            print(f"    ERR {g['_error']}")
        else:
            print(
                f"    winner={g.get('winner')}  "
                f"A={g.get('A',{}).get('quality_1_5')}/5  "
                f"B={g.get('B',{}).get('quality_1_5')}/5"
            )

    out_scores = run_dir / "grades.json"
    out_assign = run_dir / "assignments.json"
    out_scores.write_text(json.dumps(grades, indent=2))
    out_assign.write_text(json.dumps(assignments, indent=2))
    print(f"\nGrades written: {out_scores}")
    print(f"Assignments (A/B → mcp_on/off): {out_assign}")
    print(f"Next: python eval/report.py --run-dir {run_dir}")


if __name__ == "__main__":
    main()
