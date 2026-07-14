"""
portal.net — cloudflared binary management + tunnel modes (Quick + Domain).
Additive & isolated: pure networking/process helpers. No base file is touched.
httpx (already a project dependency) is used lazily.
"""
from __future__ import annotations

import asyncio
import os
import re
import stat
import time
import base64
import secrets
import contextlib

import config

try:
    import httpx
    _HAVE_HTTPX = True
except ImportError:  # pragma: no cover
    _HAVE_HTTPX = False

CF_API = "https://api.cloudflare.com/client/v4"
CF_BIN_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
              "download/cloudflared-linux-amd64")
QUICK_RE = r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com"
TUNNEL_NAME = "rubika-portal"

_DATA_DIR = getattr(config, "DATA_DIR", None) or "data"


class TunnelError(Exception):
    """Stage-labeled tunnel error so the portal can post a precise card.
    stage in {cloudflared, tunnel, token, domain, cloudflare-api, verify}."""
    def __init__(self, stage: str, detail: str):
        self.stage = stage
        self.detail = detail
        super().__init__(f"{stage}: {detail}")


# --------------------------------------------------------------------------- #
# cloudflared binary: PATH -> data/cloudflared -> download static binary
# --------------------------------------------------------------------------- #
def _cf_bin_path() -> str:
    return os.path.join(_DATA_DIR, "cloudflared")


async def ensure_cloudflared() -> str:
    """Return a runnable cloudflared path or raise TunnelError('cloudflared', ...)."""
    import shutil
    p = shutil.which("cloudflared")
    if p:
        return p
    local = _cf_bin_path()
    if os.path.exists(local) and os.access(local, os.X_OK):
        return local
    if not _HAVE_HTTPX:
        raise TunnelError("cloudflared", "cloudflared نصب نیست و httpx هم برای دانلود نیست")
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        async with httpx.AsyncClient(follow_redirects=True, timeout=180) as c:
            r = await c.get(CF_BIN_URL)
            r.raise_for_status()
            with open(local, "wb") as f:
                f.write(r.content)
        os.chmod(local, os.stat(local).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return local
    except Exception as e:
        raise TunnelError("cloudflared", f"دانلود cloudflared نشد: {repr(e)[:120]}")


async def _spawn(cmd):
    return await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)


async def _read_url(proc, pattern, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        raw = await proc.stdout.readline()
        if not raw:
            break
        m = re.search(pattern, raw.decode(errors="ignore"))
        if m:
            return m.group(0)
    return None


# --------------------------------------------------------------------------- #
# Mode 1 — Quick Tunnel (temporary trycloudflare.com link)
# --------------------------------------------------------------------------- #
async def start_quick(port: int):
    """Returns (proc, url). Raises TunnelError on failure."""
    cf = await ensure_cloudflared()
    proc = await _spawn([cf, "tunnel", "--url", f"http://127.0.0.1:{port}"])
    url = await _read_url(proc, QUICK_RE, 30)
    if not url:
        with contextlib.suppress(Exception):
            proc.kill()
        raise TunnelError("tunnel", "لینک تونل دریافت نشد")
    return proc, url


# --------------------------------------------------------------------------- #
# Mode 2 — Domain Mode (permanent, your own domain via Cloudflare API)
# --------------------------------------------------------------------------- #
async def _cf_api(client, method, path, token, **kw):
    r = await client.request(
        method, CF_API + path,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"}, **kw)
    try:
        data = r.json()
    except Exception:
        raise TunnelError("cloudflare-api", f"{path} → پاسخ نامعتبر ({r.status_code})")
    if not data.get("success"):
        errs = data.get("errors") or r.status_code
        if path.endswith("/tokens/verify"):
            raise TunnelError("token", "توکن نامعتبر")
        raise TunnelError("cloudflare-api", f"{path} → {str(errs)[:120]}")
    return data.get("result")


async def start_domain(port: int, domain: str, api_token: str):
    """Provision a Named Tunnel + DNS for `domain`, run it, verify.
    Returns (proc, url). Raises TunnelError('<stage>', detail) with a precise stage."""
    if not _HAVE_HTTPX:
        raise TunnelError("domain", "httpx نصب نیست")
    if not api_token:
        raise TunnelError("token", "توکن Cloudflare تنظیم نشده")
    if not domain:
        raise TunnelError("domain", "دامنه تنظیم نشده")

    cf = await ensure_cloudflared()
    root = ".".join(domain.split(".")[-2:])

    async with httpx.AsyncClient(timeout=30) as client:
        await _cf_api(client, "GET", "/user/tokens/verify", api_token)      # token
        accts = await _cf_api(client, "GET", "/accounts", api_token)
        if not accts:
            raise TunnelError("cloudflare-api", "هیچ اکانتی برای این توکن نیست")
        acct = accts[0]["id"]

        zones = await _cf_api(client, "GET", f"/zones?name={root}", api_token)
        if not zones:
            raise TunnelError("domain", f"zone برای {root} یافت نشد")
        zone = zones[0]["id"]

        secret = base64.b64encode(secrets.token_bytes(32)).decode()
        tun = await _cf_api(client, "POST", f"/accounts/{acct}/cfd_tunnel", api_token,
                            json={"name": TUNNEL_NAME, "tunnel_secret": secret})
        tid = tun["id"]

        ctoken = await _cf_api(client, "GET",
                               f"/accounts/{acct}/cfd_tunnel/{tid}/token", api_token)

        await _cf_api(client, "PUT",
                      f"/accounts/{acct}/cfd_tunnel/{tid}/configurations", api_token,
                      json={"config": {"ingress": [
                          {"hostname": domain, "service": f"http://127.0.0.1:{port}"},
                          {"service": "http_status:404"}]}})

        target = f"{tid}.cfargotunnel.com"
        existing = await _cf_api(client, "GET",
                                 f"/zones/{zone}/dns_records?name={domain}", api_token)
        rec = {"type": "CNAME", "name": domain, "content": target, "proxied": True}
        if existing:
            await _cf_api(client, "PUT",
                          f"/zones/{zone}/dns_records/{existing[0]['id']}", api_token, json=rec)
        else:
            await _cf_api(client, "POST",
                          f"/zones/{zone}/dns_records", api_token, json=rec)

    proc = await _spawn([cf, "tunnel", "run", "--token", ctoken])
    url = f"https://{domain}"
    if not await verify_domain(url, timeout=90):
        with contextlib.suppress(Exception):
            proc.kill()
        raise TunnelError("verify", "دامنه active نشد")
    return proc, url


async def verify_domain(url: str, timeout=90) -> bool:
    """Poll <url>/ping until it answers 200 or timeout."""
    if not _HAVE_HTTPX:
        return False
    t0 = time.time()
    async with httpx.AsyncClient(timeout=10) as client:
        while time.time() - t0 < timeout:
            with contextlib.suppress(Exception):
                r = await client.get(url + "/ping")
                if r.status_code == 200:
                    return True
            await asyncio.sleep(5)
    return False
