"""Small, dependency-free helpers shared by the scale benchmark runners."""

from __future__ import annotations

import hashlib
import math
import os
import platform
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]


def resolve_path(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixture_metadata(path: Path, dataset: dict[str, Any]) -> dict[str, Any]:
    table_rows = {
        str(table).upper(): len(rows) if isinstance(rows, list) else None
        for table, rows in dataset.items()
    }
    known_counts = [count for count in table_rows.values() if count is not None]
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "table_rows": table_rows,
        "total_source_rows": sum(known_counts),
        "schema_version": 1,
    }


def runtime_metadata() -> dict[str, Any]:
    uname = platform.uname()
    cpu = platform.processor()
    if not cpu:
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("model name") and ":" in line:
                    cpu = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    available_memory = None
    try:
        available_memory = (
            os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        )
    except (AttributeError, OSError, ValueError):
        pass
    return {
        "os": f"{uname.system} {uname.release}",
        "machine": uname.machine,
        "cpu": cpu or "unknown",
        "logical_cpus": os.cpu_count(),
        "available_memory_bytes": available_memory,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_flags": {
            name: getattr(sys.flags, name)
            for name in (
                "debug", "inspect", "interactive", "isolated", "optimize",
                "dont_write_bytecode", "no_user_site", "no_site", "utf8_mode",
            )
            if hasattr(sys.flags, name)
        },
    }


def finite_duration(value: float, label: str) -> float:
    if not math.isfinite(value) or value < 0:
        raise RuntimeError(f"invalid {label}: {value!r}")
    return value


def stats(samples: Iterable[float]) -> dict[str, Any]:
    values = [finite_duration(float(value), "sample") for value in samples]
    if not values:
        raise RuntimeError("cannot summarize an empty sample set")
    ordered = sorted(values)
    count = len(values)
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if count >= 2 else 0.0
    p95 = None
    if count >= 20:
        rank = max(1, math.ceil(0.95 * count))
        p95 = ordered[rank - 1]
    return {
        "count": count,
        "mean_ms": mean,
        "median_ms": statistics.median(values),
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
        "stdev_ms": stdev,
        "cv": stdev / mean if mean else 0.0,
        "p95_ms": p95,
        "samples_ms": values,
    }
