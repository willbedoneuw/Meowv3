"""Restart-safe multi-account Telegram send orchestrator.

This module owns its SQLite tables and only uses public helpers from
``telegram_client`` for client acquisition, contact discovery, and sending.
It deliberately contains no Telegram client or session construction logic.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterable

import db
import telegram_client as tg

_ACTIVE_STATES = {"queued", "running", "waiting", "stop_requested"}
_TERMINAL_STATES = {"completed", "failed"}
_TASKS: dict[str, asyncio.Task] = {}
_TASKS_LOCK = threading.RLock()
_SCHEMA_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    parent = os.path.dirname(os.path.abspath(db.DB_PATH))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _init() -> None:
    with _SCHEMA_LOCK, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tg_multi_jobs (
                job_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                content_json TEXT NOT NULL,
                delay REAL NOT NULL DEFAULT 0,
                recipient_index INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                sent_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                uncertain_count INTEGER NOT NULL DEFAULT 0,
                current_account TEXT,
                first_account TEXT,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                last_error TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tg_multi_accounts (
                job_id TEXT NOT NULL,
                phone TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                cooldown_until REAL NOT NULL DEFAULT 0,
                sent_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT DEFAULT '',
                PRIMARY KEY (job_id, phone)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tg_multi_recipients (
                job_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                recipient_key TEXT NOT NULL,
                target_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                sent_by TEXT,
                last_error TEXT DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY (job_id, idx),
                UNIQUE (job_id, recipient_key)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tg_multi_attempts (
                job_id TEXT NOT NULL,
                recipient_idx INTEGER NOT NULL,
                phone TEXT NOT NULL,
                outcome TEXT NOT NULL,
                detail TEXT DEFAULT '',
                attempted_at REAL NOT NULL,
                PRIMARY KEY (job_id, recipient_idx, phone)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tgm_jobs_state ON tg_multi_jobs(state, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tgm_recip_state ON tg_multi_recipients(job_id, state, idx)")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _target_record(value: Any) -> tuple[str, Any]:
    """Turn common recipient forms into a durable key and sendable reference."""
    if isinstance(value, dict):
        explicit_key = value.get("key") or value.get("recipient_key")
        target = value.get("target", value.get("ref", value.get("entity")))
        if target is None and value.get("username"):
            target = "@" + str(value["username"]).lstrip("@")
        if target is None:
            target = value.get("user_id", value.get("id"))
        if target is None:
            raise ValueError("recipient dict needs target/ref/username/user_id")
        key = str(explicit_key) if explicit_key is not None else _recipient_key(target)
        return key, target
    if not isinstance(value, (str, int)):
        username = getattr(value, "username", None)
        target = "@" + str(username).lstrip("@") if username else getattr(value, "id", None)
        if target is None:
            raise ValueError(f"unsupported recipient: {type(value).__name__}")
        return _recipient_key(target), target
    return _recipient_key(value), value


def _recipient_key(target: Any) -> str:
    if isinstance(target, int):
        return f"id:{target}"
    text = str(target).strip()
    if not text:
        raise ValueError("empty recipient")
    return "ref:" + text.casefold()


def _normalize_content(content: dict | None, text: str, file_path: str,
                       caption: str, typing: float, send_timeout: float) -> dict:
    value = dict(content or {})
    value.setdefault("text", text or "")
    value.setdefault("file_path", file_path or "")
    value.setdefault("caption", caption or "")
    value.setdefault("typing", max(0.0, float(typing or 0)))
    value.setdefault("send_timeout", max(5.0, float(send_timeout or 120)))
    if not value["text"] and not value["file_path"]:
        raise ValueError("text or file_path is required")
    return value


def _account_phones(accounts: Iterable[Any] | None) -> list[str]:
    if accounts is None:
        accounts = [a for a in db.tg_list_accounts() if a.get("status") == "active"]
    out: list[str] = []
    seen: set[str] = set()
    for item in accounts:
        phone = item.get("phone") if isinstance(item, dict) else str(item)
        phone = (phone or "").strip()
        if not phone or phone in seen:
            continue
        row = db.tg_get_account(phone)
        if not row or row.get("status") != "active" or not row.get("session"):
            continue
        seen.add(phone)
        out.append(phone)
    if not out:
        raise ValueError("no eligible Telegram accounts")
    return out


async def _discover_recipients(phones: list[str]) -> list[Any]:
    """Build one ordered, deduplicated union through telegram_client APIs."""
    result: list[Any] = []
    seen: set[str] = set()
    for phone in phones:
        client = await tg.get_client(phone)
        targets, _mutual_count = await tg.get_contacts_ordered(client)
        for target in targets:
            key, durable = _target_record(target)
            if key not in seen:
                seen.add(key)
                result.append(durable)
    return result


async def create_job(account_phones: Iterable[Any] | None = None,
                     recipients: Iterable[Any] | None = None,
                     content: dict | None = None, *, accounts: Iterable[Any] | None = None,
                     text: str = "", file_path: str = "", caption: str = "",
                     typing: float = 0.0, delay: float | None = None,
                     send_timeout: float = 120.0, job_id: str | None = None) -> dict:
    """Create a durable job without starting it.

    ``recipients`` may contain ids, usernames/refs, dicts, or Telethon user-like
    objects. If omitted, an ordered union of the selected accounts' contacts is
    built via :func:`telegram_client.get_contacts_ordered`.
    """
    _init()
    phones = _account_phones(account_phones if account_phones is not None else accounts)
    if recipients is None:
        recipient_values = await _discover_recipients(phones)
    else:
        recipient_values = list(recipients)
    normalized: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for value in recipient_values:
        key, target = _target_record(value)
        if key in seen:
            continue
        seen.add(key)
        normalized.append((key, target))
    if not normalized:
        raise ValueError("no recipients")

    payload = _normalize_content(content, text, file_path, caption, typing, send_timeout)
    if delay is None:
        delay = db.tg_get_send_delay()
    delay = max(0.0, float(delay))
    job_id = str(job_id or uuid.uuid4().hex)
    now = time.time()
    with _connect() as conn:
        existing = conn.execute("SELECT 1 FROM tg_multi_jobs WHERE job_id=?", (job_id,)).fetchone()
        if existing:
            return status(job_id)
        conn.execute(
            "INSERT INTO tg_multi_jobs(job_id,state,content_json,delay,total,created_at,updated_at) "
            "VALUES(?, 'queued', ?, ?, ?, ?, ?)",
            (job_id, _json(payload), delay, len(normalized), now, now),
        )
        conn.executemany(
            "INSERT INTO tg_multi_accounts(job_id,phone,ordinal) VALUES(?,?,?)",
            [(job_id, phone, i) for i, phone in enumerate(phones)],
        )
        conn.executemany(
            "INSERT INTO tg_multi_recipients(job_id,idx,recipient_key,target_json,updated_at) "
            "VALUES(?,?,?,?,?)",
            [(job_id, i, key, _json(target), now) for i, (key, target) in enumerate(normalized)],
        )
    return status(job_id)


def _job(job_id: str) -> dict | None:
    _init()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tg_multi_jobs WHERE job_id=?", (str(job_id),)).fetchone()
    return dict(row) if row else None


def status(job_id: str) -> dict:
    """Return a durable job snapshot, including per-account cooldowns."""
    row = _job(job_id)
    if not row:
        raise KeyError(f"unknown job: {job_id}")
    now = time.time()
    with _connect() as conn:
        accounts = [dict(r) for r in conn.execute(
            "SELECT phone,ordinal,enabled,cooldown_until,sent_count,failure_count,last_error "
            "FROM tg_multi_accounts WHERE job_id=? ORDER BY ordinal", (job_id,)).fetchall()]
        counts = {r["state"]: int(r["n"]) for r in conn.execute(
            "SELECT state,COUNT(*) AS n FROM tg_multi_recipients WHERE job_id=? GROUP BY state",
            (job_id,)).fetchall()}
    for account in accounts:
        account["cooldown_remaining"] = max(0.0, float(account["cooldown_until"] or 0) - now)
        account["enabled"] = bool(account["enabled"])
    row["stop_requested"] = bool(row["stop_requested"])
    row["content"] = json.loads(row.pop("content_json") or "{}")
    row["recipient_counts"] = counts
    row["accounts"] = accounts
    with _TASKS_LOCK:
        task = _TASKS.get(job_id)
        row["in_process"] = bool(task and not task.done())
    return row


def list_jobs(state: str | None = None, limit: int = 100) -> list[dict]:
    """List newest jobs; snapshots are intentionally compact."""
    _init()
    limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        if state:
            rows = conn.execute(
                "SELECT job_id FROM tg_multi_jobs WHERE state=? ORDER BY created_at DESC LIMIT ?",
                (state, limit),).fetchall()
        else:
            rows = conn.execute(
                "SELECT job_id FROM tg_multi_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [status(r["job_id"]) for r in rows]


def _reconcile_inflight(job_id: str) -> int:
    """Conservatively skip crash-time in-flight targets to prevent duplicates."""
    now = time.time()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT idx FROM tg_multi_recipients WHERE job_id=? AND state='inflight'", (job_id,)).fetchall()
        if not rows:
            return 0
        conn.execute(
            "UPDATE tg_multi_recipients SET state='uncertain',last_error='process interrupted after claim',"
            "updated_at=? WHERE job_id=? AND state='inflight'", (now, job_id),)
        conn.execute(
            "UPDATE tg_multi_jobs SET uncertain_count=uncertain_count+?,last_error=?,updated_at=? WHERE job_id=?",
            (len(rows), "in-flight recipients skipped after restart to avoid duplicate delivery", now, job_id),)
    _advance_index(job_id)
    return len(rows)


def _advance_index(job_id: str) -> int:
    now = time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT MIN(idx) AS idx FROM tg_multi_recipients WHERE job_id=? AND state='pending'",
            (job_id,),).fetchone()
        if row and row["idx"] is not None:
            index = int(row["idx"])
        else:
            total = conn.execute("SELECT total FROM tg_multi_jobs WHERE job_id=?", (job_id,)).fetchone()
            index = int(total["total"] if total else 0)
        conn.execute("UPDATE tg_multi_jobs SET recipient_index=?,updated_at=? WHERE job_id=?",
                     (index, now, job_id))
    return index


async def start(job_id: str) -> dict:
    """Start once; repeated concurrent calls return the same in-process task."""
    _init()
    job_id = str(job_id)
    with _TASKS_LOCK:
        current = _TASKS.get(job_id)
        if current and not current.done():
            return status(job_id)
        row = _job(job_id)
        if not row:
            raise KeyError(f"unknown job: {job_id}")
        if row["state"] == "completed":
            return status(job_id)
        _reconcile_inflight(job_id)
        now = time.time()
        with _connect() as conn:
            conn.execute(
                "UPDATE tg_multi_jobs SET state='running',stop_requested=0,"
                "started_at=COALESCE(started_at,?),finished_at=NULL,updated_at=? WHERE job_id=?",
                (now, now, job_id),)
        task = asyncio.create_task(_run(job_id), name=f"telegram-multi-send:{job_id}")
        _TASKS[job_id] = task

        def _drop(done: asyncio.Task, key: str = job_id) -> None:
            with _TASKS_LOCK:
                if _TASKS.get(key) is done:
                    _TASKS.pop(key, None)

        task.add_done_callback(_drop)
    await asyncio.sleep(0)
    return status(job_id)


async def stop(job_id: str, grace: float = 2.0) -> dict:
    """Request a checkpointed stop and return promptly even during a slow send."""
    row = _job(job_id)
    if not row:
        raise KeyError(f"unknown job: {job_id}")
    if row["state"] in _TERMINAL_STATES or row["state"] == "paused":
        return status(job_id)
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "UPDATE tg_multi_jobs SET stop_requested=1,state='stop_requested',updated_at=? WHERE job_id=?",
            (now, job_id),)
    with _TASKS_LOCK:
        task = _TASKS.get(job_id)
    if not task or task.done():
        with _connect() as conn:
            conn.execute("UPDATE tg_multi_jobs SET state='paused',updated_at=? WHERE job_id=?",
                         (time.time(), job_id))
    elif grace > 0:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=float(grace))
        except asyncio.TimeoutError:
            pass
    return status(job_id)


async def resume(job_id: str) -> dict:
    """Resume a paused/failed job from its first durable pending recipient."""
    return await start(job_id)


async def restore_pending() -> list[dict]:
    """Restore jobs that were active at process exit; manual pauses stay paused."""
    _init()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT job_id,state FROM tg_multi_jobs WHERE state IN "
            "('queued','running','waiting','stop_requested') ORDER BY created_at"
        ).fetchall()
    restored: list[dict] = []
    for row in rows:
        job_id = row["job_id"]
        with _TASKS_LOCK:
            live = _TASKS.get(job_id)
        if live and not live.done():
            restored.append(status(job_id))
            continue
        _reconcile_inflight(job_id)
        if row["state"] == "stop_requested":
            with _connect() as conn:
                conn.execute(
                    "UPDATE tg_multi_jobs SET state='paused',stop_requested=0,updated_at=? WHERE job_id=?",
                    (time.time(), job_id),)
            continue
        restored.append(await start(job_id))
    return restored


def _pick_account(job_id: str, current: str | None, *, rotate: bool = False) -> tuple[dict | None, float | None]:
    now = time.time()
    with _connect() as conn:
        accounts = [dict(r) for r in conn.execute(
            "SELECT * FROM tg_multi_accounts WHERE job_id=? AND enabled=1 ORDER BY ordinal",
            (job_id,),).fetchall()]
        first = conn.execute("SELECT first_account FROM tg_multi_jobs WHERE job_id=?", (job_id,)).fetchone()
    if not accounts:
        return None, None
    eligible = [a for a in accounts if float(a["cooldown_until"] or 0) <= now]
    if not eligible:
        return None, min(float(a["cooldown_until"]) for a in accounts)
    first_account = first["first_account"] if first else None
    if not first_account:
        chosen = random.choice(eligible)
        with _connect() as conn:
            conn.execute("UPDATE tg_multi_jobs SET first_account=?,current_account=?,updated_at=? WHERE job_id=?",
                         (chosen["phone"], chosen["phone"], now, job_id))
        return chosen, None
    if current:
        same = next((a for a in eligible if a["phone"] == current), None)
        if same and not rotate:
            return same, None
        ordered = accounts
        old_pos = next((i for i, a in enumerate(ordered) if a["phone"] == current), -1)
        for offset in range(1, len(ordered) + 1):
            candidate = ordered[(old_pos + offset) % len(ordered)]
            if any(e["phone"] == candidate["phone"] for e in eligible):
                return candidate, None
    return eligible[0], None


def _next_recipient(job_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tg_multi_recipients WHERE job_id=? AND state='pending' ORDER BY idx LIMIT 1",
            (job_id,),).fetchone()
    return dict(row) if row else None


def _claim(job_id: str, idx: int, phone: str) -> bool:
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tg_multi_recipients SET state='inflight',attempts=attempts+1,updated_at=? "
            "WHERE job_id=? AND idx=? AND state='pending'", (now, job_id, idx),)
        if cur.rowcount:
            conn.execute(
                "UPDATE tg_multi_jobs SET current_account=?,recipient_index=?,state='running',updated_at=? "
                "WHERE job_id=?", (phone, idx, now, job_id),)
        return bool(cur.rowcount)


def _record_attempt(job_id: str, idx: int, phone: str, outcome: str, detail: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tg_multi_attempts(job_id,recipient_idx,phone,outcome,detail,attempted_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(job_id,recipient_idx,phone) DO UPDATE SET "
            "outcome=excluded.outcome,detail=excluded.detail,attempted_at=excluded.attempted_at",
            (job_id, idx, phone, outcome, detail[:240], time.time()),)


def _recipient_exhausted(job_id: str, idx: int) -> bool:
    """True when every still-enabled account already failed this recipient."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM tg_multi_accounts AS a "
            "WHERE a.job_id=? AND a.enabled=1 AND NOT EXISTS ("
            "SELECT 1 FROM tg_multi_attempts AS t WHERE t.job_id=a.job_id "
            "AND t.recipient_idx=? AND t.phone=a.phone AND t.outcome='error')",
            (job_id, idx),).fetchone()
    return int(row["n"] or 0) == 0


def _flood_seconds(exc: BaseException) -> int | None:
    if type(exc).__name__ not in {"FloodWaitError", "FloodWait"}:
        return None
    value = getattr(exc, "seconds", getattr(exc, "value", 1))
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _auth_failure(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}:{exc}".lower()
    return any(token in text for token in (
        "unauthorized", "no_session", "authkey", "sessionrevoked",
        "session revoked", "userdeactivated", "auth key",
    ))


async def _send(phone: str, target: Any, content: dict) -> None:
    """Send once with no internal FloodWait sleep so the fleet can fail over.

    Client acquisition and retry policy stay inside telegram_client APIs. The
    returned warm client is only used for the actual Telegram call; no client
    or session is constructed or managed here.
    """
    client = await tg.get_client(phone)
    file_path = content.get("file_path", "")
    caption = content.get("caption", "") or content.get("text", "")
    if file_path:
        call = tg.safe_call(
            lambda: client.send_file(target, file_path, caption=caption or None),
            retries=0,
        )
    else:
        call = tg.safe_call(
            lambda: client.send_message(target, content.get("text", "")),
            retries=0,
        )
    await asyncio.wait_for(call, timeout=float(content.get("send_timeout", 120) or 120))


async def _interruptible_sleep(job_id: str, seconds: float) -> bool:
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        row = _job(job_id)
        if not row or row["stop_requested"]:
            return False
        await asyncio.sleep(min(0.25, end - time.monotonic()))
    return True


def _pause(job_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE tg_multi_jobs SET state='paused',stop_requested=0,current_account=NULL,updated_at=? "
            "WHERE job_id=?", (time.time(), job_id),)


def _finish(job_id: str, state: str, error: str = "") -> None:
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "UPDATE tg_multi_jobs SET state=?,stop_requested=0,current_account=NULL,last_error=?,"
            "finished_at=?,updated_at=? WHERE job_id=?",
            (state, error[:240], now, now, job_id),)


async def _run(job_id: str) -> None:
    try:
        current: str | None = _job(job_id).get("current_account")
        rotate = False
        while True:
            job = _job(job_id)
            if not job:
                return
            if job["stop_requested"]:
                _pause(job_id)
                return
            recipient = _next_recipient(job_id)
            if not recipient:
                _finish(job_id, "completed")
                return

            account, wake_at = _pick_account(job_id, current, rotate=rotate)
            rotate = False
            if account is None:
                if wake_at is None:
                    _finish(job_id, "failed", "no enabled account remains")
                    return
                with _connect() as conn:
                    conn.execute("UPDATE tg_multi_jobs SET state='waiting',current_account=NULL,updated_at=? WHERE job_id=?",
                                 (time.time(), job_id))
                if not await _interruptible_sleep(job_id, max(0.05, min(1.0, wake_at - time.time()))):
                    _pause(job_id)
                    return
                current = None
                continue

            phone = account["phone"]
            current = phone
            if not _claim(job_id, int(recipient["idx"]), phone):
                continue
            target = json.loads(recipient["target_json"])
            content = json.loads(job["content_json"])
            try:
                await _send(phone, target, content)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # fail over; never wait on a broken account
                detail = f"{type(exc).__name__}: {str(exc)[:180]}"
                flood = _flood_seconds(exc)
                now = time.time()
                if flood is not None:
                    cooldown = now + flood
                    _record_attempt(job_id, int(recipient["idx"]), phone, "flood", detail)
                    with _connect() as conn:
                        conn.execute(
                            "UPDATE tg_multi_accounts SET cooldown_until=?,failure_count=failure_count+1,last_error=? "
                            "WHERE job_id=? AND phone=?", (cooldown, detail, job_id, phone),)
                        conn.execute(
                            "UPDATE tg_multi_recipients SET state='pending',last_error=?,updated_at=? "
                            "WHERE job_id=? AND idx=?", (detail, now, job_id, recipient["idx"]),)
                    current = phone
                    rotate = True
                    continue

                _record_attempt(job_id, int(recipient["idx"]), phone, "error", detail)
                disable = 1 if _auth_failure(exc) else 0
                with _connect() as conn:
                    conn.execute(
                        "UPDATE tg_multi_accounts SET enabled=CASE WHEN ?=1 THEN 0 ELSE enabled END,"
                        "failure_count=failure_count+1,last_error=? WHERE job_id=? AND phone=?",
                        (disable, detail, job_id, phone),)
                    conn.execute(
                        "UPDATE tg_multi_recipients SET state='pending',last_error=?,updated_at=? "
                        "WHERE job_id=? AND idx=?", (detail, now, job_id, recipient["idx"]),)
                    conn.execute("UPDATE tg_multi_jobs SET last_error=?,updated_at=? WHERE job_id=?",
                                 (detail, now, job_id),)
                if _recipient_exhausted(job_id, int(recipient["idx"])):
                    with _connect() as conn:
                        conn.execute(
                            "UPDATE tg_multi_recipients SET state='failed',updated_at=? WHERE job_id=? AND idx=?",
                            (now, job_id, recipient["idx"]),)
                        conn.execute(
                            "UPDATE tg_multi_jobs SET failed_count=failed_count+1,updated_at=? WHERE job_id=?",
                            (now, job_id),)
                    _advance_index(job_id)
                current = phone
                rotate = True
                continue

            now = time.time()
            _record_attempt(job_id, int(recipient["idx"]), phone, "sent", "")
            with _connect() as conn:
                conn.execute(
                    "UPDATE tg_multi_recipients SET state='sent',sent_by=?,last_error='',updated_at=? "
                    "WHERE job_id=? AND idx=? AND state='inflight'",
                    (phone, now, job_id, recipient["idx"]),)
                conn.execute(
                    "UPDATE tg_multi_accounts SET sent_count=sent_count+1,last_error='' "
                    "WHERE job_id=? AND phone=?", (job_id, phone),)
                conn.execute(
                    "UPDATE tg_multi_jobs SET sent_count=sent_count+1,last_error='',updated_at=? WHERE job_id=?",
                    (now, job_id),)
            try:
                db.tg_incr_sent(phone, 1)
            except Exception:
                pass
            _advance_index(job_id)
            if not await _interruptible_sleep(job_id, float(job.get("delay") or 0)):
                _pause(job_id)
                return
    except asyncio.CancelledError:
        # Leave the claimed row in-flight. restore_pending() will conservatively
        # classify it as uncertain instead of risking a duplicate send.
        raise
    except Exception as exc:  # keep the checkpoint resumable after unexpected bugs
        detail = f"orchestrator: {type(exc).__name__}: {str(exc)[:180]}"
        with _connect() as conn:
            conn.execute(
                "UPDATE tg_multi_jobs SET state='failed',last_error=?,finished_at=?,updated_at=? WHERE job_id=?",
                (detail, time.time(), time.time(), job_id),)
