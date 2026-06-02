"""File watch API endpoints — SSE, long-polling, sync wait."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.service_container import services
from app.schemas.file_watch import CreateWatchRequest, PollRequest, WaitRequest
from app.services.file_watch import WatcherLimitExceededError


logger = logging.getLogger(__name__)
router = APIRouter()


def _poll_response(
    events: list[dict],
    requested_cursor: int,
    limit: int,
    overflow: bool,
) -> dict:
    limited_events = events[:limit]
    cursor = limited_events[-1]['seq'] if limited_events else requested_cursor
    return {'events': limited_events, 'cursor': cursor, 'overflow': overflow}


def _watcher_is_closing(svc, watcher_id: str) -> bool:
    checker = getattr(svc, 'is_watcher_closing', None)
    if checker is None:
        return False
    return bool(checker(watcher_id))


def _existing_file_event(watcher_id: str, path: str) -> dict:
    stat = os.stat(path)
    now = time.time()
    return {
        'seq': 0,
        'watcher_id': watcher_id,
        'type': 'create',
        'path': path,
        'relative_path': os.path.basename(path),
        'old_path': None,
        'is_dir': int(os.path.isdir(path)),
        'timestamp': now,
        'mtime': stat.st_mtime,
        'size': int(stat.st_size),
        'inode': int(stat.st_ino) if hasattr(stat, 'st_ino') else None,
    }


def _wait_path_matches(event_path: str, requested_path: str, resolved_path: str) -> bool:
    if event_path == requested_path or event_path == resolved_path:
        return True
    return os.path.realpath(event_path) == resolved_path


def _present_wait_event(
    event: dict,
    requested_path: str,
    resolved_path: str,
) -> dict:
    if event['path'] == requested_path:
        return event
    if os.path.realpath(event['path']) != resolved_path:
        return event
    presented = dict(event)
    presented['path'] = requested_path
    return presented


# ── Create watcher ─────────────────────────────────────────────


@router.post(
    '/watch',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'watch_create',
    },
)
async def create_watch(req: CreateWatchRequest):
    svc = services.require('file_watch_service')
    try:
        result = await svc.create_watcher(req)
        return {'data': result.model_dump()}
    except WatcherLimitExceededError as e:
        raise HTTPException(
            status_code=429,
            detail={'code': 'WATCH_LIMIT_EXCEEDED', 'message': str(e)},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── SSE event stream ──────────────────────────────────────────


@router.get(
    '/watch/{watcher_id}/events',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'watch_events',
    },
)
async def watch_events(watcher_id: str, request: Request):
    svc = services.require('file_watch_service')
    store = services.require('state_store')

    aw = svc.get_watcher(watcher_id)
    persisted = None
    if not aw:
        persisted = await store.get_watcher(watcher_id)
    if _watcher_is_closing(svc, watcher_id):
        raise HTTPException(
            status_code=404,
            detail={'code': 'WATCHER_NOT_FOUND', 'message': f'Watcher {watcher_id} not found'},
        )
    if not aw and not persisted:
        raise HTTPException(
            status_code=404,
            detail={'code': 'WATCHER_NOT_FOUND', 'message': f'Watcher {watcher_id} not found'},
        )

    # Parse Last-Event-ID for reconnection
    last_seq = 0
    last_event_id = request.headers.get('last-event-id', '')
    if last_event_id and ':' in last_event_id:
        try:
            last_seq = int(last_event_id.rsplit(':', 1)[1])
        except (ValueError, IndexError):
            pass

    watcher_path = aw.path if aw else persisted['path']
    subscriber_id = uuid.uuid4().hex[:8]
    subscribed = False
    if aw:
        await svc.subscribe(watcher_id, subscriber_id)
        subscribed = True

    async def generate():
        nonlocal last_seq
        heartbeat_interval = 30
        try:
            # First message
            yield f'event: watch_started\ndata: {json.dumps({"watcher_id": watcher_id, "path": watcher_path})}\n\n'

            # Replay missed events on reconnection
            if last_seq > 0:
                events, _, overflow = await store.get_events_since(watcher_id, last_seq)
                if overflow:
                    yield f'event: overflow\ndata: {json.dumps({"message": "event buffer overflow, please full refresh", "watcher_id": watcher_id})}\n\n'
                    return
                for e in events:
                    yield f'id: {watcher_id}:{e["seq"]}\nevent: file_change\ndata: {json.dumps(e)}\n\n'
                    last_seq = e['seq']

            while True:
                events, _, overflow = await store.wait_for_events(
                    watcher_id,
                    last_seq,
                    timeout=heartbeat_interval,
                )
                if (
                    not events
                    and not overflow
                    and (
                        _watcher_is_closing(svc, watcher_id)
                        or await store.get_watcher(watcher_id) is None
                    )
                ):
                    return
                if not events and not overflow and svc.get_watcher(watcher_id) is None:
                    return
                if not events and not overflow:
                    yield ': heartbeat\n\n'
                    if await request.is_disconnected():
                        break
                    continue

                if overflow:
                    yield f'event: overflow\ndata: {json.dumps({"message": "event buffer overflow", "watcher_id": watcher_id})}\n\n'
                    return
                for e in events:
                    yield f'id: {watcher_id}:{e["seq"]}\nevent: file_change\ndata: {json.dumps(e)}\n\n'
                    last_seq = e['seq']
        finally:
            if subscribed:
                try:
                    await svc.unsubscribe(watcher_id, subscriber_id)
                except KeyError:
                    logger.debug('SSE watcher %s was already cleaned up before unsubscribe', watcher_id)

    return StreamingResponse(generate(), media_type='text/event-stream')


# ── POST long-polling ─────────────────────────────────────────


@router.post(
    '/watch/{watcher_id}/poll',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'watch_poll',
    },
)
async def poll_events(watcher_id: str, req: PollRequest):
    svc = services.require('file_watch_service')
    store = services.require('state_store')

    aw = svc.get_watcher(watcher_id)
    persisted = None
    if not aw:
        persisted = await store.get_watcher(watcher_id)
    if _watcher_is_closing(svc, watcher_id):
        raise HTTPException(
            status_code=404,
            detail={'code': 'WATCHER_NOT_FOUND', 'message': f'Watcher {watcher_id} not found'},
        )
    if not aw and not persisted:
        raise HTTPException(
            status_code=404,
            detail={'code': 'WATCHER_NOT_FOUND', 'message': f'Watcher {watcher_id} not found'},
        )

    # Immediate check
    events, _, overflow = await store.wait_for_events(
        watcher_id,
        req.cursor,
        timeout=req.timeout,
    )
    if (
        not events
        and not overflow
        and (
            _watcher_is_closing(svc, watcher_id)
            or await store.get_watcher(watcher_id) is None
        )
    ):
        raise HTTPException(
            status_code=404,
            detail={'code': 'WATCHER_NOT_FOUND', 'message': f'Watcher {watcher_id} not found'},
        )
    return _poll_response(events, req.cursor, req.limit, overflow)


# ── Sync wait for specific file ──────────────────────────────


@router.post(
    '/watch/wait',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'watch_wait',
    },
)
async def wait_for_file(req: WaitRequest):
    svc = services.require('file_watch_service')
    store = services.require('state_store')

    requested_path = os.path.abspath(req.path)
    target_path = os.path.realpath(req.path)

    # Create temporary watcher on the requested parent path so wait shares the
    # same outward path semantics as the normal watcher APIs.
    parent = os.path.dirname(requested_path)
    if not os.path.isdir(parent):
        raise HTTPException(status_code=400, detail=f'Parent directory {parent} does not exist')

    watch_req = CreateWatchRequest(path=parent, recursive=False, debounce=100)
    try:
        watcher = await svc.create_watcher(watch_req)
    except WatcherLimitExceededError as e:
        raise HTTPException(
            status_code=429,
            detail={'code': 'WATCH_LIMIT_EXCEEDED', 'message': str(e)},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    watcher_id = watcher.watcher_id
    abs_path = target_path
    last_seq = await store.get_latest_seq(watcher_id)
    subscriber_id = uuid.uuid4().hex[:8]
    await svc.subscribe(watcher_id, subscriber_id)

    try:
        # Fast path only applies to callers waiting for presence/create semantics.
        if os.path.exists(requested_path) and 'create' in req.event_types:
            existing_events, _, _ = await store.get_events_since(watcher_id, last_seq)
            for event in existing_events:
                if _wait_path_matches(event['path'], requested_path, abs_path) and event['type'] in req.event_types:
                    return {'event': _present_wait_event(event, requested_path, abs_path)}
            return {'event': _existing_file_event(watcher_id, requested_path)}

        deadline = asyncio.get_event_loop().time() + req.timeout

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise HTTPException(
                    status_code=408,
                    detail={
                        'code': 'WATCH_TIMEOUT',
                        'message': f'File {req.path} did not appear within {req.timeout}s',
                    },
                )

            events, new_cursor, _ = await store.wait_for_events(
                watcher_id,
                last_seq,
                timeout=min(remaining, 5.0),
            )
            if not events:
                continue

            last_seq = new_cursor
            for e in events:
                if _wait_path_matches(e['path'], requested_path, abs_path) and e['type'] in req.event_types:
                    return {'event': _present_wait_event(e, requested_path, abs_path)}
    finally:
        try:
            await svc.unsubscribe(watcher_id, subscriber_id)
        except KeyError:
            logger.debug('Temporary watcher %s was already cleaned up before unsubscribe', watcher_id)
        try:
            await svc.stop_watcher(watcher_id)
        except KeyError:
            logger.debug('Temporary watcher %s was already cleaned up', watcher_id)


# ── List watchers ─────────────────────────────────────────────


@router.get(
    '/watch',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'watch_list',
    },
)
async def list_watches():
    svc = services.require('file_watch_service')
    watchers = await svc.list_watchers()
    return {'data': watchers}


# ── Stop watcher ──────────────────────────────────────────────


@router.delete(
    '/watch/{watcher_id}',
    openapi_extra={
        'x-fern-sdk-group-name': 'file',
        'x-fern-sdk-method-name': 'watch_stop',
    },
)
async def stop_watch(watcher_id: str):
    svc = services.require('file_watch_service')
    try:
        result = await svc.stop_watcher(watcher_id)
        return {'data': result.model_dump()}
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={'code': 'WATCHER_NOT_FOUND', 'message': f'Watcher {watcher_id} not found'},
        )
