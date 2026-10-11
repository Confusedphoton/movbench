import json
import re
import subprocess
from pathlib import Path

from capstone import CS_AC_WRITE, CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_OP_MEM, X86_OP_REG

POLICY = json.loads(Path(__file__).with_name("policy.json").read_text())
ALLOWED = frozenset(x for group in POLICY["groups"].values() for x in group)
BASE_REGS = {"rdi", "rsi", "rdx", "rcx"}
GP_WRITABLE = {"rax", "eax", "ax", "al", "ah"}
for reg in range(8, 12):
    GP_WRITABLE.update({f"r{reg}", f"r{reg}d", f"r{reg}w", f"r{reg}b"})
WRITABLE = GP_WRITABLE | {f"{kind}{n}" for kind in ("xmm", "ymm", "zmm") for n in range(32)}
WRITABLE |= {f"k{n}" for n in range(8)}


class InvalidKernel(ValueError):
    pass


def assemble(source: str, build_dir: Path) -> bytes:
    if len(source.encode()) > POLICY["max_source_bytes"]:
        raise InvalidKernel("Source exceeds the source size limit")
    body = []
    for number, line in enumerate(source.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_ ,+*\[\]{}\-]+", line):
            raise InvalidKernel(f"Line {number}: directives, labels and expressions are forbidden")
        mnemonic = line.split()[0].lower()
        if mnemonic not in ALLOWED:
            raise InvalidKernel(f"Line {number}: {mnemonic} is not allowed")
        body.append(line)
    if not body:
        raise InvalidKernel("Empty kernel")
    build_dir.mkdir(parents=True, exist_ok=True)
    source_path = build_dir / "wrapped.s"
    source_path.write_text(
        ".intel_syntax noprefix\n.text\n.global kernel\nkernel:\n"
        + "\n".join(body)
        + '\nret\n.section .note.GNU-stack,"",@progbits\n'
    )
    try:
        subprocess.run(
            ["gcc", "-c", str(source_path), "-o", str(build_dir / "kernel.o")],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # No symbol operands are accepted, so text has no relocations.
        subprocess.run(
            [
                "objcopy",
                "-O",
                "binary",
                "-j",
                ".text",
                str(build_dir / "kernel.o"),
                str(build_dir / "code.bin"),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        raise InvalidKernel(f"Assembly failed: {err[:2000]}") from exc
    code = (build_dir / "code.bin").read_bytes()
    validate_bytes(code)
    return code


def validate_bytes(code: bytes):
    if not code or len(code) > POLICY["max_code_bytes"]:
        raise InvalidKernel("Invalid code size")
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    instructions = list(decoder.disasm(code, 0))
    if sum(i.size for i in instructions) != len(code):
        raise InvalidKernel("Undecodable bytes in kernel")
    if instructions[-1].mnemonic != "ret" or instructions[-1].bytes != b"\xc3":
        raise InvalidKernel("Missing harness return")
    counts = {}
    for ins in instructions[:-1]:
        if ins.mnemonic not in ALLOWED:
            raise InvalidKernel(f"0x{ins.address:x}: forbidden instruction {ins.mnemonic}")
        if any(p in (0xF0, 0xF2, 0xF3) for p in ins.prefix):
            # F2/F3 mandatory SIMD opcode prefixes are decoded separately by Capstone.
            raise InvalidKernel("LOCK and repeat prefixes are forbidden")
        _, writes = ins.regs_access()
        for reg in writes:
            name = ins.reg_name(reg)
            if name not in WRITABLE:
                raise InvalidKernel(f"{ins.mnemonic} writes reserved register {name}")
        for op in ins.operands:
            if op.type == X86_OP_REG and op.access & CS_AC_WRITE:
                name = ins.reg_name(op.reg)
                if name not in WRITABLE:
                    raise InvalidKernel(f"Writes reserved register {name}")
            if op.type != X86_OP_MEM:
                continue
            mem = op.mem
            base = ins.reg_name(mem.base)
            index = ins.reg_name(mem.index) if mem.index else None
            if mem.segment or base not in BASE_REGS:
                raise InvalidKernel("Memory must use rdi/rsi/rdx/rcx, with no segment override")
            if (
                index
                and index not in {"rax", "r8", "r9", "r10", "r11"}
                and not re.fullmatch(r"[xyz]mm(?:[0-9]|[12][0-9]|3[01])", index)
            ):
                raise InvalidKernel(f"Forbidden memory index {index}")
            if (op.access & CS_AC_WRITE or "scatter" in ins.mnemonic) and base in {"rdi", "rdx"}:
                raise InvalidKernel("Inputs and tables are read-only")
            if mem.disp < 0:
                raise InvalidKernel("Negative memory displacements are forbidden")
        counts[ins.mnemonic] = counts.get(ins.mnemonic, 0) + 1
    return {
        "instructions": len(instructions) - 1,
        "code_bytes": len(code),
        "mnemonics": counts,
        "policy": POLICY["id"],
    }
