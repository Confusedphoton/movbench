import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from .hardware import hardware_info
from .reference import reference_kernel
from .runner import run_submission, validate_submission
from .storage import get_runs, leaderboard
from .submission import write_submission
from .tasks import TASKS, TIERS
from .validation import POLICY

DEFAULT_DB = Path(".movbench/scores.sqlite3")


def selected(value, available):
    values = value.split(",") if value else list(available)
    if not values or any(x not in available for x in values) or len(values) != len(set(values)):
        raise ValueError(f"Choose unique comma-separated values from {', '.join(available)}")
    return sorted(values)


def generate(args):
    tasks, tiers = selected(args.tasks, TASKS), selected(args.tiers, TIERS)
    if (args.out / "submission.json").exists():
        raise ValueError(
            f"A submission already exists at {args.out}; choose another output directory"
        )
    kernels = []
    for tier in tiers:
        for task_id in tasks:
            folder = args.out / tier / task_id
            folder.mkdir(parents=True, exist_ok=True)
            spec = {
                **TASKS[task_id].spec(),
                "tier": tier,
                "memory_budget_bytes": TIERS[tier],
                "policy": POLICY,
                "abi": {"input": "rdi", "output": "rsi", "tables": "rdx", "scratch": "rcx"},
            }
            (folder / "task.json").write_text(json.dumps(spec, indent=2) + "\n")
            prompt = (
                f"Implement {task_id}: {TASKS[task_id].description}\n"
                f"Memory budget: {TIERS[tier]} bytes for tables + scratch. Target: AMD Zen 5 AVX-512.\n"
                "Write kernel.s (Intel assembly body, no directives/labels/ret), tables.bin, "
                "and kernel.json with scratch_bytes. Use only the supplied exact policy.\n"
                "rdi=input, rsi=output, rdx=tables, rcx=scratch; these registers must be preserved.\n"
                "Writable scalar regs: rax,r8-r11; vector and mask regs allowed. "
                "Memory bases must be rdi/rsi/rdx/rcx. All outputs must be written; scratch "
                "and other registers have no guaranteed initial contents.\n"
            )
            (folder / "prompt.txt").write_text(prompt)
            if args.compiler_command:
                replacements = {
                    "spec": str((folder / "task.json").resolve()),
                    "output": str(folder.resolve()),
                    "task": task_id,
                    "tier": tier,
                }
                command = [
                    token.format(**replacements) for token in shlex.split(args.compiler_command)
                ]
                try:
                    result = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=args.compile_timeout,
                    )
                    (folder / "compiler.stdout.txt").write_text(result.stdout)
                    (folder / "compiler.stderr.txt").write_text(result.stderr)
                    if result.returncode:
                        raise ValueError(f"Compiler exited {result.returncode}")
                    kernel = {
                        "source": (folder / "kernel.s").read_text(),
                        "tables": (folder / "tables.bin").read_bytes(),
                        "scratch_bytes": json.loads((folder / "kernel.json").read_text())[
                            "scratch_bytes"
                        ],
                    }
                except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                    (folder / "compiler.error.txt").write_text(str(exc))
                    kernel = {
                        "source": "# Compilation failed: see compiler.error.txt\n",
                        "tables": b"",
                        "scratch_bytes": 0,
                    }
            elif args.backend in {"reference", "shuffle"}:
                kernel = reference_kernel(task_id, tier, args.backend == "shuffle")
            else:
                kernel = {
                    "source": "# Add your instruction body here. See prompt.txt and task.json.\n",
                    "tables": b"",
                    "scratch_bytes": 0,
                }
            kernels.append({**kernel, "task": task_id, "tier": tier})
    write_submission(
        args.out,
        args.model,
        kernels,
        {
            "backend": "compiler-command" if args.compiler_command else args.backend,
            "compiler_command": args.compiler_command,
        },
    )
    print(f"Generated {len(kernels)} kernel cells in {args.out / 'submission.json'}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="MOVBench · compute by moving data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tasks", help="List exact kernel specifications and memory tiers")
    sub.add_parser("policy", help="Print the instruction whitelist")
    doctor = sub.add_parser("doctor", help="Inspect hardware and toolchain")
    doctor.add_argument("--target", choices=["zen5", "zen4"], default="zen5")
    gen = sub.add_parser("generate", help="Generate task prompts, submissions, or compiler runs")
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--model", default="my-model")
    gen.add_argument("--backend", choices=["template", "reference", "shuffle"], default="template")
    gen.add_argument("--tasks")
    gen.add_argument("--tiers")
    gen.add_argument("--compiler-command")
    gen.add_argument("--compile-timeout", type=float, default=120)
    val = sub.add_parser(
        "validate", help="Validate machine code and memory budgets without execution"
    )
    val.add_argument("submission", type=Path)
    val.add_argument("--tasks")
    val.add_argument("--tiers")
    run = sub.add_parser("run", help="Verify outputs and measure kernels against legal references")
    run.add_argument("submission", type=Path)
    run.add_argument("--tasks")
    run.add_argument("--tiers")
    run.add_argument("--out", type=Path, default=Path("runs"))
    run.add_argument("--db", type=Path, default=DEFAULT_DB)
    run.add_argument("--target", choices=["zen5", "zen4"], default="zen5")
    run.add_argument("--allow-other-cpu", action="store_true")
    run.add_argument("--seed", type=int, default=1729)
    run.add_argument("--repeats", type=int, default=7)
    run.add_argument("--trial-ms", type=int, default=10)
    run.add_argument("--cpu", type=int)
    run.add_argument("--timeout", type=float, default=45)
    scores = sub.add_parser("scores", help="Show scores from comparable completed runs")
    scores.add_argument("--db", type=Path, default=DEFAULT_DB)
    scores.add_argument("--cohort")
    scores.add_argument("--mode", choices=["log", "elo"], default="log")
    scores.add_argument("--json", action="store_true")
    importer = sub.add_parser(
        "import-run", help="Index trusted completed run JSON without executing code"
    )
    importer.add_argument("files", type=Path, nargs="+")
    importer.add_argument("--db", type=Path, default=DEFAULT_DB)
    serve = sub.add_parser("serve", help="Serve the score dashboard")
    serve.add_argument("--db", type=Path, default=DEFAULT_DB)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    try:
        if args.command == "tasks":
            print(
                json.dumps({"tiers": TIERS, "tasks": [t.spec() for t in TASKS.values()]}, indent=2)
            )
        elif args.command == "policy":
            print(json.dumps(POLICY, indent=2))
        elif args.command == "doctor":
            print(json.dumps(hardware_info(args.target), indent=2))
        elif args.command == "generate":
            generate(args)
        elif args.command == "validate":
            result = validate_submission(
                args.submission, selected(args.tasks, TASKS), selected(args.tiers, TIERS)
            )
            print(json.dumps(result, indent=2))
            return 0 if result and all(r["valid"] for r in result) else 1
        elif args.command == "run":

            def progress(cell):
                detail = (
                    f"{cell['score']:.1f} pts · {cell['speedup']:.2f}×"
                    if cell["valid"]
                    else cell.get("error", "invalid")
                )
                print(f"{cell['tier']:6} {cell['task']:16} {detail}", flush=True)

            result, folder = run_submission(
                args.submission,
                args.out,
                args.db,
                selected(args.tasks, TASKS),
                selected(args.tiers, TIERS),
                seed=args.seed,
                repeats=args.repeats,
                trial_ms=args.trial_ms,
                cpu=args.cpu,
                target=args.target,
                allow_other_cpu=args.allow_other_cpu,
                timeout=args.timeout,
                progress=progress,
            )
            print(f"Run {result['id']} saved to {folder / 'run.json'} · cohort {result['cohort']}")
            return 0 if all(r["valid"] for r in result["results"]) else 1
        elif args.command == "scores":
            data = leaderboard(get_runs(args.db), args.cohort)
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print(f"Cohort: {data['cohort'] or 'no runs'} · latest complete run per model")
                for row in sorted(
                    data["rows"], key=lambda r: -r["elo" if args.mode == "elo" else "score"]
                ):
                    print(
                        f"{row['model']:30} {row['score']:8.2f} pts  {row['elo']:7.1f} Elo  {row['valid']}/{row['total']} valid"
                    )
        elif args.command == "import-run":
            from .storage import save_run

            for path in args.files:
                run = json.loads(path.read_text())
                if run.get("schema_version") != 1 or not run.get("complete"):
                    raise ValueError(f"{path}: expected a completed schema-version-1 run")
                expected = {(task, tier) for task in run["tasks"] for tier in run["tiers"]}
                actual = {(r["task"], r["tier"]) for r in run["results"]}
                if actual != expected or len(actual) != len(run["results"]):
                    raise ValueError(f"{path}: incomplete or duplicate task/tier cells")
                if any(t not in TASKS for t in run["tasks"]) or any(
                    t not in TIERS for t in run["tiers"]
                ):
                    raise ValueError(f"{path}: unknown task/tier")
                if any(r["id"] == run["id"] for r in get_runs(args.db)):
                    print(f"Already indexed {run['id']}")
                    continue
                save_run(args.db, run)
                print(f"Indexed {run['id']} · {run['model']} · source hardware retained")
        elif args.command == "serve":
            from .server import serve as start_server

            start_server(args.db, args.host, args.port)
    except (ValueError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print(f"movbench: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
