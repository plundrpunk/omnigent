"""Lifecycle checks for the ACP subprocess transport."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omnigent.acp_transport import ACPClient


@pytest.mark.asyncio
async def test_start_retains_stderr_drain_until_eof(monkeypatch):
    """Diagnostics must keep draining after start returns, without another spawn."""
    stderr = asyncio.StreamReader()
    proc = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=stderr, returncode=0)
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = ACPClient(["fake-acp"])

    await client.start()
    drain = client._stderr_task
    try:
        assert drain is not None
        assert not drain.done()
        await client.start()
        spawn.assert_awaited_once()
        assert client._stderr_task is drain

        stderr.feed_data(b"agent diagnostic\n")
        stderr.feed_eof()
        await asyncio.wait_for(drain, timeout=1)
        assert client._stderr_tail == ["agent diagnostic"]
    finally:
        stderr.feed_eof()
        if drain is not None and not drain.done():
            drain.cancel()
        await client.close()
