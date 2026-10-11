"""Versioned, exact integer kernels; no floating-point tolerances."""

import random
from dataclasses import asdict, dataclass

TIERS = {"tiny": 4096, "small": 65536, "medium": 1048576, "large": 16777216}
SUITE_VERSION = "move-kernels-v1"


@dataclass(frozen=True)
class Task:
    id: str
    name: str
    category: str
    input_bytes: int
    output_bytes: int
    description: str

    def spec(self):
        return {**asdict(self), "suite_version": SUITE_VERSION, "dtype": "uint8"}

    def oracle(self, data: bytes) -> bytes:
        if len(data) != self.input_bytes:
            raise ValueError("Incorrect input size")
        if self.id in {"add_u8", "mul_u8", "xor_u8"}:
            a, b = data[:32], data[32:]
            if self.id == "add_u8":
                return bytes((x + y) & 255 for x, y in zip(a, b))
            if self.id == "mul_u8":
                return bytes((x * y) & 255 for x, y in zip(a, b))
            return bytes(x ^ y for x, y in zip(a, b))
        if self.id == "sbox_u8":
            return bytes(SBOX[x] for x in data)
        if self.id == "madd_u8":
            if any(data[i] for i in range(3, 128, 4)):
                raise ValueError("madd_u8 record padding must be zero")
            return bytes((data[i] * data[i + 1] + data[i + 2]) & 255 for i in range(0, 128, 4))
        if self.id in {"sum_u8", "prefix_u8"}:
            total, result = 0, []
            for x in data:
                total = (total + x) & 255
                result.append(total)
            return bytes([total]) if self.id == "sum_u8" else bytes(result)
        if self.id == "transpose_8x8":
            return bytes(data[row * 8 + col] for col in range(8) for row in range(8))
        if self.id == "route_64":
            return bytes(data[i & 63] for i in data[64:])
        raise ValueError(self.id)

    def cases(self, seed: int, count: int = 192):
        rng = random.Random(seed)
        n = self.input_bytes
        cases = [
            bytes(n),
            bytes([255]) * n,
            bytes([1]) * n,
            bytes(i & 255 for i in range(n)),
            bytes([85, 170]) * (n // 2),
        ]
        for bit in range(8):
            cases += [bytes([1 << bit]) * n, bytes([255 ^ (1 << bit)]) * n]
        if self.id in {"add_u8", "mul_u8", "xor_u8"}:
            # Exhaustively cover every operand pair, in 32-lane batches.
            for a in range(256):
                for start in range(0, 256, 32):
                    cases.append(bytes([a]) * 32 + bytes(range(start, start + 32)))
        if self.id == "sbox_u8":
            cases.append(bytes(range(256)))
        if self.id == "route_64":
            cases += [bytes(range(64)) + bytes([i]) * 64 for i in range(256)]
        cases += [rng.randbytes(n) for _ in range(count)]
        if self.id == "madd_u8":
            corners = [0, 1, 2, 15, 16, 127, 128, 254, 255]
            triples = [(a, b, c) for a in corners for b in corners for c in corners]
            for start in range(0, len(triples), 32):
                batch = (triples[start : start + 32] + [(0, 0, 0)] * 32)[:32]
                cases.append(bytes(value for a, b, c in batch for value in (a, b, c, 0)))
            padded = []
            for data in cases:
                mutable = bytearray(data)
                mutable[3::4] = bytes(32)
                padded.append(bytes(mutable))
            cases = padded
        rng.shuffle(cases)
        return [(x, self.oracle(x)) for x in cases]


TASKS = {
    t.id: t
    for t in [
        Task(
            "add_u8",
            "Byte addition",
            "Arithmetic",
            64,
            32,
            "out[i] = (in[i] + in[32+i]) mod 256, 32 independent lanes.",
        ),
        Task(
            "mul_u8",
            "Byte multiplication",
            "Arithmetic",
            64,
            32,
            "out[i] = (in[i] * in[32+i]) mod 256, 32 independent lanes.",
        ),
        Task(
            "xor_u8",
            "Byte XOR",
            "Logic",
            64,
            32,
            "out[i] = in[i] XOR in[32+i], 32 independent lanes.",
        ),
        Task(
            "madd_u8",
            "Fused byte multiply-add",
            "Arithmetic",
            128,
            32,
            "32 interleaved four-byte records (a,b,c,0): out[i] = (a*b+c) mod 256. Padding is always zero.",
        ),
        Task(
            "sbox_u8",
            "AES substitution",
            "Lookup",
            256,
            256,
            "Apply the fixed AES forward S-box independently to 256 bytes.",
        ),
        Task("sum_u8", "Modular reduction", "Reduction", 64, 1, "out[0] = sum(in[0:64]) mod 256."),
        Task(
            "prefix_u8",
            "Inclusive prefix sum",
            "Scan",
            64,
            64,
            "out[i] = sum(in[0:i+1]) mod 256, 64 bytes.",
        ),
        Task(
            "transpose_8x8",
            "8 × 8 transpose",
            "Routing",
            64,
            64,
            "out[8*col+row] = in[8*row+col], row-major byte matrix.",
        ),
        Task(
            "route_64",
            "Indexed byte routing",
            "Routing",
            128,
            64,
            "out[i] = in[in[64+i] mod 64], arbitrary byte selectors.",
        ),
    ]
}

SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)
