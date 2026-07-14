"""
portal.panel — Owner control panel for the portal (isolated & additive).
Registers its own callback/command/message handlers on the EXISTING bot client
(bot.bot). It never edits a base handler. Uses db.get_setting/set_setting +
crypto_util (Fernet) for the Cloudflare token. Domain setup uses a private
conversation step namespace ("portal_await_*") so the base message_router
(which only knows its own steps) simply ignores it.
"""
from __future__ import annotations
import contextlib

import config
import db

try:
    import crypto_util
    _HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    _HAVE_CRYPTO = False

_registered = False


def _enabled() -> bool:
    v = db.get_setting("portal_enabled", None)
    if v is None:
        return bool(getattr(config, "PORTAL_ENABLED", True))
    return str(v) not in ("0", "false", "False", "")


def _mode() -> str:
    return (db.get_setting("portal_mode", None) or getattr(config, "PORTAL_MODE", "quick")).strip()


def _count() -> int:
    return db.get_int_setting("portal_added_count", 0)


def _link() -> str:
    return db.get_setting("portal_tunnel_url", "") or "—"


def _domain() -> str:
    return db.get_setting("portal_domain", "") or "—"


def _panel_text() -> str:
    return "\n".join([
        "🌐 پنل پورتال",
        "─────────────────────",
        f"وضعیت  : {'🟢 روشن' if _enabled() else '🔴 خاموش'}",
        f"مد      : {'🌍 دامنه' if _mode() == 'domain' else '🔗 Quick Tunnel'}",
        f"لینک    : {_link()}",
        f"دامنه   : {_domain()}",
        f"اکانت‌ها : {_count()} اکانت از طریق پورتال",
    ])


def _buttons(bot):
    B = bot.Button
    en = _enabled()
    return [
        [B.inline("🔴 خاموش‌کردن" if en else "🟢 روشن‌کردن", b"portal_toggle")],
        [B.inline("🔗 Quick Tunnel", b"portal_mode_quick"),
         B.inline("🌍 مد دامنه", b"portal_mode_domain")],
        [B.inline("🔄 ری‌استارت تونل", b"portal_restart"),
         B.inline("📊 بروزرسانی", b"portal_panel")],
        [B.inline("‹ بازگشت", b"home")],
    ]


def _restart_portal():
    with contextlib.suppress(Exception):
        from . import app
        app.request_restart()


def register(bot):
    """Attach panel handlers to the existing client (idempotent)."""
    global _registered
    if _registered:
        return
    _registered = True
    events = bot.events
    client = bot.bot

    @client.on(events.CallbackQuery(data=b"portal_panel"))
    async def _open(event):
        if not bot.is_owner(event):
            return
        await bot.safe_edit(event, _panel_text(), buttons=_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_toggle"))
    async def _toggle(event):
        if not bot.is_owner(event):
            return
        db.set_setting("portal_enabled", "0" if _enabled() else "1")
        _restart_portal()
        await bot.safe_edit(event, _panel_text(), buttons=_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_mode_quick"))
    async def _mode_quick(event):
        if not bot.is_owner(event):
            return
        db.set_setting("portal_mode", "quick")
        _restart_portal()
        await bot.safe_edit(event, "✅ مد روی Quick Tunnel تنظیم شد.\n\n" + _panel_text(),
                            buttons=_buttons(bot))

    @client.on(events.CallbackQuery(data=b"portal_mode_domain"))
    async def _mode_domain(event):
        if not bot.is_owner(event):
            return
        if not (_HAVE_CRYPTO and crypto_util.is_configured()):
            await bot.safe_edit(event,
                "⚠️ برای مد دامنه باید WORKER_SECRET در .env تنظیم باشد.\n\n" + _panel_text(),
                buttons=_buttons(bot))
            return
        bot.state[event.sender_id] = {"step": "portal_await_domain"}
        await bot.safe_edit(event,
            "🌍 دامنه‌ات را بفرست (مثال: `portal.example.com`):")

    @client.on(events.CallbackQuery(data=b"portal_restart"))
    async def _restart(event):
        if not bot.is_owner(event):
            return
        _restart_portal()
        await bot.safe_edit(event, "🔄 دستور ری‌استارت تونل صادر شد.\n\n" + _panel_text(),
                            buttons=_buttons(bot))

    @client.on(events.NewMessage(pattern=r"^/portal$"))
    async def _cmd(event):
        if not bot.is_owner(event):
            return
        await event.respond(_panel_text(), buttons=_buttons(bot))

    # Isolated domain-setup conversation. Runs alongside the base message_router;
    # only acts on our own "portal_await_*" steps, ignores everything else.
    @client.on(events.NewMessage)
    async def _domain_input(event):
        if not bot.is_owner(event):
            return
        st = bot.state.get(event.sender_id)
        if not st:
            return
        step = st.get("step")
        if step == "portal_await_domain":
            dom = (event.raw_text or "").strip().lower()
            if "." not in dom or " " in dom:
                await event.respond("✗ دامنه نامعتبره. دوباره بفرست:")
                return
            bot.state[event.sender_id] = {"step": "portal_await_token", "domain": dom}
            await event.respond("🔑 حالا توکن Cloudflare (API Token) را بفرست:")
        elif step == "portal_await_token":
            token = (event.raw_text or "").strip()
            dom = st.get("domain", "")
            if not token:
                await event.respond("✗ توکن خالیه. دوباره بفرست:")
                return
            db.set_setting("portal_domain", dom)
            with contextlib.suppress(Exception):
                db.set_setting("cf_token_enc", crypto_util.encrypt(token))
            db.set_setting("portal_mode", "domain")
            bot.state.pop(event.sender_id, None)
            _restart_portal()
            await event.respond(
                "✅ مد دامنه تنظیم شد؛ در حال بالا آوردن تونل...\n\n" + _panel_text(),
                buttons=_buttons(bot))
