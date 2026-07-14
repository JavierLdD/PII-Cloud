from __future__ import annotations

from dataclasses import dataclass
import resource
import sys


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
    self_usage = resource.getrusage(resource.RUSAGE_SELF)
    children_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ResourceUsageSnapshot(
        cpu_user_seconds=round(
            float(self_usage.ru_utime) + float(children_usage.ru_utime),
            6,
        ),
        cpu_system_seconds=round(
            float(self_usage.ru_stime) + float(children_usage.ru_stime),
            6,
        ),
        peak_memory_mb=round(
            max(
                _maxrss_to_mb(float(self_usage.ru_maxrss)),
                _maxrss_to_mb(float(children_usage.ru_maxrss)),
            ),
            6,
        ),
    )


def resource_usage_delta(started: ResourceUsageSnapshot) -> ResourceUsageDelta:
    completed = capture_resource_usage()
    user_seconds = max(0.0, completed.cpu_user_seconds - started.cpu_user_seconds)
    system_seconds = max(
        0.0,
        completed.cpu_system_seconds - started.cpu_system_seconds,
    )
    return ResourceUsageDelta(
        cpu_user_seconds=round(user_seconds, 6),
        cpu_system_seconds=round(system_seconds, 6),
        cpu_total_seconds=round(user_seconds + system_seconds, 6),
        peak_memory_mb=completed.peak_memory_mb,
    )


def _maxrss_to_mb(value: float) -> float:
    if sys.platform == "darwin":
        return value / (1024 * 1024)
    return value / 1024
