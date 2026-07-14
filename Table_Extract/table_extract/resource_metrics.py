from __future__ import annotations

from dataclasses import dataclass
import os
import resource


@dataclass(frozen=True)
class ResourceUsageSnapshot:
    cpu_user_seconds: float
    cpu_system_seconds: float
    peak_memory_mb: float


@dataclass(frozen=True)
class ResourceUsageDelta:
    cpu_user_seconds: float
    cpu_system_seconds: float
    cpu_total_seconds: float
    peak_memory_mb: float


def capture_resource_usage() -> ResourceUsageSnapshot:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return ResourceUsageSnapshot(
        cpu_user_seconds=float(usage.ru_utime),
        cpu_system_seconds=float(usage.ru_stime),
        peak_memory_mb=round(_ru_maxrss_to_mb(float(usage.ru_maxrss)), 6),
    )


def resource_usage_delta(started: ResourceUsageSnapshot) -> ResourceUsageDelta:
    completed = capture_resource_usage()
    user_seconds = max(0.0, completed.cpu_user_seconds - started.cpu_user_seconds)
    system_seconds = max(0.0, completed.cpu_system_seconds - started.cpu_system_seconds)
    return ResourceUsageDelta(
        cpu_user_seconds=round(user_seconds, 6),
        cpu_system_seconds=round(system_seconds, 6),
        cpu_total_seconds=round(user_seconds + system_seconds, 6),
        peak_memory_mb=completed.peak_memory_mb,
    )


def _ru_maxrss_to_mb(value: float) -> float:
    # Linux reports kilobytes; macOS reports bytes.
    if os.uname().sysname == "Darwin":
        return value / (1024 * 1024)
    return value / 1024
