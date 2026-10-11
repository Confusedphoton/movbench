import pytest

from movbench.reference import reference_kernel
from movbench.runner import validate_submission
from movbench.submission import write_submission
from movbench.tasks import TASKS, TIERS
from movbench.validation import InvalidKernel, assemble, validate_bytes


@pytest.mark.parametrize("task", list(TASKS))
@pytest.mark.parametrize("tier", list(TIERS))
def test_all_references_fit_and_decode(task, tier, tmp_path):
    kernel = reference_kernel(task, tier)
    assert len(kernel["tables"]) + kernel["scratch_bytes"] <= TIERS[tier]
    assert validate_bytes(assemble(kernel["source"], tmp_path))["instructions"] > 0


@pytest.mark.parametrize(
    "source",
    [
        "add eax, 1",
        "lea rax, [rdi]",
        "xor eax, eax",
        "cmp eax, 0",
        "jmp 0",
        "call rax",
        "ret",
        "nop",
        "vpxord zmm0, zmm0, zmm0",
        "vpmultishiftqb zmm0, zmm1, zmm2",
        ".byte 0x90",
        '.include "secret"',
        "mov eax, 1; add eax, 1",
        "label: mov eax, 1",
        "mov rdi, rax",
        "mov rsp, rax",
        "mov rbx, rax",
        "mov BYTE PTR [rdx], al",
        "mov BYTE PTR [rdi], al",
        "mov al, BYTE PTR [rax]",
        "mov al, BYTE PTR [rip]",
        "mov al, BYTE PTR [rdi - 1]",
        "mov al, BYTE PTR fs:[rdi]",
    ],
)
def test_forbidden_instructions_and_operands(source, tmp_path):
    with pytest.raises(InvalidKernel):
        assemble(source, tmp_path)


@pytest.mark.parametrize("code", [b"\x90\xc3", b"\xc3\xc3", b"\x0f\xc3", b"\x89\xc0"])
def test_actual_machine_bytes_are_enforced(code):
    with pytest.raises(InvalidKernel):
        validate_bytes(code)


@pytest.mark.parametrize(
    "source",
    [
        "movzx eax, BYTE PTR [rdi]\nmov BYTE PTR [rsi], al",
        "movdqu xmm0, XMMWORD PTR [rdi]\nmovdqu XMMWORD PTR [rsi], xmm0",
        "vmovdqu8 zmm0, ZMMWORD PTR [rdi]\nvpshufb zmm1, zmm0, zmm0",
        "kmovw k1, eax\nvmovdqu8 zmm0{k1}{z}, ZMMWORD PTR [rdi]",
        "vpgatherdd zmm1{k1}, [rdx + zmm0*4]",
    ],
)
def test_legal_scalar_simd_masks_and_gathers(source, tmp_path):
    assert validate_bytes(assemble(source, tmp_path))["instructions"] > 0


def test_over_budget_has_no_validity_points(tmp_path):
    root = tmp_path / "submission"
    kernel = reference_kernel("transpose_8x8", "tiny")
    kernel["tables"] = bytes(4096)
    kernel["scratch_bytes"] = 1
    write_submission(root, "over-budget", [kernel])
    result = validate_submission(root)
    assert not result[0]["valid"]
    assert "4097" in result[0]["error"]


def test_paths_cannot_escape_submission(tmp_path):
    from movbench.submission import local_path

    with pytest.raises(ValueError):
        local_path(tmp_path, "../outside.bin")
