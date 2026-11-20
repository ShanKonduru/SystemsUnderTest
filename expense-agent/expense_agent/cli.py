"""``harbor-expense-agent``: run the expense agent and its tools on this machine, without Docker.

It starts the same two processes as ``docker-compose.yml`` (the MCP tool server and the agent service) and stops
both on Ctrl+C. Settings come from the environment and from ``.env`` in the current folder (real environment
variables win), as in the containers. The agent's records go to ``--data`` (default ``./data``): ``expense.db`` and
``out/notices/``, the same layout as the containers' ``/app/data``.

    harbor-expense-agent                                  agent :8700, tools :9700, the agent calls the tools direct
    harbor-expense-agent --tools-url http://127.0.0.1:9710/mcp
                                                          the agent calls the tools through a recording proxy

Without an LLM key: ``DEFAULT_LLM_PROVIDER=SCRIPTED`` and ``LLM_SCRIPT=expense_agent.policy:factory`` (a
deterministic, rule-based stand-in for the model; planted defects still apply).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from expense_agent import __version__

Popen = Callable[..., subprocess.Popen[bytes]]


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` lines, used as written (no quotes removed, no ``$`` expansion), as Docker's ``format: raw``."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if key:
            values[key] = value.strip()
    return values


def child_env(args: argparse.Namespace, base: Mapping[str, str], dotenv: Mapping[str, str]) -> dict[str, str]:
    """The children's environment: ``.env`` under the real environment, then this command's own settings."""
    env = {**dotenv, **base}
    data = Path(args.data).resolve()
    env["EXPENSE_DB"] = str(data / "expense.db")
    env["EXPENSE_OUT"] = str(data / "out")
    env["EXPENSE_TOOLS_URL"] = args.tools_url or f"http://127.0.0.1:{args.tools_port}/mcp"
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def wait_healthy(url: str, proc: subprocess.Popen[bytes], timeout: float, sleep: Callable[[float], None]) -> bool:
    """True once ``url`` answers 200; False if the process exits first or ``timeout`` seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        sleep(0.5)
    return False


def stop(procs: Sequence[subprocess.Popen[bytes]]) -> None:
    for proc in reversed(procs):
        if proc.poll() is None:
            proc.terminate()
    for proc in reversed(procs):
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="harbor-expense-agent",
        description="Run Harbor Mutual's expense agent (fictitious) and its MCP tools on this machine, without Docker.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--data", default="data", help="where the agent keeps expense.db and out/notices (default ./data)")
    p.add_argument("--env-file", default=".env", help="settings file read first (default ./.env; missing is fine)")
    p.add_argument("--host", default="127.0.0.1", help="address both servers listen on (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8700, help="the agent's port (default 8700)")
    p.add_argument("--tools-port", type=int, default=9700, help="the MCP tool server's port (default 9700)")
    p.add_argument(
        "--tools-url",
        default="",
        help="where the agent reaches the tools, e.g. a recording proxy (default http://127.0.0.1:<tools-port>/mcp)",
    )
    p.add_argument("--reset", action="store_true", help="restore the seed data before starting")
    p.add_argument("--startup-timeout", type=float, default=60.0, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    popen: Popen = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = parse_args(argv)
    env = child_env(args, os.environ if environ is None else environ, read_env_file(Path(args.env_file)))
    if args.reset:
        from datetime import date

        from expense_agent.backend import init_db

        today = env.get("EXPENSE_TODAY", "").strip()
        init_db(env["EXPENSE_DB"], env["EXPENSE_OUT"], date.fromisoformat(today) if today else date(2026, 11, 3))
    py = sys.executable
    tools_cmd = [py, "-m", "expense_agent.tools_server", "--host", args.host, "--port", str(args.tools_port)]
    agent_cmd = [py, "-m", "uvicorn", "expense_agent.service:app", "--host", args.host, "--port", str(args.port)]
    agent_cmd += ["--log-level", "warning"]
    procs: list[subprocess.Popen[bytes]] = []
    try:
        procs.append(popen(tools_cmd, env=env))
        if not wait_healthy(f"http://127.0.0.1:{args.tools_port}/healthz", procs[0], args.startup_timeout, sleep):
            print(f"harbor-expense-agent: the tool server did not start on port {args.tools_port}", file=sys.stderr)
            return 1
        procs.append(popen(agent_cmd, env=env))
        if not wait_healthy(f"http://127.0.0.1:{args.port}/healthz", procs[1], args.startup_timeout, sleep):
            print(f"harbor-expense-agent: the agent did not start on port {args.port}", file=sys.stderr)
            return 1
        provider = env.get("EXPENSE_LLM_PROVIDER") or env.get("DEFAULT_LLM_PROVIDER") or "(not set)"
        print(f"Harbor Mutual expense agent {__version__} (fictitious), LLM provider {provider}")
        print(f"  agent  http://127.0.0.1:{args.port}/events   (POST, header X-Principal)")
        print(f"  tools  http://127.0.0.1:{args.tools_port}/mcp   the agent calls {env['EXPENSE_TOOLS_URL']}")
        print(f"  data   {Path(env['EXPENSE_DB']).parent}")
        print("Ctrl+C stops both.", flush=True)
        while all(p.poll() is None for p in procs):
            sleep(1)
        print("harbor-expense-agent: a server stopped; stopping the other", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        stop(procs)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
