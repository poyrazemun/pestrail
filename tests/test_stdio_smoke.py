"""End-to-end smoke test: start the real server as a subprocess and speak JSON-RPC over stdio.

Uses dummy credentials and a reserved, non-resolvable host (.invalid); no tool call made
here reaches the network. Also asserts that stdout carries nothing but JSON-RPC messages.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from typing import Any

import pytest
from mcp.types import LATEST_PROTOCOL_VERSION

TIMEOUT = 30

DUMMY_ENV = {
    "TESTRAIL_URL": "https://testrail.invalid",
    "TESTRAIL_USERNAME": "smoke@example.invalid",
    "TESTRAIL_API_KEY": "dummy-smoke-key",
}


def clean_env(extra_env: dict[str, str], *, config_home: str | None = None) -> dict[str, str]:
    """Process env without TESTRAIL_*/PESTRAIL_* vars from the developer's shell.

    Unless ``config_home`` is given, .env loading is disabled, so a developer's real
    per-user pestrail config is never read by tests.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TESTRAIL_", "PESTRAIL_"))}
    if config_home is None:
        env["PESTRAIL_ENV_FILE"] = "none"
    else:
        env["APPDATA"] = config_home
        env["XDG_CONFIG_HOME"] = config_home
    env["PYTHONUNBUFFERED"] = "1"
    env.update(extra_env)
    return env


def run_once(env: dict[str, str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pestrail"],
        input="",
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=TIMEOUT,
        check=False,
    )


class StdioServer:
    def __init__(self, extra_env: dict[str, str], *, config_home: str | None = None) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "pestrail"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_env(extra_env, config_home=config_home),
            text=True,
            encoding="utf-8",
        )
        self.lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._next_id = 0

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line)

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        self.send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})
        while True:
            try:
                line = self.lines.get(timeout=TIMEOUT)
            except queue.Empty:
                pytest.fail(f"No response to {method} within {TIMEOUT}s. stderr:\n{self.stderr()}")
            # Every stdout line must be a JSON-RPC message; anything else corrupts the stream.
            message = json.loads(line)
            assert message.get("jsonrpc") == "2.0", line
            if message.get("id") == msg_id:
                return message

    def stderr(self) -> str:
        if self.proc.poll() is None:
            self.proc.kill()
        assert self.proc.stderr is not None
        return self.proc.stderr.read()

    def close(self) -> None:
        if self.proc.poll() is None:
            assert self.proc.stdin is not None
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def _session(
    extra_env: dict[str, str], *, config_home: str | None = None
) -> tuple[StdioServer, dict[str, Any]]:
    server = StdioServer(extra_env, config_home=config_home)
    init = server.request(
        "initialize",
        {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pestrail-smoke", "version": "0"},
        },
    )
    assert "result" in init, init
    server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return server, init["result"]


def test_stdio_lists_all_tools():
    server, init = _session(DUMMY_ENV)
    try:
        assert init["serverInfo"]["name"] == "pestrail"
        tools = server.request("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        assert len(names) == 38
        assert {"get_projects", "add_results_for_cases", "add_result_with_attachments"} <= names
        assert not any(n.startswith(("delete", "close", "update")) for n in names)

        # A tool error comes back as an is_error result, not a crash (no network needed).
        res = server.request("tools/call", {"name": "get_case", "arguments": {"case_id": "R5"}})
        assert res["result"]["isError"] is True
        assert "does not belong to a case" in res["result"]["content"][0]["text"]
    finally:
        server.close()
    assert "dummy-smoke-key" not in server.stderr()


def test_stdio_read_only_mode_hides_write_tools():
    server, _ = _session({**DUMMY_ENV, "TESTRAIL_READ_ONLY": "true"})
    try:
        tools = server.request("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        assert len(names) == 29
        assert not any(n.startswith("add_") for n in names)
    finally:
        server.close()


def _write_env(path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")


def test_per_user_env_file_is_loaded(tmp_path):
    config_home = tmp_path / "config"
    _write_env(config_home / "pestrail" / ".env", {**DUMMY_ENV, "TESTRAIL_READ_ONLY": "true"})
    server, _ = _session({}, config_home=str(config_home))
    try:
        names = {t["name"] for t in server.request("tools/list")["result"]["tools"]}
        assert len(names) == 29  # read-only came from the per-user file
    finally:
        server.close()


def test_working_directory_env_file_is_not_loaded(tmp_path):
    # A cloned repo with its own .env must not configure pestrail implicitly.
    repo = tmp_path / "cloned-repo"
    _write_env(repo / ".env", {**DUMMY_ENV, "TESTRAIL_URL": "https://attacker.invalid"})
    empty_home = tmp_path / "empty-config"
    empty_home.mkdir()
    proc = run_once(clean_env({}, config_home=str(empty_home)), cwd=str(repo))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "Missing required" in proc.stderr
    assert "is not loaded automatically" in proc.stderr
    assert "PESTRAIL_ENV_FILE" in proc.stderr


def test_explicit_env_file_is_loaded(tmp_path):
    env_file = tmp_path / "my.env"
    _write_env(env_file, {**DUMMY_ENV, "TESTRAIL_READ_ONLY": "true"})
    server, _ = _session({"PESTRAIL_ENV_FILE": str(env_file)})
    try:
        names = {t["name"] for t in server.request("tools/list")["result"]["tools"]}
        assert len(names) == 29
    finally:
        server.close()


def test_explicit_env_file_missing_fails(tmp_path):
    proc = run_once(clean_env({"PESTRAIL_ENV_FILE": str(tmp_path / "nope.env")}))
    assert proc.returncode == 2
    assert "PESTRAIL_ENV_FILE not found" in proc.stderr


def test_env_file_cannot_redirect_api_key(tmp_path):
    env_file = tmp_path / ".env"
    _write_env(env_file, {"TESTRAIL_URL": "https://attacker.invalid"})
    without_url = {k: v for k, v in DUMMY_ENV.items() if k != "TESTRAIL_URL"}
    proc = run_once(clean_env({**without_url, "PESTRAIL_ENV_FILE": str(env_file)}))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "Refusing to read TESTRAIL_URL from a .env file" in proc.stderr
    assert "dummy-smoke-key" not in proc.stderr


def test_missing_config_fails_fast_on_stderr(tmp_path):
    proc = run_once(clean_env({}), cwd=str(tmp_path))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "TESTRAIL_URL" in proc.stderr
    assert "TESTRAIL_API_KEY" in proc.stderr
