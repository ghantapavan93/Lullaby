"""The review gateway's slot reseed, without processes: the race a single-slot
free host hits when one visitor ends a workspace and the next one arrives while
the janitor is still wiping it.

Invariant: at most one reseed runs per slot, everyone waiting on it shares its
result, and the janitor never wipes a slot a visitor has already been handed.
"""
from __future__ import annotations

import asyncio
import importlib.util

import pytest

from .conftest import ROOT


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_STATE", str(tmp_path / "gateway.sqlite3"))
    monkeypatch.setenv("GATEWAY_SLOTS", "1,2")
    spec = importlib.util.spec_from_file_location("gateway_under_test", ROOT.parent / "deploy" / "gateway" / "gateway.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: list[int] = []

    async def fake_reseed(slot: int) -> bool:
        calls.append(slot)
        await asyncio.sleep(0.2)
        return True

    module._reseed = fake_reseed
    module.calls = calls
    yield module
    module.store.db.close()


def test_a_visitor_arriving_mid_reseed_shares_it_instead_of_being_refused(gateway):
    async def scenario():
        janitor = asyncio.create_task(gateway.reset_slot(1))          # the janitor starts wiping slot 1
        await asyncio.sleep(0.05)
        visitor = await gateway.reset_slot(1)                           # a visitor claims it meanwhile
        return await janitor, visitor

    assert asyncio.run(scenario()) == (True, True)
    assert gateway.calls == [1]                                         # one reseed, not two, not a refusal
    assert gateway.resets == {}                                         # nothing left in flight


def test_a_failed_reseed_is_reported_to_every_waiter_and_retried_next_time(gateway):
    async def failing(slot: int) -> bool:
        gateway.calls.append(slot)
        await asyncio.sleep(0.1)
        return False

    gateway._reseed = failing

    async def scenario():
        return await asyncio.gather(gateway.reset_slot(2), gateway.reset_slot(2))

    assert asyncio.run(scenario()) == [False, False]
    assert asyncio.run(gateway.reset_slot(2)) is False and gateway.calls == [2, 2]


def test_the_janitor_never_wipes_a_slot_handed_out_while_it_reseeded_another(gateway):
    async def visitor_takes_slot_2_meanwhile(slot: int) -> bool:
        gateway.calls.append(slot)
        if slot == 1:   # both slots were dirty; slot 2 is leased (and reseeded by its visitor) during slot 1's wipe
            gateway.store.db.execute("UPDATE slots SET state='leased', token_hash='t' WHERE slot=2")
        await asyncio.sleep(0.05)
        return True

    gateway._reseed = visitor_takes_slot_2_meanwhile
    assert sorted(gateway.store.with_state("dirty")) == [1, 2]            # new slots start dirty
    asyncio.run(gateway.janitor_pass())
    assert gateway.calls == [1]                                         # slot 2's visitor keeps their data
    assert gateway.store.with_state("clean") == [1] and gateway.store.with_state("leased") == [2]
