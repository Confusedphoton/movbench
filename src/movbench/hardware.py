import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path


def hardware_info(target="zen5"):
    cpuinfo = Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else ""
    first = cpuinfo.split("\n\n")[0]
    fields = dict(line.split(":", 1) for line in first.splitlines() if ":" in line)
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    family, model = int(fields.get("cpu family", 0)), int(fields.get("model", 0))
    vendor = fields.get("vendor_id", "unknown")
    # Conservative AMD family/model classification; unknown CPUs stay unverified.
    is_zen5 = (
        vendor == "AuthenticAMD"
        and family == 0x1A
        and model in {*range(0x30), *range(0x40, 0x50), *range(0x60, 0x80)}
    )
    is_zen4 = (
        vendor == "AuthenticAMD"
        and family == 0x19
        and model in {*range(0x10, 0x20), *range(0x60, 0x80), *range(0xA0, 0xB0)}
    )
    flags = fields.get("flags", "").split()
    required = {"avx2", "avx512f", "avx512bw", "avx512vl", "avx512vbmi", "avx512_vbmi2"}
    try:
        gcc = subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        gcc = "unavailable"
    return {
        "cpu": fields.get("model name", platform.processor()),
        "vendor": vendor,
        "family": family,
        "model": model,
        "microcode": fields.get("microcode", "unknown"),
        "architecture": "zen5" if is_zen5 else "zen4" if is_zen4 else "unknown",
        "target": target,
        "target_verified": is_zen5 if target == "zen5" else is_zen4,
        "required_features_present": required <= set(flags),
        "missing_features": sorted(required - set(flags)),
        "flags": flags,
        "os": platform.platform(),
        "python": platform.python_version(),
        "gcc": gcc,
        "available_cpus": sorted(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else [],
    }


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]
