import json
import platform
import random
import shutil
import struct
import subprocess

import pytest

from movbench.hardware import hardware_info
from movbench.reference import reference_kernel
from movbench.runner import build_worker
from movbench.tasks import TASKS
from movbench.validation import assemble

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux" or platform.machine() != "x86_64" or not shutil.which("gcc"),
    reason="Linux x86-64 GCC required",
)


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    return build_worker(tmp_path_factory.mktemp("worker"))


def execute(worker, tmp_path, task_id, tier, optimized=False, source=None, scratch=None):
    task = TASKS[task_id]
    kernel = reference_kernel(task_id, tier, optimized)
    candidate, reference = tmp_path / "candidate", tmp_path / "reference"
    assemble(source if source is not None else kernel["source"], candidate)
    assemble(kernel["source"], reference)
    for folder in [candidate, reference]:
        (folder / "tables.bin").write_bytes(kernel["tables"])
    rng = random.Random(42)
    inputs = [
        bytes(task.input_bytes),
        bytes([255]) * task.input_bytes,
        bytes([85, 170]) * (task.input_bytes // 2),
    ]
    inputs += [rng.randbytes(task.input_bytes) for _ in range(64)]
    if task_id == "madd_u8":
        inputs = [
            bytes(value if i % 4 != 3 else 0 for i, value in enumerate(data)) for data in inputs
        ]
    fixtures = struct.pack("<III", len(inputs), task.input_bytes, task.output_bytes)
    fixtures += b"".join(data + task.oracle(data) for data in inputs)
    (tmp_path / "fixtures.bin").write_bytes(fixtures)
    args = [
        str(worker),
        str(candidate / "code.bin"),
        str(candidate / "tables.bin"),
        str(kernel["scratch_bytes"] if scratch is None else scratch),
        str(reference / "code.bin"),
        str(reference / "tables.bin"),
        str(kernel["scratch_bytes"]),
        str(tmp_path / "fixtures.bin"),
        "3",
        "1",
        str(hardware_info()["available_cpus"][0]),
        "42",
    ]
    return subprocess.run(args, check=False, capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("task", list(TASKS))
@pytest.mark.parametrize("tier", ["tiny", "small"])
def test_reference_native_oracle_and_measurement(worker, tmp_path, task, tier):
    result = execute(worker, tmp_path, task, tier)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["valid"]
    assert len(data["candidate_ns"]) == len(data["reference_ns"]) == 3
    assert all(x > 0 for x in data["candidate_ns"])
    assert data["iterations_per_trial"]["candidate"] >= 64


@pytest.mark.parametrize("task", ["route_64", "transpose_8x8"])
def test_vector_rearrangements_against_scalar_oracle(worker, tmp_path, task):
    if not hardware_info()["required_features_present"]:
        pytest.skip("AVX-512 VBMI required")
    result = execute(worker, tmp_path, task, "tiny", optimized=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tier", ["medium", "large"])
def test_fused_multiply_add_table_backends(worker, tmp_path, tier):
    result = execute(worker, tmp_path, "madd_u8", tier)
    assert result.returncode == 0, result.stderr


def test_wrong_output_fails_and_canary_overwrite_fails(worker, tmp_path):
    result = execute(worker, tmp_path, "sum_u8", "small", source="mov BYTE PTR [rsi], 0")
    assert result.returncode == 4
    assert "Wrong output" in result.stderr


def test_output_guard_fault_is_contained_in_worker(worker, tmp_path):
    result = execute(worker, tmp_path, "sum_u8", "small", source="mov BYTE PTR [rsi + 1], 0")
    assert result.returncode < 0


def test_tables_are_read_only_in_native_verification(worker, tmp_path):
    # Bypass source validation deliberately to exercise the worker boundary.
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    # MOV byte [rdx], 0; RET
    (candidate / "code.bin").write_bytes(bytes.fromhex("c60200c3"))
    (candidate / "tables.bin").write_bytes(bytes(256))
    ref = reference_kernel("sbox_u8", "tiny")
    reference = tmp_path / "reference"
    assemble(ref["source"], reference)
    (reference / "tables.bin").write_bytes(ref["tables"])
    inp = bytes(range(256))
    (tmp_path / "fixtures.bin").write_bytes(
        struct.pack("<III", 1, 256, 256) + inp + TASKS["sbox_u8"].oracle(inp)
    )
    result = subprocess.run(
        [
            str(worker),
            str(candidate / "code.bin"),
            str(candidate / "tables.bin"),
            "0",
            str(reference / "code.bin"),
            str(reference / "tables.bin"),
            "0",
            str(tmp_path / "fixtures.bin"),
            "3",
            "1",
            str(hardware_info()["available_cpus"][0]),
            "42",
        ],
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode < 0
