from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.exceptions import BadRequestException, ResourceNotFoundException
from app.models.observation import (
    ObservationEvent,
    ObservationExportResult,
    ObservationLiveSnapshot,
    ObservationMode,
    ObservationReportInfo,
    ObservationStartResult,
    ObservationStatus,
    ObservationStopResult,
)
from app.services.observation_sampler import ObservationSample, ObservationSampler
from app.services.observation_session import CaptureMode, ObservationSession
from app.services.observation_store import ObservationStore


logger = logging.getLogger(__name__)

DEFAULT_OBSERVATION_DURATION_SECONDS = 900
MAX_OBSERVATION_DURATION_SECONDS = 3600


class ObservationService:
    def __init__(
        self,
        runtime_root: Path | None = None,
        state_root: Path | None = None,
        disk_paths: list[str] | None = None,
        current_user: str | None = None,
    ) -> None:
        self._store = ObservationStore(runtime_root=runtime_root, state_root=state_root)
        self._sampler = ObservationSampler(
            current_user=current_user,
            disk_paths=disk_paths,
        )

        self._lock = asyncio.Lock()
        self._sample_lock = asyncio.Lock()
        self._storage_lock = asyncio.Lock()
        self._collector_stop: asyncio.Event | None = None
        self._collector_task: asyncio.Task[None] | None = None
        self._active_session: ObservationSession | None = None
        self._sessions: dict[str, ObservationSession] = {}
        self._reports: dict[str, ObservationReportInfo] = {}
        self._recent_events_by_session: dict[str, deque[ObservationEvent]] = {}
        self._last_completed_session_id: str | None = None
        self._last_sample_at: datetime | None = None

    async def initialize(self) -> None:
        await asyncio.to_thread(self._store.initialize)
        await asyncio.to_thread(self._sampler.initialize)
        self._reports = await asyncio.to_thread(self._store.load_report_index)

    async def shutdown(self) -> None:
        async with self._lock:
            stop_event = self._collector_stop
            collector_task = self._collector_task
        if stop_event is not None:
            stop_event.set()
        if collector_task is not None:
            await asyncio.gather(collector_task, return_exceptions=True)

    async def status(self) -> ObservationStatus:
        async with self._lock:
            session = self._active_session
            runtime_dir = (
                str(session.runtime_dir)
                if session
                else str(self._store.runtime_root)
                if self._store.runtime_root
                else None
            )
            return ObservationStatus(
                mode=session.mode if session else 'off',
                running=session is not None,
                session_id=session.session_id if session else None,
                started_at=session.started_at if session else None,
                ends_at=session.ends_at if session else None,
                interval_seconds=session.interval_seconds if session else None,
                last_sample_at=self._last_sample_at,
                runtime_dir=runtime_dir,
                report_count=len(self._reports),
            )

    async def live_snapshot(self, top_rows: int = 10) -> ObservationLiveSnapshot:
        if top_rows <= 0:
            raise BadRequestException('top_rows must be greater than 0')
        async with self._lock:
            session = self._active_session
            mode: ObservationMode = session.mode if session else 'off'
        return await self._collect_snapshot(
            mode=mode,
            include_processes=True,
            include_disk=True,
            top_rows=top_rows,
            process_limit=top_rows,
            include_fd_counts=True,
            persist=False,
            session=session,
        )

    async def start(
        self,
        mode: CaptureMode,
        idempotency_key: str | None = None,
        duration_seconds: int | None = None,
        interval_seconds: float | None = None,
        include_processes: bool | None = None,
        include_disk: bool = True,
    ) -> ObservationStartResult:
        if mode not in ('guardrail', 'capture'):
            raise BadRequestException(f'Unsupported observation mode: {mode}')

        resolved_interval = interval_seconds or (5.0 if mode == 'guardrail' else 2.0)
        if resolved_interval <= 0:
            raise BadRequestException('interval_seconds must be greater than 0')

        if duration_seconds is None:
            resolved_duration_seconds = DEFAULT_OBSERVATION_DURATION_SECONDS
        else:
            resolved_duration_seconds = duration_seconds

        if resolved_duration_seconds <= 0:
            raise BadRequestException('duration_seconds must be greater than 0')
        if resolved_duration_seconds > MAX_OBSERVATION_DURATION_SECONDS:
            raise BadRequestException(
                f'duration_seconds must be less than or equal to {MAX_OBSERVATION_DURATION_SECONDS}'
            )

        if include_processes is None:
            include_processes = mode == 'capture'

        if self._store.runtime_root is None:
            raise BadRequestException('Observation service is not initialized')

        now = datetime.now(UTC)
        request_fingerprint = self._make_start_request_fingerprint(
            mode=mode,
            duration_seconds=resolved_duration_seconds,
            interval_seconds=resolved_interval,
            include_processes=include_processes,
            include_disk=include_disk,
        )

        async with self._lock:
            if idempotency_key:
                existing = self._find_session_by_start_key(idempotency_key)
                if existing is not None:
                    if existing.request_fingerprint != request_fingerprint:
                        raise BadRequestException(
                            'idempotency_key already used with different observation start parameters'
                        )
                    return self._build_start_result(existing)
            if self._active_session is not None:
                raise BadRequestException(
                    f'Observation session already running: {self._active_session.session_id}'
                )
            session_id = self._make_session_id(now)
            runtime_dir = self._store.runtime_root / mode / session_id
            session = ObservationSession(
                session_id=session_id,
                mode=mode,
                started_at=now,
                ends_at=now + timedelta(seconds=resolved_duration_seconds)
                if resolved_duration_seconds
                else None,
                stopped_at=None,
                interval_seconds=resolved_interval,
                include_processes=include_processes,
                include_disk=include_disk,
                runtime_dir=runtime_dir,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            await asyncio.to_thread(self._store.prepare_session_dir, session)
            self._active_session = session
            self._sessions[session_id] = session
            self._recent_events_by_session[session_id] = deque(maxlen=100)
            self._collector_stop = asyncio.Event()
            await asyncio.to_thread(self._store.write_current_session, session)

        await self._append_event(
            ObservationEvent(
                ts=now,
                type='observe_start',
                message=f'Observation {mode} session started',
                data={
                    'session_id': session_id,
                    'interval_seconds': resolved_interval,
                    'include_processes': include_processes,
                    'include_disk': include_disk,
                },
            ),
            session=session,
        )

        await self._collect_snapshot(
            mode=mode,
            include_processes=include_processes,
            include_disk=include_disk,
            top_rows=50,
            process_limit=50 if include_processes else None,
            include_fd_counts=False,
            persist=True,
            session=session,
        )

        task = asyncio.create_task(self._run_collector(session), name=f'observe-{session_id}')
        async with self._lock:
            self._collector_task = task

        return self._build_start_result(session)

    async def stop(self, session_id: str | None = None) -> ObservationStopResult:
        async with self._lock:
            session = self._active_session
            stop_event = self._collector_stop
            collector_task = self._collector_task

        if session is None:
            if session_id:
                completed = self._sessions.get(session_id)
                if completed is not None:
                    return self._build_stop_result(completed)
                raise BadRequestException(f'No observation session found for session_id={session_id}')
            if self._last_completed_session_id is not None:
                completed = self._sessions.get(self._last_completed_session_id)
                if completed is not None:
                    return self._build_stop_result(completed)
            raise BadRequestException('No active observation session')

        if session_id and session.session_id != session_id:
            raise BadRequestException(
                f'Active observation session is {session.session_id}, not {session_id}'
            )

        if stop_event is not None:
            stop_event.set()
        if collector_task is not None:
            await asyncio.gather(collector_task, return_exceptions=True)

        return self._build_stop_result(session)

    async def export_report(
        self,
        session_id: str | None = None,
        reason: str = 'manual',
        idempotency_key: str | None = None,
    ) -> ObservationExportResult:
        session = await self._resolve_session_for_export(session_id)
        report_id = self._make_report_id(session.session_id, idempotency_key)

        async with self._storage_lock:
            existing = self._reports.get(report_id)
            if existing is not None:
                if existing.session_id != session.session_id or existing.reason != reason:
                    raise BadRequestException(
                        'idempotency_key already used with different observation export parameters'
                    )
                return ObservationExportResult(**existing.model_dump())

            if idempotency_key is None and not session.runtime_dir.exists():
                latest_report = self._find_latest_report_for_session(
                    session.session_id,
                    reason=reason,
                )
                if latest_report is not None:
                    return ObservationExportResult(**latest_report.model_dump())

            info = await asyncio.to_thread(
                self._store.build_report,
                session,
                report_id,
                reason,
                self._sampler.current_user,
            )
            self._reports[report_id] = info
            await asyncio.to_thread(self._store.persist_report_index, self._reports)
            if self._active_session is None or self._active_session.session_id != session.session_id:
                await asyncio.to_thread(self._store.delete_session_runtime, session)
        await self._append_event(
            ObservationEvent(
                ts=datetime.now(UTC),
                type='observe_export',
                message='Observation report exported',
                data={
                    'session_id': session.session_id,
                    'report_id': report_id,
                    'reason': reason,
                },
            ),
            session=self._active_session
            if self._active_session
            and self._active_session.session_id == session.session_id
            else None,
        )
        return ObservationExportResult(**info.model_dump())

    async def list_reports(self) -> list[ObservationReportInfo]:
        return sorted(
            self._reports.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )

    async def get_report_path(self, report_id: str) -> Path:
        info = self._reports.get(report_id)
        if info is None:
            raise ResourceNotFoundException(f'Observation report not found: {report_id}')
        path = self._store.reports_dir / f'{report_id}.tar.gz' if self._store.reports_dir else None
        if path is None or not path.exists():
            path = None
        if path is None:
            # Fallback to persisted absolute path so old reports still download.
            path = Path(info.path)
        if not path.exists():
            raise ResourceNotFoundException(f'Observation report file not found: {report_id}')
        return path

    async def delete_report(self, report_id: str) -> ObservationReportInfo:
        info = self._reports.get(report_id)
        if info is None:
            raise ResourceNotFoundException(f'Observation report not found: {report_id}')
        path = await self.get_report_path(report_id)
        await asyncio.to_thread(path.unlink)
        del self._reports[report_id]
        await asyncio.to_thread(self._store.persist_report_index, self._reports)
        return info

    async def _run_collector(self, session: ObservationSession) -> None:
        try:
            while True:
                async with self._lock:
                    stop_event = self._collector_stop
                if stop_event is None or stop_event.is_set():
                    break
                if session.ends_at and datetime.now(UTC) >= session.ends_at:
                    await self._append_event(
                        ObservationEvent(
                            ts=datetime.now(UTC),
                            type='observe_timeout',
                            message='Observation session reached configured duration',
                            data={'session_id': session.session_id},
                        ),
                        session=session,
                    )
                    break

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=session.interval_seconds)
                except asyncio.TimeoutError:
                    pass

                if stop_event.is_set():
                    break
                if session.ends_at and datetime.now(UTC) >= session.ends_at:
                    await self._append_event(
                        ObservationEvent(
                            ts=datetime.now(UTC),
                            type='observe_timeout',
                            message='Observation session reached configured duration',
                            data={'session_id': session.session_id},
                        ),
                        session=session,
                    )
                    break

                await self._collect_snapshot(
                    mode=session.mode,
                    include_processes=session.include_processes,
                    include_disk=session.include_disk,
                    top_rows=50,
                    process_limit=50 if session.include_processes else None,
                    include_fd_counts=False,
                    persist=True,
                    session=session,
                )
        except asyncio.CancelledError:
            logger.info('Observation collector cancelled')
        except Exception:
            logger.exception('Observation collector crashed')
        finally:
            await self._finalize_session(session)

    async def _finalize_session(self, session: ObservationSession) -> None:
        stopped_at = datetime.now(UTC)
        session.stopped_at = stopped_at
        await self._append_event(
            ObservationEvent(
                ts=stopped_at,
                type='observe_stop',
                message='Observation session stopped',
                data={'session_id': session.session_id},
            ),
            session=session,
        )
        async with self._lock:
            if self._active_session and self._active_session.session_id == session.session_id:
                self._active_session = None
            self._collector_task = None
            self._collector_stop = None
            self._last_completed_session_id = session.session_id
            await asyncio.to_thread(self._store.clear_current_session)

    async def _resolve_session_for_export(
        self, session_id: str | None
    ) -> ObservationSession:
        async with self._lock:
            if session_id:
                session = self._sessions.get(session_id)
                if session is None:
                    raise ResourceNotFoundException(
                        f'Observation session not found: {session_id}'
                    )
                return session
            if self._active_session is not None:
                return self._active_session
            if self._last_completed_session_id is not None:
                session = self._sessions.get(self._last_completed_session_id)
                if session is not None:
                    return session
        raise BadRequestException('No observation session available to export')

    async def _collect_snapshot(
        self,
        mode: ObservationMode,
        include_processes: bool,
        include_disk: bool,
        top_rows: int,
        process_limit: int | None,
        include_fd_counts: bool,
        persist: bool,
        session: ObservationSession | None,
    ) -> ObservationLiveSnapshot:
        async with self._sample_lock:
            raw_snapshot = await asyncio.to_thread(
                self._sampler.sample,
                include_processes,
                include_disk,
                process_limit,
                include_fd_counts,
            )

        events = raw_snapshot['events']
        for event in events:
            await self._append_event(event, session=session)

        snapshot = ObservationLiveSnapshot(
            captured_at=raw_snapshot['captured_at'],
            mode=mode,
            cgroup=raw_snapshot['cgroup'],
            disk=raw_snapshot['disk'],
            top_processes=raw_snapshot['top_processes'][:top_rows],
            recent_events=self._build_recent_events_view(
                session=session,
                transient_events=events,
            ),
        )
        self._last_sample_at = snapshot.captured_at

        if persist and session is not None:
            await self._persist_snapshot(session, raw_snapshot)
        return snapshot

    async def _persist_snapshot(
        self,
        session: ObservationSession,
        raw_snapshot: ObservationSample,
    ) -> None:
        async with self._storage_lock:
            await asyncio.to_thread(
                self._store.append_json_line,
                session.runtime_dir / 'cgroup.ndjson',
                {
                    'ts': raw_snapshot['captured_at'].isoformat(),
                    **raw_snapshot['cgroup'].model_dump(),
                },
            )
            for disk_entry in raw_snapshot['disk']:
                await asyncio.to_thread(
                    self._store.append_json_line,
                    session.runtime_dir / 'disk.ndjson',
                    {
                        'ts': raw_snapshot['captured_at'].isoformat(),
                        **disk_entry.model_dump(),
                    },
                )
            if session.include_processes:
                for proc in raw_snapshot['top_processes']:
                    await asyncio.to_thread(
                        self._store.append_json_line,
                        session.runtime_dir / 'process.ndjson',
                        {
                            'ts': raw_snapshot['captured_at'].isoformat(),
                            **proc.model_dump(),
                        },
                    )

    async def _append_event(
        self,
        event: ObservationEvent,
        session: ObservationSession | None,
    ) -> None:
        if session is not None:
            recent_events = self._recent_events_by_session.setdefault(
                session.session_id,
                deque(maxlen=100),
            )
            recent_events.append(event)
        if session is not None:
            async with self._storage_lock:
                await asyncio.to_thread(
                    self._store.append_json_line,
                    session.runtime_dir / 'events.ndjson',
                    event.model_dump(mode='json'),
                )

    def _build_recent_events_view(
        self,
        session: ObservationSession | None,
        transient_events: list[ObservationEvent],
    ) -> list[ObservationEvent]:
        if session is None:
            return transient_events[-10:]
        return list(self._recent_events_by_session.get(session.session_id, []))[-10:]

    def _build_start_result(self, session: ObservationSession) -> ObservationStartResult:
        return ObservationStartResult(
            session_id=session.session_id,
            mode=session.mode,
            started_at=session.started_at,
            ends_at=session.ends_at,
            interval_seconds=session.interval_seconds,
            runtime_dir=str(session.runtime_dir),
        )

    def _build_stop_result(self, session: ObservationSession) -> ObservationStopResult:
        return ObservationStopResult(
            session_id=session.session_id,
            stopped=True,
            report_ready=self._report_ready_for_session(session),
        )

    def _report_ready_for_session(self, session: ObservationSession) -> bool:
        if session.runtime_dir.exists():
            return True
        return any(report.session_id == session.session_id for report in self._reports.values())

    def _find_session_by_start_key(self, idempotency_key: str) -> ObservationSession | None:
        for session in self._sessions.values():
            if session.idempotency_key == idempotency_key:
                return session
        return None

    def _find_latest_report_for_session(
        self,
        session_id: str,
        *,
        reason: str,
    ) -> ObservationReportInfo | None:
        matches = [
            report
            for report in self._reports.values()
            if report.session_id == session_id and report.reason == reason
        ]
        if not matches:
            return None
        return max(matches, key=lambda item: item.created_at)

    def _make_start_request_fingerprint(
        self,
        *,
        mode: CaptureMode,
        duration_seconds: int,
        interval_seconds: float,
        include_processes: bool,
        include_disk: bool,
    ) -> str:
        return json.dumps(
            {
                'mode': mode,
                'duration_seconds': duration_seconds,
                'interval_seconds': interval_seconds,
                'include_processes': include_processes,
                'include_disk': include_disk,
            },
            sort_keys=True,
            separators=(',', ':'),
        )

    def _make_session_id(self, now: datetime) -> str:
        return f'obs_{now.strftime("%Y%m%d_%H%M%S_%f")}'

    def _make_report_id(
        self,
        session_id: str,
        idempotency_key: str | None,
    ) -> str:
        if idempotency_key:
            digest = hashlib.sha256(
                f'{session_id}\0{idempotency_key}'.encode('utf-8')
            ).hexdigest()[:16]
            return f'obs_report_{digest}'
        return f'obs_report_{datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")}'
