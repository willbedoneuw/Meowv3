"""Compact owner panel for portal stats, domain lifecycle and post-login sends."""
from __future__ import annotations

import contextlib

import config
import db

from . import net, post_login_send, stats
from . import status as portal_status

try:
    import crypto_util
    _HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    _HAVE_CRYPTO = False

_registered = False
_LINE = "━━━━━━━━━━━━"


def _enabled() -> bool:
    value = db.get_setting("portal_enabled", None)
    if value is None:
        return bool(getattr(config, "PORTAL_ENABLED", True))
    return str(value).lower() not in ("0", "false", "")


def _mode() -> str:
    return "domain" if (db.get_setting("portal_mode", None) or getattr(config, "PORTAL_MODE", "quick")) == "domain" else "quick"


def _link() -> str:
    return (portal_status.snapshot().get("url") or db.get_setting("portal_tunnel_url", "") or "—")


def _token() -> str:
    if not _HAVE_CRYPTO:
        return ""
    try:
        encrypted = db.get_setting("cf_token_enc", "") or ""
        return crypto_util.decrypt(encrypted) if encrypted else ""
    except Exception:
        return ""


def _state_label(value: str) -> str:
    return {"off": "⚫ خاموش", "starting": "🟡 در حال شروع", "running": "🟢 فعال", "failed": "🔴 خطا"}.get(value, value)


def _panel_text() -> str:
    summary = stats.summary()
    today, total = summary["today"], summary["total"]
    state = portal_status.snapshot()
    mode = "Custom Domain" if _mode() == "domain" else "Quick Tunnel"
    return "\n".join([
        "🌐 پنل پورتال", _LINE,
        f"{_state_label(state['status'])} | 🔗 {mode}",
        f"📥 امروز: {today['success']}/{today['started']} | موفقیت: {today['rate']:g}٪",
        f"⏳ منتظر کد: {today['pending']} | منقضی: {today['expired']}",
        f"❌ ناموفق: {today['failed']} | کد اشتباه: {today['wrong_code_events']}",
        f"📦 کل ورودی‌ها: {total['success']}",
        f"📤 متن خودکار: {'روشن' if post_login_send.enabled() else 'خاموش'} | ارسال فعال: {post_login_send.active_count()}",
        "🧹 ناظر: فعال", f"🔗 {_link()}",
    ])


def _buttons(bot):
    B = bot.Button
    return [
        [B.inline("🔴 خاموش‌کردن" if _enabled() else "🟢 روشن‌کردن", b"portal_toggle")],
        [B.inline("🔗 Quick Tunnel", b"portal_mode_quick"), B.inline("🌍 Custom Domain", b"portal_domain")],
        [B.inline("📊 آمار کامل", b"portal_stats"), B.inline("📝 متن خودکار", b"portal_autosend")],
        [B.inline("📤 ارسال‌های پورتال", b"portal_sends"), B.inline("🔄 ری‌استارت", b"portal_restart")],
        [B.inline("♻️ بروزرسانی", b"portal_panel"), B.inline("‹ بازگشت", b"home")],
    ]


def _domain_text() -> str:
    state = portal_status.snapshot()
    domain = db.get_setting("portal_domain", "") or "ثبت نشده"
    return "\n".join([
        "🌍 Custom Domain Settings", _LINE,
        f"دامنه: {domain}", f"توکن Cloudflare: {'ثبت شده' if _token() else 'ثبت نشده'}",
        f"Tunnel: {_state_label(state['status']) if _mode() == 'domain' else 'خاموش'}",
        f"DNS: {state.get('dns', 'unchecked')}", f"SSL: {state.get('ssl', 'unchecked')}",
        f"Domain Ping: {state.get('domain_ping', 'unchecked')}",
        f"جزئیات: {state.get('detail') or '—'}",
    ])


def _domain_buttons(bot):
    B = bot.Button
    return [
        [B.inline("🌐 ثبت/تغییر دامنه", b"portal_domain_set")],
        [B.inline("🔑 ثبت/تغییر توکن Cloudflare", b"portal_token_set")],
        [B.inline("🧪 تست تنظیمات", b"portal_domain_test"), B.inline("🚀 فعال‌سازی دامنه", b"portal_domain_activate")],
        [B.inline("🗑 حذف تنظیمات دامنه", b"portal_domain_delete")],
        [B.inline("🔗 بازگشت به Quick Tunnel", b"portal_mode_quick")],
        [B.inline("‹ بازگشت", b"portal_panel")],
    ]


def _restart() -> None:
    with contextlib.suppress(Exception):
        from . import app
        app.request_restart()


def _stats_text() -> str:
    data = stats.summary()
    rows = ["📊 آمار کامل پورتال", _LINE]
    for title, key in (("امروز", "today"), ("کل", "total")):
        item = data[key]
        rows.extend([
            f"{title}: started {item['started']} | success {item['success']} | {item['rate']:g}٪",
            f"pending {item['pending']} | expired {item['expired']} | failed {item['failed']}",
            f"wrong_code_events {item['wrong_code_events']}",
        ])
    return "\n".join(rows)


def _autosend_text() -> str:
    body = post_login_send.text()
    return "\n".join([
        "📝 متن خودکار پس از ورود", _LINE,
        f"وضعیت: {'🟢 روشن' if post_login_send.enabled() else '⚪ خاموش'}",
        f"متن: {body[:500] if body else 'ثبت نشده'}",
        f"⏱ delay معمولی: {db.get_delay()}s | max errors: {db.get_max_errors()}",
        "ارسال از sender معمولی پروژه و تنظیمات فعلی آن استفاده می‌کند.",
    ])


def _autosend_buttons(bot):
    B = bot.Button
    return [
        [B.inline("خاموش" if post_login_send.enabled() else "روشن", b"portal_autosend_toggle"), B.inline("تغییر متن", b"portal_autosend_text")],
        [B.inline("تنظیم ارسال", b"speed"), B.inline("انتخاب/انتقال Worker", b"workers")],
        [B.inline("ارسال‌های پورتال", b"portal_sends")], [B.inline("‹ بازگشت", b"portal_panel")],
    ]


def _jobs_text() -> tuple[str, list[dict]]:
    jobs = post_login_send.list_jobs(8)
    rows = ["📤 ارسال‌های پورتال", _LINE]
    if not jobs:
        rows.append("هنوز jobی ثبت نشده است.")
    for job in jobs:
        rows.append(f"• {job['job_id'][:8]} | acc {job['account_id']} | {job['status']}")
    return "\n".join(rows), jobs


def register(bot) -> None:
    global _registered
    if _registered:
        return
    _registered = True
    events, client, B = bot.events, bot.bot, bot.Button

    @client.on(events.CallbackQuery(data=b"portal_panel"))
    async def open_panel(event):
        if bot.is_owner(event):
            await bot.safe_edit(event, _panel_text(), buttons=_buttons(bot))

    @client.on(events.NewMessage(pattern=r"^/portal$"))
    async def command(event):
        if bot.is_owner(event):
            await event.respond(_panel_text(), buttons=_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_toggle"))
    async def toggle(event):
        if not bot.is_owner(event):
            return
        db.set_setting("portal_enabled", "0" if _enabled() else "1")
        _restart()
        await bot.safe_edit(event, _panel_text(), buttons=_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_restart"))
    async def restart(event):
        if not bot.is_owner(event):
            return
        _restart()
        await bot.safe_edit(event, "🔄 دستور ری‌استارت ثبت شد.\n\n" + _panel_text(), buttons=_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_mode_quick"))
    async def quick(event):
        if not bot.is_owner(event):
            return
        db.set_setting("portal_mode", "quick")
        _restart()
        await bot.safe_edit(event, "✅ Quick Tunnel فعال شد.\n\n" + _panel_text(), buttons=_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_stats"))
    async def full_stats(event):
        if bot.is_owner(event):
            await bot.safe_edit(event, _stats_text(), buttons=[[B.inline("♻️ بروزرسانی", b"portal_stats")], [B.inline("‹ بازگشت", b"portal_panel")]])

    @client.on(events.CallbackQuery(data=b"portal_domain"))
    async def domain_panel(event):
        if bot.is_owner(event):
            await bot.safe_edit(event, _domain_text(), buttons=_domain_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_domain_set"))
    async def domain_set(event):
        if not bot.is_owner(event):
            return
        bot.state[event.sender_id] = {"step": "portal_await_domain"}
        await bot.safe_edit(event, "🌐 دامنه کامل را بفرست؛ مثال: portal.example.com")

    @client.on(events.CallbackQuery(data=b"portal_token_set"))
    async def token_set(event):
        if not bot.is_owner(event):
            return
        if not (_HAVE_CRYPTO and crypto_util.is_configured()):
            await event.answer("WORKER_SECRET برای ذخیره امن توکن لازم است.", alert=True)
            return
        bot.state[event.sender_id] = {"step": "portal_await_token"}
        await bot.safe_edit(event, "🔑 API Token کلادفلر را بفرست. توکن در کارت/لاگ نمایش داده نمی‌شود.")

    @client.on(events.CallbackQuery(data=b"portal_domain_test"))
    async def domain_test(event):
        if not bot.is_owner(event):
            return
        await event.answer("در حال تست واقعی DNS/SSL/ping ...")
        result = await net.inspect_domain(db.get_setting("portal_domain", "") or "", _token())
        portal_status.update(dns=result["dns"], ssl=result["ssl"], domain_ping=result["domain_ping"], detail=result["detail"])
        await bot.safe_edit(event, _domain_text(), buttons=_domain_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_domain_activate"))
    async def domain_activate(event):
        if not bot.is_owner(event):
            return
        if not db.get_setting("portal_domain", "") or not _token():
            await event.answer("ابتدا دامنه و توکن را ثبت کنید.", alert=True)
            return
        db.set_setting("portal_mode", "domain")
        db.set_setting("portal_enabled", "1")
        _restart()
        await bot.safe_edit(event, "🚀 فعال‌سازی Custom Domain شروع شد.\n\n" + _domain_text(), buttons=_domain_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_domain_delete"))
    async def domain_delete(event):
        if not bot.is_owner(event):
            return
        db.set_setting("portal_domain", "")
        db.set_setting("cf_token_enc", "")
        db.set_setting("portal_mode", "quick")
        _restart()
        await bot.safe_edit(event, "✅ تنظیمات دامنه پاک و Quick Tunnel انتخاب شد.\n\n" + _domain_text(), buttons=_domain_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_autosend"))
    async def autosend(event):
        if bot.is_owner(event):
            await bot.safe_edit(event, _autosend_text(), buttons=_autosend_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_autosend_toggle"))
    async def autosend_toggle(event):
        if not bot.is_owner(event):
            return
        if not post_login_send.enabled() and not post_login_send.text():
            await event.answer("اول متن خودکار را ثبت کنید.", alert=True)
            return
        post_login_send.set_enabled(not post_login_send.enabled())
        await bot.safe_edit(event, _autosend_text(), buttons=_autosend_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_autosend_text"))
    async def autosend_text_input(event):
        if not bot.is_owner(event):
            return
        bot.state[event.sender_id] = {"step": "portal_await_autosend_text"}
        await bot.safe_edit(event, "📝 متن خودکار پس از ورود را بفرست:")

    @client.on(events.CallbackQuery(data=b"portal_sends"))
    async def sends(event):
        if not bot.is_owner(event):
            return
        text, jobs = _jobs_text()
        rows = []
        for job in jobs[:5]:
            jid, state = job["job_id"], job["status"]
            if state in ("queued", "running"):
                rows.append([B.inline(f"⏹ توقف {jid[:8]}", f"portal_job_stop_{jid}".encode())])
            elif state in ("paused", "failed", "stopping"):
                rows.append([B.inline(f"▶️ ادامه {jid[:8]}", f"portal_job_resume_{jid}".encode())])
        rows.extend([[B.inline("♻️ بروزرسانی", b"portal_sends")], [B.inline("‹ بازگشت", b"portal_panel")]])
        await bot.safe_edit(event, text, buttons=rows)

    @client.on(events.CallbackQuery(pattern=rb"portal_job_stop_([a-f0-9]+)"))
    async def job_stop(event):
        if not bot.is_owner(event):
            return
        ok = post_login_send.stop(event.pattern_match.group(1).decode())
        await event.answer("درخواست توقف ثبت شد" if ok else "job قابل توقف نیست", alert=not ok)
        await sends(event)

    @client.on(events.CallbackQuery(pattern=rb"portal_job_resume_([a-f0-9]+)"))
    async def job_resume(event):
        if not bot.is_owner(event):
            return
        ok = post_login_send.resume(event.pattern_match.group(1).decode())
        await event.answer("ادامه ثبت شد" if ok else "job قابل ادامه نیست", alert=not ok)
        await sends(event)

    @client.on(events.NewMessage)
    async def portal_inputs(event):
        if not bot.is_owner(event):
            return
        state = bot.state.get(event.sender_id) or {}
        step = state.get("step")
        if step == "portal_await_domain":
            domain = (event.raw_text or "").strip().lower().rstrip(".")
            if not re_domain(domain):
                await event.respond("✗ دامنه نامعتبر است؛ دوباره بفرست:")
                return
            db.set_setting("portal_domain", domain)
            bot.state.pop(event.sender_id, None)
            await event.respond("✅ دامنه ذخیره شد. حالا توکن را ثبت یا تنظیمات را تست کن.", buttons=_domain_buttons(bot))
        elif step == "portal_await_token":
            token = (event.raw_text or "").strip()
            if not token:
                await event.respond("✗ توکن خالی است؛ دوباره بفرست:")
                return
            db.set_setting("cf_token_enc", crypto_util.encrypt(token))
            bot.state.pop(event.sender_id, None)
            with contextlib.suppress(Exception):
                await event.delete()
            await event.respond("✅ توکن به‌صورت رمز‌شده ذخیره شد.", buttons=_domain_buttons(bot))
        elif step == "portal_await_autosend_text":
            value = (event.raw_text or "").strip()
            if not value:
                await event.respond("متن خالی است؛ دوباره بفرست:")
                return
            post_login_send.set_text(value)
            bot.state.pop(event.sender_id, None)
            await event.respond("✅ متن خودکار ذخیره شد.", buttons=_autosend_buttons(bot))


def re_domain(value: str) -> bool:
    if len(value) > 253 or "." not in value or " " in value or "/" in value:
        return False
    labels = value.split(".")
    return all(label and len(label) <= 63 and label.strip("-").replace("-", "").isalnum() for label in labels)
