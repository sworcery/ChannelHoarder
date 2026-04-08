"""PO token server watchdog.

The bgutil PO token server has a known memory leak (~25MB per request)
and its BotGuard VM can hang after extended use. This watchdog periodically
tests actual token generation (not just /ping) and restarts the Node.js
process if it's stuck.
"""

import asyncio
import logging
import signal
import os

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Track the PO token server PID so we can restart it
_pot_pid: int | None = None


def set_pot_pid(pid: int):
    """Called from app startup to register the PO token server PID."""
    global _pot_pid
    _pot_pid = pid
    logger.info("PO token watchdog tracking PID %d", pid)


async def _restart_pot_server():
    """Kill and restart the PO token server Node.js process."""
    global _pot_pid

    if _pot_pid:
        logger.warning("Killing hung PO token server (PID %d)", _pot_pid)
        try:
            os.kill(_pot_pid, signal.SIGKILL)
        except OSError:
            pass
        await asyncio.sleep(2)

    # Start a new instance
    port = os.environ.get("POT_SERVER_PORT", "4416")
    pot_log = "/config/pot-server.log"
    server_dir = "/opt/pot-provider/server"

    if not os.path.isdir(f"{server_dir}/build"):
        logger.error("PO token server build directory not found, cannot restart")
        return

    env = os.environ.copy()
    env["HOME"] = "/home/appuser"
    env["NODE_OPTIONS"] = "--max-old-space-size=256"

    with open(pot_log, "w") as log_file:
        proc = await asyncio.create_subprocess_exec(
            "node", "build/main.js", "--port", port,
            cwd=server_dir,
            stdout=log_file,
            stderr=log_file,
            env=env,
        )
        _pot_pid = proc.pid
        logger.info("Restarted PO token server (new PID %d)", proc.pid)

    # Wait for it to be ready
    for i in range(30):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://127.0.0.1:{port}/ping", timeout=3)
                if resp.status_code == 200:
                    logger.info("PO token server ready after restart (%ds)", i + 1)
                    return
        except Exception:
            pass
        await asyncio.sleep(1)

    logger.error("PO token server failed to respond after restart")


async def check_pot_server():
    """Watchdog: test PO token generation and restart if hung.

    Runs every 5 minutes. Tests /get_pot with a 30-second timeout.
    If the server can't generate a token, it's restarted.
    """
    if not settings.POT_SERVER_ENABLED:
        return

    port = os.environ.get("POT_SERVER_PORT", "4416")

    # First check if /ping responds at all
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{port}/ping", timeout=5)
            if resp.status_code != 200:
                logger.warning("PO token server /ping returned %d, restarting", resp.status_code)
                await _restart_pot_server()
                return
    except Exception as e:
        logger.warning("PO token server /ping failed (%s), restarting", e)
        await _restart_pot_server()
        return

    # Test actual token generation with a timeout
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://127.0.0.1:{port}/get_pot",
                json={"client": "web", "visitor_data": ""},
                timeout=30,
            )
            if resp.status_code == 200:
                logger.debug("PO token server health check passed")
                return
            else:
                logger.warning("PO token /get_pot returned %d, restarting", resp.status_code)
    except httpx.TimeoutException:
        logger.warning("PO token /get_pot timed out after 30s - server is hung, restarting")
    except Exception as e:
        logger.warning("PO token /get_pot failed (%s), restarting", e)

    await _restart_pot_server()
