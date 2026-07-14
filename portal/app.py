"""
portal.app — web server + endpoints + tunnel lifecycle + login.
Additive & isolated: only existing project functions are called. Tunnel/mode
logic lives in portal.net; the owner panel lives in portal.panel.
"""
from __future__ import annotations
import asyncio, time, traceback, contextlib, re

import config, db, account_conn, worker
import rubika_client as rb

from . import net as portal_net
from .page import PAGE_HTML

try:
    import crypto_util
    _HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    _HAVE_CRYPTO = False

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
    _HAVE_WEB = True
except ImportError:  # pragma: no cover
    _HAVE_WEB = False

USER_ERR = "خطایی رخ داد، دوباره تلاش کن"
OK_PREFIX = ("091", "099", "090", "092", "093", "094")

_sessions: dict = {}     # normalized_phone -> {"ctx","exp","tries"}
_locks: dict = {}
_current: dict = {"tunnel": None, "server": None}
_control: dict = {"restart": False}
_LINE = "-------------------------------"


# ---- settings (db-first, config/env fallback) ----------------------------- #
def is_enabled() -> bool:
    v = db.get_setting("portal_enabled", None)
    if v is None:
        return bool(getattr(config, "PORTAL_ENABLED", True))
    return str(v) not in ("0", "false", "False", "")


def get_mode() -> str:
    return (db.get_setting("portal_mode", None) or getattr(config, "PORTAL_MODE", "quick")).strip()


def get_port() -> int:
    v = db.get_setting("portal_port", None)
    with contextlib.suppress(Exception):
        if v:
            return int(v)
    return int(getattr(config, "PORTAL_PORT", 8080))


def request_restart():
    """Called by the panel to restart the tunnel/server live."""
    _control["restart"] = True


# ---- detailed error cards ------------------------------------------------- #
async def _err(bot, stage, endpoint, phone, exc):
    try:
        tb = traceback.extract_tb(exc.__traceback__)
        last = tb[-1] if tb else None
        where = (f"{last.filename.split('/')[-1]}:{last.lineno} in {last.name}()"
                 if last else "-")
        await bot.log(bot.card("⚠️ - #Portal_Error", [
            "#portal #error", _LINE,
            f"🔧 مرحله  : {stage}",
            f"🔗 Endpoint: {endpoint}",
            f"📱 Phone  : {phone or '-'}",
            f"💥 Type   : {type(exc).__name__}",
            f"📝 Error  : {repr(exc)[:200]}",
            f"🧩 Where  : {where}",
            f"🕒 {config.now_str()}",
        ]))
    except Exception as e:
        print(f"[portal _err failed] {e}")


async def _tunnel_err_card(bot, stage, detail):
    with contextlib.suppress(Exception):
        await bot.log(bot.card("⚠️ - #Portal_Error", [
            "#portal #error", _LINE,
            f"🔧 مرحله  : tunnel/{stage}",
            f"📝 Error  : {detail}",
            f"🕒 {config.now_str()}",
        ]))


def _lock_for(phone):
    if phone not in _locks:
        _locks[phone] = asyncio.Lock()
    return _locks[phone]


async def _drop(phone):
    v = _sessions.pop(phone, None)
    if v and v.get("ctx", {}).get("client"):
        with contextlib.suppress(Exception):
            await v["ctx"]["client"].disconnect()


def _sweep():
    now = time.time()
    for p in [p for p, v in _sessions.items() if v["exp"] < now]:
        asyncio.create_task(_drop(p))


def _client_ok(p):
    return bool(re.match(r"^09\d{9}$", p)) and p.startswith(OK_PREFIX)


# ---- save account (same order as complete_account, no event) -------------- #
async def _save_account(bot, ctx):
    client = ctx["client"]; phone = ctx["phone"]
    me = await client.get_me()
    guid = rb._guid_of(me) or "-"; name = rb._name_of(me)
    ordered, stats = await rb.get_ordered_recipients(client)
    account_id = db.add_account(phone, name, str(guid), rb.session_path(phone))
    w = worker.ensure_master_worker() or {}
    if w.get("id"):
        db.set_account_worker(account_id, w["id"])
    with contextlib.suppress(Exception):
        sess = bot._session_values(client, phone, guid)
        if sess and sess.get("auth"):
            db.set_session_blob(account_id, sess)
            await bot._post_session_token(phone, name, sess)
    with contextlib.suppress(Exception):
        db.set_setting("portal_added_count",
                       db.get_int_setting("portal_added_count", 0) + 1)
    from telethon import Button
    await bot.bot.send_message(config.LOG_GROUP_ID, bot.card("🔐 - #Account_Added (Portal)", [
        f"📱 Phone : {phone}", f"• Name  : {name}", "• State : ✓ Login OK",
        f"📇 Contacts : {stats['contacts']} | 👥 Groups : {stats['groups']}",
        f"🕒 {config.now_str()}",
    ]), buttons=[[Button.inline("→ ارسال با این اکانت", f"acc_{account_id}".encode())]])
    return {"ok": True, "account_id": account_id}


# ---- web app + endpoints -------------------------------------------------- #
def _build_app(bot):
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def home():
        if not is_enabled():
            return HTMLResponse("<h3 style='font-family:sans-serif;text-align:center;"
                                "margin-top:40px'>سرویس موقتاً غیرفعال است.</h3>")
        return HTMLResponse(PAGE_HTML)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    @app.post("/api/start")
    async def api_start(req: Request):
        praw = ""
        try:
            if not is_enabled():
                return JSONResponse({"error": "سرویس موقتاً غیرفعال است"})
            b = await req.json(); praw = str(b.get("phone", "")).strip()
            if not _client_ok(praw):
                return JSONResponse({"error": "شماره خرابه"})
            phone = rb.normalize_phone(praw)
            if any(a["phone"] == phone for a in db.list_accounts()):
                return JSONResponse({"error": "این شماره قبلاً ثبت شده"})
            _sweep()
            if len(_sessions) >= config.MAX_PORTAL_LOGINS and phone not in _sessions:
                return JSONResponse({"error": "سرویس موقتاً شلوغه، کمی بعد امتحان کن"})
            async with _lock_for(phone):
                await account_conn.close(phone)
                ctx = await rb.start_login(phone)
                _sessions[phone] = {"ctx": ctx, "exp": time.time() + config.PORTAL_TTL, "tries": 0}
                st = str(ctx.get("status") or "").upper()
                if "PASS" in st:
                    return JSONResponse({"next": "password"})
                if not ctx.get("phone_code_hash"):
                    await _drop(phone)
                    return JSONResponse({"error": "روبیکا کد نفرستاد، دوباره تلاش کن"})
                return JSONResponse({"next": "code"})
        except Exception as e:
            await _err(bot, "start_login", "/api/start", praw, e)
            return JSONResponse({"error": USER_ERR})

    @app.post("/api/password")
    async def api_password(req: Request):
        praw = ""
        try:
            b = await req.json(); praw = str(b.get("phone", "")).strip()
            pwd = str(b.get("password", "")); phone = rb.normalize_phone(praw)
            async with _lock_for(phone):
                await account_conn.close(phone)
                ctx = await rb.start_login(phone, pass_key=pwd)
                _sessions[phone] = {"ctx": ctx, "exp": time.time() + config.PORTAL_TTL, "tries": 0}
                return JSONResponse({"next": "code"})
        except Exception as e:
            await _err(bot, "finish_login/password", "/api/password", praw, e)
            return JSONResponse({"error": USER_ERR})

    @app.post("/api/code")
    async def api_code(req: Request):
        praw = ""
        try:
            b = await req.json(); praw = str(b.get("phone", "")).strip()
            code = "".join(ch for ch in str(b.get("code", "")) if ch.isdigit())
            phone = rb.normalize_phone(praw); sess = _sessions.get(phone)
            if not sess:
                return JSONResponse({"error": "کد منقضی شده، دوباره شروع کن"})
            async with _lock_for(phone):
                try:
                    await rb.finish_login(sess["ctx"], code)
                except Exception:
                    sess["tries"] += 1
                    if sess["tries"] >= 3:
                        await _drop(phone)
                        return JSONResponse({"error": "کد اشتباه بود، دوباره شروع کن"})
                    return JSONResponse({"error": "کد خرابه"})
                try:
                    return JSONResponse(await _save_account(bot, sess["ctx"]))
                finally:
                    await _drop(phone)
        except Exception as e:
            await _err(bot, "add_account/session", "/api/code", praw, e)
            return JSONResponse({"error": USER_ERR})

    @app.post("/api/resend")
    async def api_resend(req: Request):
        praw = ""
        try:
            b = await req.json(); praw = str(b.get("phone", "")).strip()
            phone = rb.normalize_phone(praw)
            async with _lock_for(phone):
                await account_conn.close(phone)
                ctx = await rb.start_login(phone)
                _sessions[phone] = {"ctx": ctx, "exp": time.time() + config.PORTAL_TTL, "tries": 0}
            return JSONResponse({"ok": True})
        except Exception as e:
            await _err(bot, "resend", "/api/resend", praw, e)
            return JSONResponse({"error": USER_ERR})

    return app


# ---- tunnel lifecycle (single-instance) — Quick / Domain dispatch --------- #
async def _kill_tunnel():
    proc = _current.get("tunnel")
    if proc and getattr(proc, "returncode", None) is None:
        with contextlib.suppress(Exception):
            proc.kill()
    _current["tunnel"] = None


async def _start_tunnel(bot):
    await _kill_tunnel()
    mode = get_mode()
    port = get_port()
    try:
        if mode == "domain":
            domain = db.get_setting("portal_domain", "") or ""
            token = ""
            if _HAVE_CRYPTO:
                with contextlib.suppress(Exception):
                    enc = db.get_setting("cf_token_enc", "")
                    if enc:
                        token = crypto_util.decrypt(enc)
            proc, url = await portal_net.start_domain(port, domain, token)
        else:
            proc, url = await portal_net.start_quick(port)
        _current["tunnel"] = proc
        with contextlib.suppress(Exception):
            db.set_setting("portal_tunnel_url", url)
        await bot.log(bot.card("🌍 - Portal Online", [
            "#portal", _LINE,
            f"🔧 مد    : {mode}",
            f"🔗 {url}",
            f"🕒 {config.now_str()}"]))
    except portal_net.TunnelError as te:
        await _tunnel_err_card(bot, te.stage, te.detail)
    except Exception as e:
        await _err(bot, "tunnel", "_start_tunnel", None, e)


# ---- watchdog + live on/off/restart monitor ------------------------------- #
async def _monitor(server):
    while not getattr(server, "should_exit", False):
        if not is_enabled() or _control.get("restart"):
            _control["restart"] = False
            server.should_exit = True
            return
        await asyncio.sleep(3)


async def run_portal():
    """Entry point — called from amain via create_task. Fully isolated."""
    import bot
    with contextlib.suppress(Exception):
        from . import panel
        panel.register(bot)

    if not _HAVE_WEB:
        with contextlib.suppress(Exception):
            await bot.log(bot.card("⚠️ - #Portal_Error", [
                "#portal #error", _LINE, "🔧 مرحله  : web-server",
                "📝 Error  : fastapi/uvicorn در دسترس نیست", f"🕒 {config.now_str()}"]))
        return

    while True:
        try:
            if not is_enabled():
                await asyncio.sleep(10)
                continue
            app = _build_app(bot)
            server = uvicorn.Server(uvicorn.Config(
                app, host="127.0.0.1", port=get_port(), log_level="warning"))
            _current["server"] = server
            await _start_tunnel(bot)
            mon = asyncio.create_task(_monitor(server))
            try:
                await server.serve()
            finally:
                mon.cancel()
                await _kill_tunnel()
        except OSError as e:
            await _tunnel_err_card(bot, "web-server", f"پورت اشغال یا خطای شبکه: {repr(e)[:120]}")
        except Exception as e:
            await _err(bot, "run_portal", "serve", None, e)
        await asyncio.sleep(5)
