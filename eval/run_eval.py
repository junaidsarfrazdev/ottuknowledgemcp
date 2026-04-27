"""Run each question twice (MCP on + off) and save raw Claude Code outputs.

Usage:
    python eval/run_eval.py                 # all questions
    python eval/run_eval.py --ids Q1 Q2     # subset
    python eval/run_eval.py --only-missing  # resume interrupted run

Outputs: eval/results/<timestamp>/{mcp_on,mcp_off}/<id>.json + <id>.txt
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent

DEFAULT_ALLOWED_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git show:*)",
    "Bash(ls:*)",
    "Bash(cat:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(wc:*)",
    "Bash(grep:*)",
    "Bash(find:*)",
    "Bash(rg:*)",
]

MCP_TOOLS = [
    "mcp__ottu-knowledge__search_multi",
    "mcp__ottu-knowledge__search_ottu_code",
    "mcp__ottu-knowledge__search_ottu_docs",
    "mcp__ottu-knowledge__get_file_chunks",
    "mcp__ottu-knowledge__find_file",
    "mcp__ottu-knowledge__list_ottu_sources",
    "mcp__ottu-knowledge__check_ottu_freshness",
]


def _ensure_mcp_on_config() -> Path:
    """Write eval/mcp_on.json pointing at THIS repo's venv + server.py."""
    cfg_path = HERE / "mcp_on.json"
    venv_py = PROJECT_ROOT / "venv" / "bin" / "python"
    if not venv_py.exists():
        # Windows layout
        venv_py = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
    server_py = PROJECT_ROOT / "server.py"
    if not venv_py.exists() or not server_py.exists():
        raise SystemExit(
            f"Can't find venv python or server.py under {PROJECT_ROOT}. "
            f"Run bash setup.sh / setup.ps1 first."
        )
    cfg = {
        "mcpServers": {
            "ottu-knowledge": {
                "type": "stdio",
                "command": str(venv_py),
                "args": [str(server_py)],
            }
        }
    }
    cfg_path.write_text(json.dumps(cfg, indent=2))
    return cfg_path


def _run_claude(
    prompt: str,
    *,
    mcp_config: Path,
    allowed_tools: list[str],
    cwd: Path,
    model: str | None,
    timeout_s: int,
    max_budget_usd: float | None,
) -> dict:
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-mode",
        "bypassPermissions",
        "--allowed-tools",
        ",".join(allowed_tools),
    ]
    if model:
        cmd += ["--model", model]
    if max_budget_usd is not None:
        cmd += ["--max-budget-usd", str(max_budget_usd)]

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "_error": "timeout",
            "_timeout_s": timeout_s,
            "_wall_clock_s": time.time() - start,
            "_stdout": (e.stdout or "")[:5000] if hasattr(e, "stdout") else "",
            "_stderr": (e.stderr or "")[:5000] if hasattr(e, "stderr") else "",
        }
    elapsed = time.time() - start

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = {
            "_error": "unparseable_json",
            "_stdout": proc.stdout[:8000],
            "_stderr": proc.stderr[:4000],
        }
    data["_wall_clock_s"] = elapsed
    data["_exit_code"] = proc.returncode
    if proc.returncode != 0 and "_error" not in data:
        data["_error"] = f"non_zero_exit_{proc.returncode}"
        data["_stderr"] = proc.stderr[:4000]
    return data


def _extract_answer_text(claude_json: dict) -> str:
    """Claude's `-p --output-format=json` returns the final text under 'result'."""
    for k in ("result", "text", "content"):
        v = claude_json.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _load_questions(path: Path, ids: list[str] | None) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    qs = data.get("questions") or []
    if ids:
        wanted = set(ids)
        qs = [q for q in qs if q.get("id") in wanted]
        missing = wanted - {q["id"] for q in qs}
        if missing:
            raise SystemExit(f"Unknown question id(s): {', '.join(sorted(missing))}")
    return qs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions", default=str(HERE / "questions.yaml"))
    ap.add_argument(
        "--workspace",
        default=os.environ.get("OTTU_WORKSPACE"),
        help="CWD for claude runs. Default: $OTTU_WORKSPACE.",
    )
    ap.add_argument("--output-dir", default=str(HERE / "results"))
    ap.add_argument("--ids", nargs="*", help="Run only these question IDs.")
    ap.add_argument("--model", default=None, help="Override model (e.g. sonnet, opus).")
    ap.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-run timeout in seconds (default 600).",
    )
    ap.add_argument(
        "--max-budget",
        type=float,
        default=2.0,
        help="Per-run USD cap passed to claude --max-budget-usd (default 2.0).",
    )
    ap.add_argument(
        "--resume",
        metavar="TIMESTAMP_DIR",
        help="Name of an existing results/<ts> dir to append to (skip already-run questions).",
    )
    ap.add_argument(
        "--only",
        choices=["on", "off", "both"],
        default="both",
        help="Run only MCP-on, only MCP-off, or both (default both).",
    )
    args = ap.parse_args()

    if not args.workspace:
        raise SystemExit(
            "Missing --workspace (and no OTTU_WORKSPACE in env). "
            "Point it at the directory containing your cloned Ottu repos."
        )
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists():
        raise SystemExit(f"Workspace directory does not exist: {workspace}")

    mcp_on = _ensure_mcp_on_config()
    mcp_off = HERE / "mcp_off.json"
    if not mcp_off.exists():
        raise SystemExit(f"Missing {mcp_off}. Did you move eval/mcp_off.json?")

    questions = _load_questions(Path(args.questions), args.ids)
    if not questions:
        raise SystemExit("No questions to run.")

    results_root = Path(args.output_dir)
    if args.resume:
        run_dir = results_root / args.resume
        if not run_dir.exists():
            raise SystemExit(f"--resume dir not found: {run_dir}")
    else:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        run_dir = results_root / ts
        run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "mcp_on").mkdir(exist_ok=True)
    (run_dir / "mcp_off").mkdir(exist_ok=True)

    meta_path = run_dir / "metadata.json"
    if not meta_path.exists():
        meta_path.write_text(
            json.dumps(
                {
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "workspace": str(workspace),
                    "model": args.model,
                    "timeout_s": args.timeout,
                    "max_budget_usd": args.max_budget,
                    "questions_file": str(Path(args.questions).resolve()),
                    "question_ids": [q["id"] for q in questions],
                },
                indent=2,
            )
        )

    modes = []
    if args.only in ("on", "both"):
        modes.append(("mcp_on", mcp_on, DEFAULT_ALLOWED_TOOLS + MCP_TOOLS))
    if args.only in ("off", "both"):
        modes.append(("mcp_off", mcp_off, DEFAULT_ALLOWED_TOOLS))

    total = len(questions) * len(modes)
    done = 0
    t_run_start = time.time()
    for q in questions:
        qid = q["id"]
        prompt = q["question"].strip()
        for mode_name, cfg, tools in modes:
            done += 1
            out_json = run_dir / mode_name / f"{qid}.json"
            out_txt = run_dir / mode_name / f"{qid}.txt"
            if out_json.exists():
                print(f"[{done}/{total}] {mode_name}/{qid}  (cached, skipping)")
                continue
            print(f"[{done}/{total}] {mode_name}/{qid}  running…", flush=True)
            t0 = time.time()
            result = _run_claude(
                prompt,
                mcp_config=cfg,
                allowed_tools=tools,
                cwd=workspace,
                model=args.model,
                timeout_s=args.timeout,
                max_budget_usd=args.max_budget,
            )
            dt = time.time() - t0
            out_json.write_text(json.dumps(result, indent=2))
            out_txt.write_text(_extract_answer_text(result))
            status = "ERR " + result.get("_error", "") if "_error" in result else "ok"
            turns = result.get("num_turns", "?")
            cost = result.get("total_cost_usd", "?")
            print(f"    {status}  turns={turns}  cost=${cost}  wall={dt:.1f}s")

    elapsed = time.time() - t_run_start
    print(f"\nDone in {elapsed/60:.1f} min. Results: {run_dir}")
    print(f"Next: python eval/grade.py --run-dir {run_dir}")


if __name__ == "__main__":
    main()
