"""Narrow portal observer: expired attempts and confirmed-invalid accounts only."""
from __future__ import annotations

import asyncio
import contextlib
import glob
import os
import sqlite3

import account_conn
import db
import rubika_client as rb

from . import stats


async def _stop_account_jobs(bot, account: dict) -> bool:
    account_id = int(account["id"])
    getattr(bot, "stop_flags", {})[account_id] = True
    for name in ("stop_automation", "stop_secretary", "stop_channelreport", "stop_reply"):
        fn = getattr(bot, name, None)
        if fn:
            with contextlib.suppress(Exception):
                await fn(account)
    for _ in range(30):
        if account_id not in getattr(bot, "active_jobs", set()):
            return True
        await asyncio.sleep(1)
    return account_id not in getattr(bot, "active_jobs", set())


async def _remove_confirmed_invalid(bot, account: dict) -> bool:
    phone = account["phone"]
    account_id = int(account["id"])
    if not account_conn.is_invalid(phone):
        return False
    try:
        if not await account_conn.verify_session_dead(phone):
            return False
    except Exception:
        return False  # network/FloodWait/temporary failures never delete
    if not await _stop_account_jobs(bot, account):
        return False
    # Re-check after jobs stopped; a temporary recovery must cancel deletion.
    if not account_conn.is_invalid(phone):
        return False
    try:
        if not await account_conn.verify_session_dead(phone):
            return False
    except Exception:
        return False
    if account_id in getattr(bot, "active_jobs", set()):
        return False
    await account_conn.close(phone)
    if account_id in getattr(bot, "active_jobs", set()):
        return False
    db.delete_account(account_id)
    # db.delete_account handles core automation tables. Portal-owned and other
    # additive ledgers are cleaned here without widening db.py.
    with contextlib.suppress(Exception):
        conn = sqlite3.connect(db.DB_PATH, timeout=15)
        try:
            for table in (
                "paused_sends", "cleanup_candidates", "broadcaster_accounts",
                "linkdooni_accounts", "portal_send_jobs",
            ):
                with contextlib.suppress(sqlite3.Error):
                    conn.execute(f"DELETE FROM {table} WHERE account_id=?", (account_id,))
            conn.commit()
        finally:
            conn.close()
    for path in glob.glob(rb.session_path(phone) + "*"):
        with contextlib.suppress(OSError):
            if os.path.isfile(path):
                os.remove(path)
    with contextlib.suppress(Exception):
        await bot.log(bot.card("🧹 - #Watcher_Account_Removed", [
            f"📱 {phone}", "🔐 دلیل: invalid session قطعی و تأییدشده",
            "✅ job/connection/account/session cleanup شد",
        ]))
    return True


async def run(bot, cleanup_expired, interval: float = 1.0) -> None:
    stats.init()
    stats.expire_stale()
    account_tick = 0
    while True:
        with contextlib.suppress(Exception):
            await cleanup_expired()
        account_tick += 1
        if account_tick >= 60:
            account_tick = 0
            for account in db.list_accounts():
                with contextlib.suppress(Exception):
                    await _remove_confirmed_invalid(bot, account)
        await asyncio.sleep(interval)
