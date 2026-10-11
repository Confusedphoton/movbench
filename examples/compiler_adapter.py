"""Example compiler contract. Replace reference_kernel with your LLM/compiler."""

import json
import sys
from pathlib import Path

from movbench.reference import reference_kernel

spec = json.loads(Path(sys.argv[1]).read_text())
output = Path(sys.argv[2])
kernel = reference_kernel(spec["id"], spec["tier"], optimized=True)
(output / "kernel.s").write_text(kernel["source"])
(output / "tables.bin").write_bytes(kernel["tables"])
(output / "kernel.json").write_text(json.dumps({"scratch_bytes": kernel["scratch_bytes"]}))
