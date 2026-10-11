import json
import struct
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .hardware import fingerprint, hardware_info
from .reference import reference_kernel
from .scoring import score, summarize
from .storage import save_run
from .submission import digest, load_submission, local_path
from .tasks import SUITE_VERSION, TASKS, TIERS
from .validation import POLICY, InvalidKernel, assemble, validate_bytes


def build_worker(folder: Path):
    source = Path(__file__).with_name("native") / "worker.c"
    output = folder / "worker"
    subprocess.run(
        ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(output)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return output


def validate_submission(root, tasks=None, tiers=None):
    manifest = load_submission(root)
    results = []
    with tempfile.TemporaryDirectory(prefix="movbench-validate-") as tmp:
        for item in manifest["kernels"]:
            if tasks and item["task"] not in tasks or tiers and item["tier"] not in tiers:
                continue
            result = {"task": item["task"], "tier": item["tier"], "valid": False}
            try:
                table_file = local_path(root, item["tables"])
                memory = table_file.stat().st_size + item.get("scratch_bytes", 0)
                if memory > TIERS[item["tier"]]:
                    raise InvalidKernel(
                        f"Uses {memory} bytes; budget is {TIERS[item['tier']]} bytes"
                    )
                source_file = local_path(root, item["source"])
                if source_file.stat().st_size > POLICY["max_source_bytes"]:
                    raise InvalidKernel("Source exceeds source size limit")
                code = assemble(source_file.read_text(), Path(tmp))
                result.update(valid=True, memory_bytes=memory, **validate_bytes(code))
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                result["error"] = str(exc)
            results.append(result)
    return results


def run_submission(
    root: Path,
    output: Path,
    db: Path,
    tasks,
    tiers,
    seed=1729,
    repeats=7,
    trial_ms=10,
    cpu=None,
    target="zen5",
    allow_other_cpu=False,
    timeout=45,
    progress=None,
):
    manifest = load_submission(root)
    hardware = hardware_info(target)
    if not hardware["required_features_present"]:
        raise ValueError(f"Missing required AVX-512 features: {hardware['missing_features']}")
    if not hardware["target_verified"] and not allow_other_cpu:
        raise ValueError(
            f"Host is {hardware['architecture']}; target is {target}. "
            "Use --allow-other-cpu for a separately labeled development cohort."
        )
    cpu = hardware["available_cpus"][0] if cpu is None else cpu
    if cpu not in hardware["available_cpus"]:
        raise ValueError(f"CPU {cpu} is not in the process affinity mask")
    if not 3 <= repeats <= 99 or not 1 <= trial_ms <= 1000 or timeout <= 0:
        raise ValueError("repeats: 3–99; trial-ms: 1–1000; timeout must be positive")
    settings = {
        "seed": seed,
        "repeats": repeats,
        "trial_ms": trial_ms,
        "cpu": cpu,
        "timeout_seconds": timeout,
        "cache": "warm, 64-input rotating pool",
        "measurement": "wall ns/kernel, includes input copy and dispatch; paired alternating batches",
    }
    package = Path(__file__).parent
    harness_hash = digest(
        b"".join(
            (package / p).read_bytes()
            for p in [
                "native/worker.c",
                "reference.py",
                "tasks.py",
                "policy.json",
                "scoring.py",
                "validation.py",
            ]
        )
    )
    cohort = fingerprint(
        {
            "hardware": hardware,
            "settings": settings,
            "tasks": tasks,
            "tiers": tiers,
            "suite": SUITE_VERSION,
            "harness": harness_hash,
        }
    )
    run = {
        "schema_version": 1,
        "id": uuid.uuid4().hex[:12],
        "created": datetime.now(UTC).isoformat(),
        "model": manifest["model"],
        "metadata": manifest.get("metadata", {}),
        "version": __version__,
        "suite": SUITE_VERSION,
        "policy": POLICY["id"],
        "hardware": hardware,
        "settings": settings,
        "harness_sha256": harness_hash,
        "cohort": cohort,
        "tasks": tasks,
        "tiers": tiers,
        "submission_sha256": digest((root / "submission.json").read_bytes()),
        "results": [],
    }
    run_dir = output / run["id"]
    run_dir.mkdir(parents=True, exist_ok=False)
    worker = build_worker(run_dir)
    submitted = {(x["task"], x["tier"]): x for x in manifest["kernels"]}
    for tier in tiers:
        for task_id in tasks:
            started = time.monotonic()
            task = TASKS[task_id]
            cell = {"task": task_id, "tier": tier, "valid": False, "score": 0.0}
            folder = run_dir / tier / task_id
            folder.mkdir(parents=True)
            item = submitted.get((task_id, tier))
            try:
                if item is None:
                    raise InvalidKernel("Missing submission for this task/tier")
                table_path = local_path(root, item["tables"])
                memory = table_path.stat().st_size + item.get("scratch_bytes", 0)
                if memory > TIERS[tier]:
                    raise InvalidKernel(f"Memory {memory} > {TIERS[tier]} byte budget")
                source_path = local_path(root, item["source"])
                if source_path.stat().st_size > POLICY["max_source_bytes"]:
                    raise InvalidKernel("Source exceeds size limit")
                source, tables = source_path.read_text(), table_path.read_bytes()
                (folder / "kernel.s").write_text(source)
                (folder / "tables.bin").write_bytes(tables)
                code = assemble(source, folder / "candidate")
                validation = validate_bytes(code)
                base = reference_kernel(task_id, tier)
                base_dir = folder / "reference"
                base_code = assemble(base["source"], base_dir)
                (base_dir / "kernel.s").write_text(base["source"])
                (base_dir / "tables.bin").write_bytes(base["tables"])
                fixtures = task.cases(seed)
                fixture_bytes = struct.pack(
                    "<III", len(fixtures), task.input_bytes, task.output_bytes
                )
                fixture_bytes += b"".join(a + b for a, b in fixtures)
                (folder / "fixtures.bin").write_bytes(fixture_bytes)
                cell.update(
                    memory_bytes=memory,
                    tables_bytes=len(tables),
                    scratch_bytes=item.get("scratch_bytes", 0),
                    validation=validation,
                    code_sha256=digest(code),
                    tables_sha256=digest(tables),
                    reference_sha256=digest(base_code + base["tables"]),
                    fixtures_sha256=digest(fixture_bytes),
                )
                args = [
                    str(worker.resolve()),
                    str((folder / "candidate/code.bin").resolve()),
                    str((folder / "tables.bin").resolve()),
                    str(item.get("scratch_bytes", 0)),
                    str((base_dir / "code.bin").resolve()),
                    str((base_dir / "tables.bin").resolve()),
                    str(base["scratch_bytes"]),
                    str((folder / "fixtures.bin").resolve()),
                    str(repeats),
                    str(trial_ms),
                    str(cpu),
                    str(seed),
                ]
                (folder / "command.json").write_text(json.dumps(args, indent=2))
                measured = subprocess.run(
                    args, check=False, capture_output=True, text=True, timeout=timeout
                )
                (folder / "stdout.txt").write_text(measured.stdout)
                (folder / "stderr.txt").write_text(measured.stderr)
                if measured.returncode:
                    detail = (
                        measured.stderr.strip()
                        or f"Worker terminated with code {measured.returncode} (fault or illegal instruction)"
                    )
                    raise InvalidKernel(detail)
                raw = json.loads(measured.stdout)
                candidate_timing, reference_timing = (
                    summarize(raw["candidate_ns"]),
                    summarize(raw["reference_ns"]),
                )
                cell.update(
                    valid=True,
                    timing=candidate_timing,
                    reference_timing=reference_timing,
                    speedup=reference_timing["median_ns"] / candidate_timing["median_ns"],
                    score=score(True, candidate_timing["median_ns"], reference_timing["median_ns"]),
                    cases=raw["cases"],
                    iterations_per_trial=raw["iterations_per_trial"],
                )
            except subprocess.TimeoutExpired:
                cell["error"] = f"Timed out after {timeout} seconds"
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                cell["error"] = str(exc)
            cell["elapsed_seconds"] = time.monotonic() - started
            run["results"].append(cell)
            # Keep an incremental artifact so interrupted runs retain evidence.
            (run_dir / "run.json").write_text(json.dumps(run, indent=2) + "\n")
            if progress:
                progress(cell)
    run["complete"] = True
    (run_dir / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    save_run(db, run)
    return run, run_dir
