from __future__ import annotations

import getpass
import logging
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from app.models.observation import (
    ObservationCgroupSnapshot,
    ObservationDiskSnapshot,
    ObservationEvent,
    ObservationProcessSnapshot,
)


logger = logging.getLogger(__name__)


class ObservationSample(TypedDict):
    captured_at: datetime
    cgroup: ObservationCgroupSnapshot
    disk: list[ObservationDiskSnapshot]
    top_processes: list[ObservationProcessSnapshot]
    events: list[ObservationEvent]


class ObservationSampler:
    def __init__(
        self,
        current_user: str | None = None,
        disk_paths: list[str] | None = None,
    ) -> None:
        self._current_user = current_user or getpass.getuser() or os.environ.get('USER') or 'unknown'
        self._disk_paths = disk_paths
        self._cgroup_path: Path | None = None
        self._prev_cpu_usage_usec: int | None = None
        self._prev_cpu_sample_monotonic: float | None = None
        self._prev_memory_events: tuple[int | None, int | None] | None = None

    @property
    def current_user(self) -> str:
        return self._current_user

    def initialize(self) -> None:
        self._cgroup_path = self._detect_cgroup_path()

    def sample(
        self,
        include_processes: bool,
        include_disk: bool,
        process_limit: int | None = None,
        include_fd_counts: bool = False,
    ) -> ObservationSample:
        captured_at = datetime.now(UTC)
        events: list[ObservationEvent] = []

        try:
            cgroup, cgroup_events = self._read_cgroup_snapshot()
            events.extend(cgroup_events)
        except Exception as exc:
            logger.warning('Failed to sample cgroup metrics: %s', exc)
            cgroup = ObservationCgroupSnapshot()
            events.append(
                ObservationEvent(
                    ts=datetime.now(UTC),
                    type='sample_error',
                    message='Failed to sample cgroup metrics',
                    data={'section': 'cgroup', 'error': str(exc)},
                )
            )

        if include_disk:
            try:
                disk = self._read_disk_snapshots()
            except Exception as exc:
                logger.warning('Failed to sample disk metrics: %s', exc)
                disk = []
                events.append(
                    ObservationEvent(
                        ts=datetime.now(UTC),
                        type='sample_error',
                        message='Failed to sample disk metrics',
                        data={'section': 'disk', 'error': str(exc)},
                    )
                )
        else:
            disk = []

        if include_processes:
            try:
                top_processes = self._read_process_snapshots(
                    limit=process_limit,
                    include_fd_counts=include_fd_counts,
                )
            except Exception as exc:
                logger.warning('Failed to sample process metrics: %s', exc)
                top_processes = []
                events.append(
                    ObservationEvent(
                        ts=datetime.now(UTC),
                        type='sample_error',
                        message='Failed to sample process metrics',
                        data={'section': 'process', 'error': str(exc)},
                    )
                )
        else:
            top_processes = []

        return {
            'captured_at': captured_at,
            'cgroup': cgroup,
            'disk': disk,
            'top_processes': top_processes,
            'events': events,
        }

    def _read_cgroup_snapshot(
        self,
    ) -> tuple[ObservationCgroupSnapshot, list[ObservationEvent]]:
        if self._cgroup_path is None:
            return ObservationCgroupSnapshot(), []

        cpu_stat = self._read_key_value_file(self._cgroup_path / 'cpu.stat')
        memory_events = self._read_key_value_file(self._cgroup_path / 'memory.events')
        memory_stat = self._read_key_value_file(self._cgroup_path / 'memory.stat')
        mem_current = self._read_int_file(self._cgroup_path / 'memory.current') or 0
        mem_max = self._read_int_or_max_file(self._cgroup_path / 'memory.max')

        now_monotonic = time.monotonic()
        cpu_usage_usec = int(cpu_stat.get('usage_usec', 0))
        cpu_usage_pct: float | None = None
        if (
            self._prev_cpu_usage_usec is not None
            and self._prev_cpu_sample_monotonic is not None
        ):
            delta_usage_usec = cpu_usage_usec - self._prev_cpu_usage_usec
            delta_seconds = now_monotonic - self._prev_cpu_sample_monotonic
            if delta_seconds > 0:
                cpu_usage_pct = max(
                    0.0, delta_usage_usec / (delta_seconds * 1_000_000) * 100
                )

        self._prev_cpu_usage_usec = cpu_usage_usec
        self._prev_cpu_sample_monotonic = now_monotonic

        mem_usage_pct: float | None = None
        if mem_max and mem_max > 0:
            mem_usage_pct = mem_current / mem_max * 100

        oom = memory_events.get('oom')
        oom_kill = memory_events.get('oom_kill')

        new_events: list[ObservationEvent] = []
        prev_oom, prev_oom_kill = self._prev_memory_events or (None, None)
        if prev_oom is not None and oom is not None and oom > prev_oom:
            new_events.append(
                ObservationEvent(
                    ts=datetime.now(UTC),
                    type='oom',
                    message='cgroup oom counter increased',
                    data={'oom': oom, 'previous_oom': prev_oom},
                )
            )
        if prev_oom_kill is not None and oom_kill is not None and oom_kill > prev_oom_kill:
            new_events.append(
                ObservationEvent(
                    ts=datetime.now(UTC),
                    type='oom_kill',
                    message='cgroup oom_kill counter increased',
                    data={
                        'oom_kill': oom_kill,
                        'previous_oom_kill': prev_oom_kill,
                        'mem_current_bytes': mem_current,
                        'mem_max_bytes': mem_max,
                    },
                )
            )
        self._prev_memory_events = (oom, oom_kill)

        return (
            ObservationCgroupSnapshot(
                cpu_usage_pct=cpu_usage_pct,
                cpu_usage_usec=cpu_usage_usec,
                cpu_nr_periods=cpu_stat.get('nr_periods'),
                cpu_nr_throttled=cpu_stat.get('nr_throttled'),
                cpu_throttled_usec=cpu_stat.get('throttled_usec'),
                mem_current_bytes=mem_current,
                mem_max_bytes=mem_max,
                mem_usage_pct=mem_usage_pct,
                oom=oom,
                oom_kill=oom_kill,
                memory_anon_bytes=memory_stat.get('anon'),
                memory_file_bytes=memory_stat.get('file'),
                memory_shmem_bytes=memory_stat.get('shmem'),
                memory_slab_bytes=memory_stat.get('slab'),
            ),
            new_events,
        )

    def _read_disk_snapshots(self) -> list[ObservationDiskSnapshot]:
        result: list[ObservationDiskSnapshot] = []
        for raw_path in self._resolve_disk_paths():
            path = Path(raw_path)
            if not path.exists():
                continue
            usage = shutil.disk_usage(path)
            stat = os.statvfs(path)
            inode_total = stat.f_files or None
            inode_used = (stat.f_files - stat.f_ffree) if stat.f_files else None
            inode_usage_pct: float | None = None
            if inode_total and inode_used is not None and inode_total > 0:
                inode_usage_pct = inode_used / inode_total * 100
            result.append(
                ObservationDiskSnapshot(
                    path=str(path),
                    used_bytes=usage.used,
                    total_bytes=usage.total,
                    free_bytes=usage.free,
                    usage_pct=(usage.used / usage.total * 100) if usage.total else 0.0,
                    inode_used=inode_used,
                    inode_total=inode_total,
                    inode_usage_pct=inode_usage_pct,
                )
            )
        return result

    def _read_process_snapshots(
        self,
        limit: int | None = None,
        include_fd_counts: bool = False,
    ) -> list[ObservationProcessSnapshot]:
        ps_output = self._run_ps()
        rows: list[ObservationProcessSnapshot] = []
        for line in ps_output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parsed = self._parse_ps_row(stripped)
            if parsed is None:
                continue
            pid, ppid, user, comm, cpu_pct, rss_kib, state, uptime_sec, threads = parsed

            rows.append(
                ObservationProcessSnapshot(
                    pid=pid,
                    user=user,
                    comm=comm,
                    rss_bytes=rss_kib * 1024,
                    rss_mib=(rss_kib * 1024) / (1024 * 1024),
                    cpu_pct=cpu_pct,
                    threads=threads,
                    fds=None,
                    state=state[:1] if state else None,
                    uptime_sec=uptime_sec,
                    ppid=ppid,
                )
            )

        rows.sort(key=lambda row: row.rss_bytes, reverse=True)
        if limit is not None:
            rows = rows[:limit]
        if include_fd_counts:
            for row in rows:
                row.fds = self._count_fds(row.pid)
        return rows

    def _run_ps(self) -> str:
        if os.name == 'posix' and Path('/proc').exists():
            cmd = [
                'ps',
                '-u',
                self._current_user,
                '-o',
                'pid=,ppid=,user=,comm=,%cpu=,rss=,stat=,etimes=,nlwp=',
            ]
        else:
            cmd = [
                'ps',
                '-u',
                self._current_user,
                '-o',
                'pid=,ppid=,user=,comm=,%cpu=,rss=,state=',
            ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or 'ps command failed')
        return result.stdout

    def _parse_ps_row(
        self, row: str
    ) -> tuple[int, int, str, str, float, int, str | None, float | None, int | None] | None:
        linux_parts = row.split(None, 8)
        if len(linux_parts) == 9:
            pid_raw, ppid_raw, user, comm, cpu_raw, rss_raw, stat, etimes_raw, threads_raw = linux_parts
            try:
                return (
                    int(pid_raw),
                    int(ppid_raw),
                    user,
                    comm,
                    float(cpu_raw),
                    int(rss_raw),
                    stat,
                    float(etimes_raw),
                    int(threads_raw),
                )
            except ValueError:
                return None

        portable_parts = row.split(None, 6)
        if len(portable_parts) == 7:
            pid_raw, ppid_raw, user, comm, cpu_raw, rss_raw, state = portable_parts
            try:
                return (
                    int(pid_raw),
                    int(ppid_raw),
                    user,
                    comm,
                    float(cpu_raw),
                    int(rss_raw),
                    state,
                    None,
                    None,
                )
            except ValueError:
                return None
        return None

    def _count_fds(self, pid: int) -> int | None:
        fd_dir = Path('/proc') / str(pid) / 'fd'
        try:
            return len(list(fd_dir.iterdir()))
        except OSError:
            return None

    def _resolve_disk_paths(self) -> list[str]:
        if self._disk_paths is not None:
            return self._disk_paths

        candidates = [
            f'/home/{self._current_user}',
            os.environ.get('WORKSPACE', '/workspace'),
            '/tmp',
        ]
        deduped: list[str] = []
        seen: set[str] = set()
        for path in candidates:
            if path and path not in seen:
                seen.add(path)
                deduped.append(path)
        self._disk_paths = deduped
        return deduped

    def _detect_cgroup_path(self) -> Path:
        cgroup_file = Path('/proc/self/cgroup')
        if not cgroup_file.exists():
            return Path('/sys/fs/cgroup')

        for line in cgroup_file.read_text().splitlines():
            parts = line.split(':', 2)
            if len(parts) != 3:
                continue
            _, controllers, relative = parts
            if controllers == '':
                return Path('/sys/fs/cgroup') / relative.lstrip('/')
        return Path('/sys/fs/cgroup')

    def _read_key_value_file(self, path: Path) -> dict[str, int]:
        values: dict[str, int] = {}
        if not path.exists():
            return values
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            key, _, value = stripped.partition(' ')
            if not key or not value:
                continue
            try:
                values[key] = int(value)
            except ValueError:
                continue
        return values

    def _read_int_file(self, path: Path) -> int | None:
        if not path.exists():
            return None
        try:
            return int(path.read_text().strip())
        except ValueError:
            return None

    def _read_int_or_max_file(self, path: Path) -> int | None:
        if not path.exists():
            return None
        raw = path.read_text().strip()
        if raw == 'max':
            return None
        try:
            return int(raw)
        except ValueError:
            return None
