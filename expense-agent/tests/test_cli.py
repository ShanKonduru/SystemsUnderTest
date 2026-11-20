"""harbor-expense-agent: settings, start order, health waits and shutdown (fake processes; one real start-up)."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from expense_agent import cli


class FakeProc:
    def __init__(self, cmd: list[str], exits_after: int | None = None) -> None:
        self.cmd, self.polls, self.exits_after = cmd, 0, exits_after
        self.returncode: int | None = None
        self.terminated = self.killed = False
        self.hang = False

    def poll(self) -> int | None:
        self.polls += 1
        if self.exits_after is not None and self.polls > self.exits_after:
            self.returncode = 1
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        if self.hang:
            raise subprocess.TimeoutExpired(self.cmd, timeout or 0)
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True


def test_read_env_file_reads_values_as_written(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text(
        "﻿# a comment\n\nDEFAULT_LLM_PROVIDER=SCRIPTED\nexport LLM_SCRIPT=expense_agent.policy:factory\n"
        "PASSWORD=a$$b\nno equals sign\n=no key\n",
        encoding="utf-8",
    )
    assert cli.read_env_file(f) == {
        "DEFAULT_LLM_PROVIDER": "SCRIPTED",
        "LLM_SCRIPT": "expense_agent.policy:factory",
        "PASSWORD": "a$$b",
    }
    assert cli.read_env_file(tmp_path / "missing") == {}


def test_child_env_real_environment_wins_and_paths_follow_data(tmp_path: Path) -> None:
    args = cli.parse_args(["--data", str(tmp_path / "d"), "--tools-port", "9123"])
    env = cli.child_env(args, {"DEFAULT_LLM_PROVIDER": "AZURE"}, {"DEFAULT_LLM_PROVIDER": "SCRIPTED", "X": "1"})
    assert env["DEFAULT_LLM_PROVIDER"] == "AZURE" and env["X"] == "1"
    assert env["EXPENSE_DB"] == str((tmp_path / "d" / "expense.db").resolve())
    assert env["EXPENSE_OUT"] == str((tmp_path / "d" / "out").resolve())
    assert env["EXPENSE_TOOLS_URL"] == "http://127.0.0.1:9123/mcp"
    proxied = cli.child_env(cli.parse_args(["--tools-url", "http://127.0.0.1:9710/mcp"]), {}, {})
    assert proxied["EXPENSE_TOOLS_URL"] == "http://127.0.0.1:9710/mcp"


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str], healthy: list[bool], procs: list[FakeProc]) -> int:
    answers = iter(healthy)
    monkeypatch.setattr(cli, "wait_healthy", lambda *a: next(answers))
    started = iter(procs)

    def popen(cmd: list[str], env: dict[str, str]) -> FakeProc:
        proc = next(started)
        proc.cmd = cmd
        proc.env = env  # type: ignore[attr-defined]
        return proc

    return cli.main(argv, popen=popen, sleep=lambda _s: None, environ={})


def test_starts_tools_then_agent_and_stops_both_when_one_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".env").write_text("DEFAULT_LLM_PROVIDER=SCRIPTED\n", encoding="utf-8")
    tools, agent = FakeProc([]), FakeProc([], exits_after=2)
    code = _run(
        monkeypatch, ["--env-file", str(tmp_path / ".env"), "--data", str(tmp_path)], [True, True], [tools, agent]
    )
    assert code == 1
    assert tools.cmd[1:3] == ["-m", "expense_agent.tools_server"]
    assert agent.cmd[1:4] == ["-m", "uvicorn", "expense_agent.service:app"]
    assert agent.env["DEFAULT_LLM_PROVIDER"] == "SCRIPTED"  # type: ignore[attr-defined]
    assert tools.terminated
    out = capsys.readouterr()
    assert "LLM provider SCRIPTED" in out.out and "Ctrl+C stops both" in out.out
    assert "a server stopped" in out.err


def test_tool_server_that_never_answers_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tools = FakeProc([])
    assert _run(monkeypatch, ["--env-file", str(tmp_path / "none")], [False], [tools]) == 1
    assert "tool server did not start" in capsys.readouterr().err
    assert tools.terminated


def test_agent_that_never_answers_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tools, agent = FakeProc([]), FakeProc([])
    assert _run(monkeypatch, ["--env-file", str(tmp_path / "none")], [True, False], [tools, agent]) == 1
    assert "agent did not start" in capsys.readouterr().err
    assert tools.terminated and agent.terminated


def test_ctrl_c_is_a_clean_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tools, agent = FakeProc([]), FakeProc([])
    answers = iter([True, True])
    monkeypatch.setattr(cli, "wait_healthy", lambda *a: next(answers))
    started = iter([tools, agent])

    def interrupt(_s: float) -> None:
        raise KeyboardInterrupt

    code = cli.main(
        ["--env-file", str(tmp_path / "none"), "--data", str(tmp_path)],
        popen=lambda cmd, env: next(started),
        sleep=interrupt,
        environ={},
    )
    assert code == 0 and tools.terminated and agent.terminated


def test_reset_writes_the_seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tools = FakeProc([])
    _run(monkeypatch, ["--env-file", str(tmp_path / "none"), "--data", str(tmp_path), "--reset"], [False], [tools])
    assert (tmp_path / "expense.db").is_file() and (tmp_path / "out" / "notices").is_dir()


def test_stop_kills_a_process_that_will_not_exit() -> None:
    proc = FakeProc([])
    proc.hang = True
    cli.stop([proc])  # type: ignore[list-item]
    assert proc.terminated and proc.killed


def test_wait_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    class Resp:
        status = 200

        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

    def urlopen(url: str, timeout: float) -> Resp:
        calls.append(url)
        if len(calls) == 1:
            raise OSError("refused")
        return Resp()

    monkeypatch.setattr(cli.urllib.request, "urlopen", urlopen)
    assert cli.wait_healthy("http://x/healthz", FakeProc([]), 5, lambda _s: None)  # type: ignore[arg-type]
    assert len(calls) == 2
    assert not cli.wait_healthy("http://x/healthz", FakeProc([], exits_after=0), 5, lambda _s: None)  # type: ignore[arg-type]
    assert not cli.wait_healthy("http://x/healthz", FakeProc([]), 0, lambda _s: None)  # type: ignore[arg-type]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_real_start_scripted_handles_an_event(tmp_path: Path) -> None:
    """Both servers for real, with the offline rule-based model: one event end to end, then Ctrl+C-like stop."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEFAULT_LLM_PROVIDER=SCRIPTED\nEXPENSE_LLM_PROVIDER=SCRIPTED\nLLM_SCRIPT=expense_agent.policy:factory\n"
        "EXPENSE_DEFECTS=\n",
        encoding="utf-8",
    )
    port, tools_port = _free_port(), _free_port()
    result: dict[str, int] = {}
    stop_flag = threading.Event()

    def sleep(seconds: float) -> None:
        if stop_flag.is_set():
            raise KeyboardInterrupt
        time.sleep(min(seconds, 0.2))

    argv = ["--env-file", str(env_file), "--data", str(tmp_path / "data"), "--port", str(port)]
    argv += ["--tools-port", str(tools_port)]
    worker = threading.Thread(target=lambda: result.setdefault("code", cli.main(argv, sleep=sleep)))
    worker.start()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not (tmp_path / "data" / "expense.db").exists():
            time.sleep(0.2)
        body = b'{"event": {"type": "expense.submitted", "report_id": "ER-3001"}}'
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/events",
            data=body,
            headers={"X-Principal": "expense-bot", "Content-Type": "application/json"},
        )
        for _ in range(120):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    episode = resp.read().decode()
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("the agent never answered")
        assert '"expense_review"' in episode and "update_report" in episode
    finally:
        stop_flag.set()
        worker.join(timeout=30)
    assert result.get("code") == 0
    assert sys.executable  # the children ran under this interpreter
