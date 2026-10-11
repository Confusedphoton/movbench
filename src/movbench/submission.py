import hashlib
import json
from pathlib import Path

from .tasks import TASKS, TIERS


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_submission(root: Path, model: str, kernels: list[dict], metadata=None):
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "model": model, "metadata": metadata or {}, "kernels": []}
    for kernel in kernels:
        folder = root / kernel["tier"] / kernel["task"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "kernel.s").write_text(kernel["source"])
        (folder / "tables.bin").write_bytes(kernel["tables"])
        manifest["kernels"].append(
            {
                "task": kernel["task"],
                "tier": kernel["tier"],
                "source": str((folder / "kernel.s").relative_to(root)),
                "tables": str((folder / "tables.bin").relative_to(root)),
                "scratch_bytes": kernel["scratch_bytes"],
            }
        )
    (root / "submission.json").write_text(json.dumps(manifest, indent=2) + "\n")


def local_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Submission paths must stay inside the submission directory")
    return path


def load_submission(root: Path):
    manifest = json.loads((root / "submission.json").read_text())
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or not isinstance(manifest.get("model"), str)
    ):
        raise ValueError("Expected schema_version 1 and a model name")
    if not manifest["model"].strip() or len(manifest["model"]) > 200:
        raise ValueError("Model names must contain 1–200 characters")
    seen = set()
    if not isinstance(manifest.get("kernels"), list) or not isinstance(
        manifest.get("metadata", {}), dict
    ):
        raise TypeError("kernels must be a list and metadata must be an object")
    for item in manifest["kernels"]:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(name), str) for name in ("task", "tier", "source", "tables")
        ):
            raise ValueError("Each kernel requires string task, tier, source, and tables fields")
        key = (item["task"], item["tier"])
        if key in seen or key[0] not in TASKS or key[1] not in TIERS:
            raise ValueError(f"Duplicate or unknown task/tier: {key}")
        seen.add(key)
        scratch = item.get("scratch_bytes", 0)
        if type(scratch) is not int or scratch < 0:
            raise ValueError("scratch_bytes must be a nonnegative integer")
        for name in ("source", "tables"):
            local_path(root, item[name])
    return manifest
