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


class StdioServer:
    def __init__(self, extra_env: dict[str, str]) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("TESTRAIL_")}
        env.update(extra_env)
        env["PYTHONUNBUFFERED"] = "1"
        env["PESTRAIL_ENV_FILE"] = os.devnull  # never pick up a developer's local .env
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "pestrail"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
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


def _session(extra_env: dict[str, str]) -> tuple[StdioServer, dict[str, Any]]:
    server = StdioServer(extra_env)
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


def test_missing_config_fails_fast_on_stderr():
    env = {k: v for k, v in os.environ.items() if not k.startswith("TESTRAIL_")}
    env["PESTRAIL_ENV_FILE"] = os.devnull
    proc = subprocess.run(
        [sys.executable, "-m", "pestrail"],
        input="",
        capture_output=True,
        text=True,
        env=env,
        timeout=TIMEOUT,
        check=False,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "TESTRAIL_URL" in proc.stderr
    assert "TESTRAIL_API_KEY" in proc.stderr
