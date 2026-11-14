"""A loader reads the keys that one turn asks for in one statement for each kind, and the kinds
of a turn in one unit of work. `docs/adr/template/0032`."""

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import PAGE_MAX
from app.pacing import yield_now
from app.store import loads
from app.store.loads import Loader, NotAnswered, NotLoading, loader_of, loading


@dataclass
class Units:
    """Each unit a loader opened, each statement in it, and the most units open at once."""

    opened: int = 0
    open: int = 0
    most_open: int = 0
    statements: list[tuple[str, list[int]]] = field(default_factory=list)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    @asynccontextmanager
    async def unit(self) -> AsyncGenerator["Units"]:
        self.opened += 1
        self.open += 1
        self.most_open = max(self.most_open, self.open)
        try:
            yield self
        finally:
            self.open -= 1


async def doubled(conn: Units, keys: Sequence[int]) -> Mapping[int, int]:
    conn.statements.append(("doubled", list(keys)))
    return {key: key * 2 for key in keys}


async def named(conn: Units, keys: Sequence[int]) -> Mapping[int, str]:
    conn.statements.append(("named", list(keys)))
    return {key: f"item {key}" for key in keys}


class Unreadable(LookupError):
    pass


async def unreadable_at_two(conn: Units, keys: Sequence[int]) -> Mapping[int, int | Exception]:
    conn.statements.append(("unreadable at two", list(keys)))
    return {key: Unreadable(f"item {key}") if key == 2 else key for key in keys}


async def answers_one_less(_conn: Units, keys: Sequence[int]) -> Mapping[int, int]:
    return {key: key for key in keys[1:]}


async def held_until_released(conn: Units, keys: Sequence[int]) -> Mapping[int, int]:
    conn.statements.append(("held", list(keys)))
    await conn.released.wait()
    return {key: key for key in keys}


async def at_once[T](loader: Loader, read: loads.SetRead[int, T], keys: list[int]) -> list[T]:
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(loader.load(read, key)) for key in keys]
    return [task.result() for task in tasks]


async def test_the_keys_that_one_turn_asks_for_are_one_statement() -> None:
    units = Units()
    with loading():
        answered = await at_once(loader_of("t", units.unit), doubled, [1, 2, 3, 2])

    assert answered == [2, 4, 6, 4]
    assert units.statements == [("doubled", [1, 2, 3])]
    assert units.opened == 1


async def test_each_kind_of_a_turn_is_a_statement_of_one_unit() -> None:
    units = Units()
    with loading():
        loader = loader_of("t", units.unit)
        async with asyncio.TaskGroup() as group:
            twice = group.create_task(at_once(loader, doubled, [1, 2]))
            names = group.create_task(at_once(loader, named, [1, 2]))

    assert (twice.result(), names.result()) == ([2, 4], ["item 1", "item 2"])
    assert sorted(units.statements) == [("doubled", [1, 2]), ("named", [1, 2])]
    assert units.opened == 1


async def test_more_keys_than_a_page_are_handed_a_page_at_a_time() -> None:
    units = Units()
    keys = list(range(PAGE_MAX + 1))
    with loading():
        answered = await at_once(loader_of("t", units.unit), doubled, keys)

    assert answered == [key * 2 for key in keys]
    assert [len(handed) for _, handed in units.statements] == [PAGE_MAX, 1]
    assert units.opened == 1


async def test_a_key_asked_after_its_turn_started_waits_for_the_next_unit() -> None:
    units = Units()
    with loading():
        loader = loader_of("t", units.unit)
        async with asyncio.TaskGroup() as group:
            first = group.create_task(loader.load(held_until_released, 1))
            while not units.statements:
                await yield_now()
            later = group.create_task(loader.load(held_until_released, 2))
            await yield_now()
            units.released.set()

    assert (first.result(), later.result()) == (1, 2)
    assert units.statements == [("held", [1]), ("held", [2])]
    assert (units.opened, units.most_open) == (2, 1)


async def test_a_key_whose_answer_is_a_failure_fails_only_its_own_callers() -> None:
    units = Units()
    with loading():
        loader = loader_of("t", units.unit)
        answered = await asyncio.gather(
            *(loader.load(unreadable_at_two, key) for key in (1, 2, 3)),
            return_exceptions=True,
        )
        names = await at_once(loader, named, [1, 2])

    assert answered[0::2] == [1, 3]
    assert isinstance(answered[1], Unreadable)
    assert str(answered[1]) == "item 2"
    assert names == ["item 1", "item 2"]
    assert units.statements[0] == ("unreadable at two", [1, 2, 3])


async def test_a_set_read_that_answers_other_keys_fails_each_caller() -> None:
    units = Units()
    with loading():
        loader = loader_of("t", units.unit)
        answered = await asyncio.gather(
            loader.load(answers_one_less, 1),
            loader.load(answers_one_less, 2),
            return_exceptions=True,
        )

    assert [type(answer) for answer in answered] == [NotAnswered, NotAnswered]
    assert "answers_one_less answered no value for ['1']" in str(answered[0])


async def test_a_turn_whose_callers_are_each_cancelled_leaves_its_unit() -> None:
    units = Units()
    with loading():
        async with asyncio.TaskGroup() as group:
            caller = group.create_task(loader_of("t", units.unit).load(held_until_released, 1))
            while not units.statements:
                await yield_now()
            _ = caller.cancel()
        while units.open:
            await yield_now()

    assert caller.cancelled()

    assert units.opened == 1


def test_a_loader_outside_a_scope_is_refused() -> None:
    with pytest.raises(NotLoading, match="outside a request"):
        _ = loader_of("t", Units().unit)


def test_a_scope_has_one_loader_for_each_key_and_another_scope_has_its_own() -> None:
    units = Units()
    with loading():
        first = loader_of("t", units.unit)
        assert loader_of("t", units.unit) is first
        assert loader_of("u", units.unit) is not first
    with loading():
        assert loader_of("t", units.unit) is not first


def test_each_request_has_a_scope_of_its_own() -> None:
    app = FastAPI()
    loads.instrument(app)
    seen: list[Loader] = []

    @app.get("/loader")
    async def loader() -> None:
        seen.append(loader_of("t", Units().unit))
        assert loader_of("t", Units().unit) is seen[-1]

    with TestClient(app) as client:
        assert client.get("/loader").status_code == 200
        assert client.get("/loader").status_code == 200

    assert seen[0] is not seen[1]
