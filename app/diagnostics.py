"""Connectivity diagnostics for the local stack.

Run via ``mise run doctor`` (one-shot report) or ``python -m app.diagnostics
--wait 90`` (poll until the configured MCP endpoint answers).

Exit codes: 0 when the configured endpoint is reachable, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx

from .config import settings

_PROBE_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "xhs-doctor", "version": "1"},
    },
}
_PROBE_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def probe(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Send an MCP ``initialize`` request and report reachability."""
    try:
        with httpx.stream(
            "POST", url, json=_PROBE_PAYLOAD, headers=_PROBE_HEADERS, timeout=timeout
        ) as response:
            if response.status_code == 200:
                return True, "OK"
            return False, f"HTTP {response.status_code}: {response.read()[:80]!r}"
    except Exception as exc:  # connection, DNS, TLS, timeout
        return False, f"{type(exc).__name__}: {exc}"[:140]


def _run(command: list[str], timeout: float = 20.0) -> tuple[int, str]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return completed.returncode, (completed.stdout or completed.stderr or "").strip()


def _parse_compose_ps(output: str) -> list[dict]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        records = []
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
    if isinstance(parsed, list):
        return parsed
    return [parsed] if isinstance(parsed, dict) else []


def docker_state() -> dict:
    """Container state and published ports for ``xhs-mcp``."""
    code, output = _run(["docker", "compose", "ps", "xhs-mcp", "--format", "json"])
    if code != 0:
        return {"error": output.splitlines()[0][:160] if output else "docker unavailable"}

    records = _parse_compose_ps(output)
    if not records:
        return {"error": "container not running"}

    record = records[0]
    ports = record.get("Ports") or record.get("Publishers") or "?"
    return {"state": record.get("State") or record.get("Status") or "?", "ports": ports}


def _default_gateway() -> str | None:
    """Windows host address when running inside WSL (None elsewhere)."""
    code, output = _run(["sh", "-c", "ip route show default 2>/dev/null | head -n1"])
    match = re.search(r"default via (\S+)", output)
    return match.group(1) if code == 0 and match else None


def candidate_urls() -> list[str]:
    """The configured URL plus the usual suspects worth testing.

    ``host.docker.internal`` is deliberately not probed: it only resolves inside
    Docker containers, so from a shell it always fails with "No route to host".
    """
    parsed = urlparse(settings.xhs.mcp_url)
    port = parsed.port or 18060
    path = parsed.path or "/mcp"

    urls = [settings.xhs.mcp_url]
    hosts = ["127.0.0.1", "localhost"]
    gateway = _default_gateway()
    if gateway:
        hosts.append(gateway)
    for host in hosts:
        url = f"http://{host}:{port}{path}"
        if url not in urls:
            urls.append(url)
    return urls


def hints(results: list[tuple[str, bool, str]], state: dict) -> None:
    """Print the action that matches what was actually observed."""
    working = [url for url, ok, _ in results if ok]
    configured = settings.xhs.mcp_url

    print("\n== next step ==")
    if configured in working:
        print(f"- {configured} is reachable; nothing to fix on the MCP side.")
        return
    if working:
        print(f"- {configured} is NOT reachable, but {working[0]} is.")
        print(f"  Put this in .env:  XHS_MCP_URL={working[0]}")
        if urlparse(working[0]).hostname not in ("127.0.0.1", "localhost"):
            print("  (that is the Windows host address; WSL cannot always see Docker's localhost forwarding.)")
        else:
            print("  (localhost may resolve to IPv6 ::1 inside WSL, where nothing listens.)")
        return

    rejected = [
        url for url, ok, detail in results if not ok and ("401" in detail or "403" in detail)
    ]
    if rejected:
        print(f"- {rejected[0]} answered 401/403: the container enforces AUTH_TOKEN.")
        print("  Set XHS_AUTH_TOKEN in .env to the same value the container was started with.")
        return

    if "error" in state:
        print(f"- {state['error']}. Start it: docker compose up -d xhs-mcp")
        return
    print("- The container is up but nothing answers on the MCP port.")
    print("- Check its logs: docker compose logs --tail 50 xhs-mcp")
    print("- First boot downloads a headless browser, so allow a minute.")


def report() -> list[tuple[str, bool, str]]:
    print("== config ==")
    print(f"MCP URL : {settings.xhs.mcp_url}")
    print(f"Timeout : {settings.xhs.timeout_seconds}s")
    print(f"API key : {'set' if settings.deepseek.api_key else 'missing'}")

    state = docker_state()
    print("\n== docker ==")
    if "error" in state:
        print(f"xhs-mcp : {state['error']}")
    else:
        print(f"xhs-mcp : {state['state']}")
        print(f"ports   : {state['ports']}")

    print("\n== reachability ==")
    results: list[tuple[str, bool, str]] = []
    for url in candidate_urls():
        ok, detail = probe(url)
        results.append((url, ok, detail))
        print(f"{url:38} {'OK  ' if ok else 'FAIL'} {detail}")

    hints(results, state)
    return results


def _wait(seconds: float) -> int:
    print(f"waiting up to {seconds:.0f}s for {settings.xhs.mcp_url} ...")
    start = time.monotonic()
    deadline = start + seconds
    while time.monotonic() < deadline:
        ok, _ = probe(settings.xhs.mcp_url, timeout=2.0)
        if ok:
            print("xhs-mcp is reachable")
            return 0
        # A container that is up should bind quickly; after ~20s this is almost
        # certainly a networking/config problem, so stop and diagnose.
        if time.monotonic() - start >= 20:
            print("\nthe configured endpoint is not answering; checking the usual suspects ...\n")
            report()
            return 1
        time.sleep(2)
    print("\nxhs-mcp did not answer in time\n")
    report()
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose XHS MCP connectivity")
    parser.add_argument(
        "--wait",
        type=float,
        default=0,
        metavar="SECONDS",
        help="poll the configured endpoint until it answers",
    )
    args = parser.parse_args(argv)

    if args.wait:
        return _wait(args.wait)

    results = report()
    return 0 if any(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
