"""A deliberately simple table compiler, including a <4 KiB Boolean backend."""

from functools import lru_cache

from .tasks import SBOX, TASKS, TIERS


@lru_cache(maxsize=4)
def operation_table(operation):
    if operation == "madd":
        return bytes(((i & 255) * ((i >> 8) & 255) + (i >> 16)) & 255 for i in range(1 << 24))
    fn = {"add": lambda a, b: a + b, "mul": lambda a, b: a * b, "xor": lambda a, b: a ^ b}[
        operation
    ]
    return bytes(fn(a, b) & 255 for b in range(256) for a in range(256))


class Circuit:
    def __init__(self):
        self.lines = []
        tables = bytearray()
        self.bits = []
        for bit in range(8):
            self.bits.append(len(tables))
            tables += bytes((x >> bit) & 1 for x in range(256))
        self.gates = {}
        for name, fn in [
            ("xor", lambda a, b: a ^ b),
            ("and", lambda a, b: a & b),
            ("or", lambda a, b: a | b),
        ]:
            self.gates[name] = len(tables)
            gate = bytearray(258)
            for a in range(2):
                for b in range(2):
                    gate[a + 256 * b] = fn(a, b)
            tables += gate
        self.merge = len(tables)
        tables += bytes(((i & 255) * 2 + (i >> 8)) & 255 for i in range(512))
        self.tables = bytes(tables)

    def emit(self, *lines):
        self.lines.extend(lines)

    def extract(self, input_offset, dest):
        self.emit(f"movzx eax, BYTE PTR [rdi + {input_offset}]")
        for bit, offset in enumerate(self.bits):
            self.emit(
                f"mov r8b, BYTE PTR [rdx + rax + {offset}]",
                f"mov BYTE PTR [rcx + {dest + bit}], r8b",
            )

    def gate(self, name, a, b, dest):
        self.emit(
            f"mov al, BYTE PTR [rcx + {a}]",
            f"mov ah, BYTE PTR [rcx + {b}]",
            "movzx eax, ax",
            f"mov r8b, BYTE PTR [rdx + rax + {self.gates[name]}]",
            f"mov BYTE PTR [rcx + {dest}], r8b",
        )

    def add(self, a, b, dest):
        self.emit("mov BYTE PTR [rcx + 40], 0")
        for bit in range(8):
            self.gate("xor", a + bit, b + bit, 32)
            self.gate("and", a + bit, b + bit, 33)
            self.gate("and", 32, 40, 34)
            self.gate("xor", 32, 40, dest + bit)
            self.gate("or", 33, 34, 40)

    def pack(self, src, output_offset):
        self.emit("mov eax, 0")
        for bit in reversed(range(8)):
            self.emit(
                f"mov ah, BYTE PTR [rcx + {src + bit}]",
                "movzx eax, ax",
                f"mov al, BYTE PTR [rdx + rax + {self.merge}]",
                "movzx eax, al",
            )
        self.emit(f"mov BYTE PTR [rsi + {output_offset}], al")


def reference_kernel(task_id: str, tier: str, optimized: bool = False):
    task, budget = TASKS[task_id], TIERS[tier]
    lines, tables, scratch = [], b"", 0
    if task_id == "transpose_8x8" and optimized:
        tables = bytes(row * 8 + col for col in range(8) for row in range(8))
        lines = [
            "vmovdqu8 zmm0, ZMMWORD PTR [rdi]",
            "vmovdqu8 zmm1, ZMMWORD PTR [rdx]",
            "vpermb zmm2, zmm1, zmm0",
            "vmovdqu8 ZMMWORD PTR [rsi], zmm2",
        ]
    elif task_id == "route_64" and optimized:
        lines = [
            "vmovdqu8 zmm0, ZMMWORD PTR [rdi]",
            "vmovdqu8 zmm1, ZMMWORD PTR [rdi + 64]",
            "vpermb zmm2, zmm1, zmm0",
            "vmovdqu8 ZMMWORD PTR [rsi], zmm2",
        ]
    elif task_id == "transpose_8x8":
        for col in range(8):
            for row in range(8):
                lines += [
                    f"mov al, BYTE PTR [rdi + {row * 8 + col}]",
                    f"mov BYTE PTR [rsi + {col * 8 + row}], al",
                ]
    elif task_id in {"sbox_u8", "route_64"}:
        tables = SBOX if task_id == "sbox_u8" else bytes(i & 63 for i in range(256))
        for i in range(task.output_bytes):
            input_offset = i if task_id == "sbox_u8" else 64 + i
            lines += [
                f"movzx eax, BYTE PTR [rdi + {input_offset}]",
                "movzx eax, BYTE PTR [rdx + rax]",
            ]
            if task_id == "route_64":
                lines += ["mov al, BYTE PTR [rdi + rax]"]
            lines += [f"mov BYTE PTR [rsi + {i}], al"]
    elif task_id == "madd_u8" and budget >= 131072:
        if budget >= 16777216:
            tables = operation_table("madd")
            for i in range(32):
                lines += [
                    f"mov eax, DWORD PTR [rdi + {4 * i}]",
                    "mov al, BYTE PTR [rdx + rax]",
                    f"mov BYTE PTR [rsi + {i}], al",
                ]
        else:
            tables = operation_table("mul") + operation_table("add")
            for i in range(32):
                lines += [
                    f"movzx eax, WORD PTR [rdi + {4 * i}]",
                    "mov al, BYTE PTR [rdx + rax]",
                    f"mov ah, BYTE PTR [rdi + {4 * i + 2}]",
                    "movzx eax, ax",
                    "mov al, BYTE PTR [rdx + rax + 65536]",
                    f"mov BYTE PTR [rsi + {i}], al",
                ]
    elif budget >= 65536 and task_id != "madd_u8":
        tables = operation_table({"mul_u8": "mul", "xor_u8": "xor"}.get(task_id, "add"))
        if task_id in {"sum_u8", "prefix_u8"}:
            lines = ["mov eax, 0"]
            for i in range(task.input_bytes):
                lines += [
                    "mov ah, al",
                    f"mov al, BYTE PTR [rdi + {i}]",
                    "movzx eax, ax",
                    "mov al, BYTE PTR [rdx + rax]",
                ]
                if task_id == "prefix_u8":
                    lines += [f"mov BYTE PTR [rsi + {i}], al"]
            if task_id == "sum_u8":
                lines += ["mov BYTE PTR [rsi], al"]
        else:
            for i in range(task.output_bytes):
                lines += [
                    f"mov al, BYTE PTR [rdi + {i}]",
                    f"mov ah, BYTE PTR [rdi + {32 + i}]",
                    "movzx eax, ax",
                    "mov al, BYTE PTR [rdx + rax]",
                    f"mov BYTE PTR [rsi + {i}], al",
                ]
    else:
        c = Circuit()
        if task_id in {"sum_u8", "prefix_u8"}:
            for bit in range(8):
                c.emit(f"mov BYTE PTR [rcx + {16 + bit}], 0")
            for i in range(task.input_bytes):
                c.extract(i, 0)
                c.add(16, 0, 16)
                if task_id == "prefix_u8":
                    c.pack(16, i)
            if task_id == "sum_u8":
                c.pack(16, 0)
        else:
            for i in range(task.output_bytes):
                c.extract(4 * i if task_id == "madd_u8" else i, 0)
                c.extract(4 * i + 1 if task_id == "madd_u8" else 32 + i, 8)
                if task_id == "add_u8":
                    c.add(0, 8, 16)
                elif task_id == "xor_u8":
                    for bit in range(8):
                        c.gate("xor", bit, 8 + bit, 16 + bit)
                else:
                    for bit in range(8):
                        c.emit(f"mov BYTE PTR [rcx + {16 + bit}], 0")
                    for shift in range(8):
                        for bit in range(8):
                            if bit < shift:
                                c.emit(f"mov BYTE PTR [rcx + {24 + bit}], 0")
                            else:
                                c.gate("and", bit - shift, 8 + shift, 24 + bit)
                        c.add(16, 24, 16)
                    if task_id == "madd_u8":
                        c.extract(4 * i + 2, 0)
                        c.add(16, 0, 16)
                c.pack(16, i)
        lines, tables, scratch = c.lines, c.tables, 48
    assert len(tables) + scratch <= budget
    return {
        "task": task_id,
        "tier": tier,
        "source": "\n".join(lines) + "\n",
        "tables": tables,
        "scratch_bytes": scratch,
    }
