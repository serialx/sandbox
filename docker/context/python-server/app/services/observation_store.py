from __future__ import annotations

import json
import os
import socket
import shutil
import tarfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from tempfile import gettempdir
from typing import Any

from app.core.exceptions import ResourceNotFoundException
from app.models.observation import ObservationReportInfo
from app.services.observation_session import ObservationSession


class ObservationStore:
    def __init__(
        self,
        runtime_root: Path | None = None,
        state_root: Path | None = None,
    ) -> None:
        self.runtime_root = runtime_root
        self.state_root = state_root
        self.reports_dir: Path | None = None
        self.report_index_path: Path | None = None
        self.current_session_path: Path | None = None

    def initialize(self) -> None:
        xdg_runtime = os.environ.get('XDG_RUNTIME_DIR', '').strip()
        xdg_state = os.environ.get('XDG_STATE_HOME', '').strip()
        home_dir = os.environ.get('HOME', '').strip()

        self.runtime_root = self.runtime_root or self._choose_writable_dir(
            [
                Path(os.environ['AIO_OBSERVE_RUNTIME_DIR'])
                if 'AIO_OBSERVE_RUNTIME_DIR' in os.environ
                else None,
                Path('/run/aio-sandbox/obs'),
                Path(xdg_runtime) / 'aio-sandbox' / 'obs' if xdg_runtime else None,
                Path(gettempdir()) / 'aio-sandbox' / 'obs',
            ]
        )
        self.state_root = self.state_root or self._choose_writable_dir(
            [
                Path(os.environ['AIO_OBSERVE_STATE_DIR'])
                if 'AIO_OBSERVE_STATE_DIR' in os.environ
                else None,
                Path('/var/lib/aio-sandbox/obs'),
                Path(xdg_state) / 'aio-sandbox' / 'obs' if xdg_state else None,
                Path(home_dir) / '.local' / 'state' / 'aio-sandbox' / 'obs'
                if home_dir
                else None,
                Path(gettempdir()) / 'aio-sandbox' / 'obs-state',
            ]
        )
        self.reports_dir = self.state_root / 'reports'
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.report_index_path = self.state_root / 'report-index.json'
        self.current_session_path = self.runtime_root / 'current-session.json'

    def prepare_session_dir(self, session: ObservationSession) -> None:
        session.runtime_dir.mkdir(parents=True, exist_ok=True)

    def write_current_session(self, session: ObservationSession) -> None:
        if self.current_session_path is None:
            return
        self.current_session_path.parent.mkdir(parents=True, exist_ok=True)
        self.current_session_path.write_text(
            json.dumps(
                {
                    'session_id': session.session_id,
                    'mode': session.mode,
                    'started_at': session.started_at.isoformat(),
                    'ends_at': session.ends_at.isoformat() if session.ends_at else None,
                    'interval_seconds': session.interval_seconds,
                    'runtime_dir': str(session.runtime_dir),
                },
                indent=2,
            )
        )

    def clear_current_session(self) -> None:
        if self.current_session_path is None or not self.current_session_path.exists():
            return
        self.current_session_path.unlink()

    def delete_session_runtime(self, session: ObservationSession) -> None:
        if session.runtime_dir.exists():
            shutil.rmtree(session.runtime_dir, ignore_errors=True)

    def append_json_line(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(payload, ensure_ascii=True))
            fh.write('\n')

    def load_report_index(self) -> dict[str, ObservationReportInfo]:
        if self.report_index_path is None or not self.report_index_path.exists():
            return {}
        try:
            raw = json.loads(self.report_index_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        reports: dict[str, ObservationReportInfo] = {}
        for item in raw:
            info = ObservationReportInfo.model_validate(item)
            reports[info.report_id] = info
        return reports

    def persist_report_index(
        self, reports: dict[str, ObservationReportInfo]
    ) -> None:
        if self.report_index_path is None:
            return
        self.report_index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            info.model_dump(mode='json')
            for info in sorted(
                reports.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )
        ]
        self.report_index_path.write_text(json.dumps(payload, indent=2))

    def build_report(
        self,
        session: ObservationSession,
        report_id: str,
        reason: str,
        current_user: str,
    ) -> ObservationReportInfo:
        if self.reports_dir is None:
            raise RuntimeError('Observation store is not initialized')
        if not session.runtime_dir.exists():
            raise ResourceNotFoundException(
                f'Observation session data not found: {session.session_id}'
            )

        summary = self._build_summary(session, reason)
        environment = self._build_environment(session, current_user)
        summary_path = session.runtime_dir / 'summary.json'
        environment_path = session.runtime_dir / 'environment.json'
        summary_path.write_text(json.dumps(summary, indent=2))
        environment_path.write_text(json.dumps(environment, indent=2))

        report_path = self.reports_dir / f'{report_id}.tar.gz'
        with tarfile.open(report_path, 'w:gz') as tar:
            for filename in [
                'summary.json',
                'environment.json',
                'cgroup.ndjson',
                'disk.ndjson',
                'process.ndjson',
                'events.ndjson',
            ]:
                target = session.runtime_dir / filename
                if target.exists():
                    tar.add(target, arcname=filename)

        stat = report_path.stat()
        return ObservationReportInfo(
            report_id=report_id,
            session_id=session.session_id,
            reason=reason,
            created_at=datetime.now(UTC),
            path=str(report_path),
            size_bytes=stat.st_size,
        )

    def _build_summary(
        self,
        session: ObservationSession,
        reason: str,
    ) -> dict[str, Any]:
        peak_memory = 0
        peak_cpu = 0.0
        oom_events = 0
        oom_kill_events = 0
        comm_counter: Counter[str] = Counter()
        timeline: list[dict[str, Any]] = []

        cgroup_path = session.runtime_dir / 'cgroup.ndjson'
        if cgroup_path.exists():
            for row in self._iter_ndjson(cgroup_path):
                peak_memory = max(peak_memory, int(row.get('mem_current_bytes', 0) or 0))
                cpu_usage_pct = row.get('cpu_usage_pct')
                if isinstance(cpu_usage_pct, (int, float)):
                    peak_cpu = max(peak_cpu, float(cpu_usage_pct))

        process_path = session.runtime_dir / 'process.ndjson'
        if process_path.exists():
            for row in self._iter_ndjson(process_path):
                comm = row.get('comm')
                if isinstance(comm, str) and comm:
                    comm_counter[comm] += 1

        events_path = session.runtime_dir / 'events.ndjson'
        if events_path.exists():
            for row in self._iter_ndjson(events_path):
                row_type = str(row.get('type', ''))
                if row_type == 'oom':
                    oom_events += 1
                if row_type == 'oom_kill':
                    oom_kill_events += 1
                timeline.append(row)

        return {
            'session_id': session.session_id,
            'mode': session.mode,
            'reason': reason,
            'interval_seconds': session.interval_seconds,
            'started_at': session.started_at.isoformat(),
            'ended_at': session.stopped_at.isoformat() if session.stopped_at else None,
            'deadline_at': session.ends_at.isoformat() if session.ends_at else None,
            'peak_memory_bytes': peak_memory,
            'peak_cpu_pct': peak_cpu,
            'oom_event_count': oom_events,
            'oom_kill_event_count': oom_kill_events,
            'top_processes': comm_counter.most_common(5),
            'timeline': timeline[-10:],
        }

    def _build_environment(
        self, session: ObservationSession, current_user: str
    ) -> dict[str, Any]:
        return {
            'session_id': session.session_id,
            'hostname': socket.gethostname(),
            'user': current_user,
            'workspace': os.environ.get('WORKSPACE', '/workspace'),
            'image_version': os.environ.get('IMAGE_VERSION', ''),
            'runtime_dir': str(session.runtime_dir),
        }

    def _iter_ndjson(self, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            rows.append(json.loads(stripped))
        return rows

    def _choose_writable_dir(self, candidates: list[Path | None]) -> Path:
        last_error: OSError | None = None
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / '.aio-observe-probe'
                probe.write_text('ok')
                probe.unlink()
                return candidate
            except OSError as exc:
                last_error = exc
                continue
        raise OSError(f'Unable to create observation directory: {last_error}')
