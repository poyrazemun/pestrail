"""Shared fixtures. Tests never hit a real TestRail instance: all HTTP is mocked with respx."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx

from pestrail.client import TestRailClient
from pestrail.config import Config, Secret

FAKE_URL = "https://testrail.invalid"
API = f"{FAKE_URL}/index.php?/api/v2/"
FAKE_KEY = "fake-api-key-DO-NOT-LEAK-123"


def make_config(
    *, read_only: bool = False, roots: tuple[Path, ...] = (), timeout: float = 5.0
) -> Config:
    return Config(
        url=FAKE_URL,
        username="qa@example.invalid",
        api_key=Secret(FAKE_KEY),
        read_only=read_only,
        attachment_roots=roots,
        timeout=timeout,
    )


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def mock_api() -> Iterator[respx.MockRouter]:
    # assert_all_mocked=True makes any un-mocked request fail instead of hitting the network.
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest.fixture
def client(mock_api: respx.MockRouter) -> Iterator[TestRailClient]:
    c = TestRailClient(make_config(), sleep=_no_sleep)
    yield c
    asyncio.run(c.aclose())


def run(coro):
    return asyncio.run(coro)
