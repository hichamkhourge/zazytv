"""
Zazy Automation API
Exposes HTTP endpoints so the iptvProvider Laravel app can trigger
provider account generation on demand.

Endpoints:
  GET  /api/health                  -> liveness check
  POST /api/generate/zazy           -> run zazy_playlist_automation.py
  POST /api/generate/layerseven     -> run layerseven_automation.py

Protected by Bearer token: Authorization: Bearer <AUTOMATION_API_KEY>
"""

import os
import sys
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# Config
AUTOMATION_API_KEY = os.getenv("AUTOMATION_API_KEY", "")
APP_DIR = Path(__file__).parent.parent  # /app

# FastAPI app
app = FastAPI(
    title="Zazy Automation API",
    description="Triggers IPTV provider account generation scripts",
    version="1.0.0",
)

bearer_scheme = HTTPBearer()


def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    """Validate the Bearer token against AUTOMATION_API_KEY env var."""
    if not AUTOMATION_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTOMATION_API_KEY is not configured on this server",
        )
    if credentials.credentials != AUTOMATION_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


# Response models
class HealthResponse(BaseModel):
    status: str
    timestamp: str


class GenerateResponse(BaseModel):
    success: bool
    provider: str
    xtream_host: str | None = None
    xtream_username: str | None = None
    xtream_password: str | None = None
    m3u_url: str | None = None
    error: str | None = None
    logs: str | None = None


# Helpers
async def run_script(script_name: str, timeout: int = 600) -> tuple[bool, str]:
    """
    Run a Python script in /app as a subprocess.
    Returns (success, full_stdout_stderr output).
    Timeout default is 10 minutes, enough for Selenium + 2captcha.
    """
    script_path = APP_DIR / script_name
    if not script_path.exists():
        return False, f"Script not found: {script_path}"

    log.info(f"[API] Starting script: {script_name}")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(APP_DIR),
            env={**os.environ},
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return False, f"Script timed out after {timeout}s"

        output = stdout.decode("utf-8", errors="replace")
        success = proc.returncode == 0
        log.info(f"[API] Script finished. exit={proc.returncode} success={success}")
        return success, output

    except Exception as exc:
        log.error(f"[API] Failed to run script: {exc}")
        return False, str(exc)


def parse_provider_output(output: str) -> dict:
    """
    Extract xtream_host, xtream_username, xtream_password, and m3u_url from
    provider script stdout.
    """
    result = {
        "xtream_host": None,
        "xtream_username": None,
        "xtream_password": None,
        "m3u_url": None,
    }

    for line in output.splitlines():
        line = line.strip()

        if line.startswith("[*] Portal URL:") or line.startswith("[*] Playlist URL:") or line.startswith("[*] IBO Player Playlist URL:"):
            result["xtream_host"] = line.split(":", 1)[1].strip()

        elif line.startswith("[*] Username:"):
            result["xtream_username"] = line.split(":", 1)[1].strip()

        elif line.startswith("[*] Password:"):
            result["xtream_password"] = line.split(":", 1)[1].strip()

        elif line.startswith("[*] M3U URL:") or line.startswith("[*] M3U Download URL:"):
            raw = line[line.index(":", 4) + 1:].strip()
            result["m3u_url"] = raw

    return result


async def run_provider(provider: str, script_name: str, timeout: int) -> GenerateResponse:
    success, output = await run_script(script_name, timeout=timeout)

    if not success:
        log.warning(f"[API] {provider} script failed")
        return GenerateResponse(
            success=False,
            provider=provider,
            error="Script exited with non-zero status",
            logs=output[-4000:],
        )

    parsed = parse_provider_output(output)

    if not parsed["xtream_username"] or not parsed["xtream_password"]:
        log.warning(f"[API] Could not parse credentials from {provider} script output")
        return GenerateResponse(
            success=False,
            provider=provider,
            error="Credentials not found in script output",
            logs=output[-4000:],
        )

    log.info(f"[API] {provider} credentials extracted: user={parsed['xtream_username']}")
    return GenerateResponse(
        success=True,
        provider=provider,
        xtream_host=parsed["xtream_host"],
        xtream_username=parsed["xtream_username"],
        xtream_password=parsed["xtream_password"],
        m3u_url=parsed["m3u_url"],
        logs=output[-4000:],
    )


# Routes
@app.get("/api/health", response_model=HealthResponse, tags=["System"])
async def health():
    return HealthResponse(status="ok", timestamp=datetime.utcnow().isoformat())


@app.post(
    "/api/generate/zazy",
    response_model=GenerateResponse,
    tags=["Providers"],
    dependencies=[Depends(verify_api_key)],
)
async def generate_zazy():
    """
    Trigger zazy_playlist_automation.py to create a new Zazy trial account
    and return the extracted Xtream credentials.
    """
    log.info("[API] /api/generate/zazy called")
    return await run_provider("zazy", "zazy_playlist_automation.py", timeout=600)


@app.post(
    "/api/generate/layerseven",
    response_model=GenerateResponse,
    tags=["Providers"],
    dependencies=[Depends(verify_api_key)],
)
async def generate_layerseven():
    """
    Trigger layerseven_automation.py to create a new LayerSeven trial account
    and return the extracted Xtream credentials.
    """
    log.info("[API] /api/generate/layerseven called")
    return await run_provider("layerseven", "layerseven_automation.py", timeout=900)
