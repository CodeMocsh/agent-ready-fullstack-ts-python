"""Every exclusion in `[tool.uv.exclude-newer-package]` names one timestamp inside the cool-off,
and the package it names has a floor."""

import json
import re
import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.test_gate import PYPROJECT

MOMENT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SPAN = re.compile(r"P[0-9WDTHMS.]+|\d+\s*[A-Za-z]+")
DURATION = re.compile(r"(\d+) (day|week)s?")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
FLOOR = re.compile(r">=?|==|~=")
NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
WEEK = timedelta(days=7)


def pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def cool_off(settings: dict[str, Any]) -> timedelta:
    setting = settings["tool"]["uv"]["exclude-newer"]
    found = DURATION.fullmatch(setting)
    assert found is not None, (
        f"exclude-newer is {setting!r}. This test reads the cool-off only as a number of days "
        f'or weeks, such as "7 days".'
    )
    return timedelta(days=int(found.group(1)) * (7 if found.group(2) == "week" else 1))


def name_of(requirement: str) -> str:
    found = NAME.match(requirement)
    assert found is not None, f"{requirement!r} does not start with a package name"
    return re.sub(r"[-_.]+", "-", found.group()).lower()


def floored(settings: dict[str, Any]) -> set[str]:
    """Every package that a requirement in this pyproject gives a floor: `>`, `>=`, `==` or `~=`,
    read before any environment marker."""
    groups = settings.get("dependency-groups", {}).values()
    requirements = [
        *settings["project"]["dependencies"],
        *(entry for group in groups for entry in group if isinstance(entry, str)),
        *settings["tool"]["uv"].get("constraint-dependencies", []),
    ]
    return {
        name_of(requirement)
        for requirement in requirements
        if FLOOR.search(requirement.partition(";")[0])
    }


def as_toml(value: object) -> str:
    return json.dumps(value, default=str)


def moment_refusals(name: str, moment: object, now: datetime, since: datetime) -> Iterator[str]:
    shown = as_toml(moment)
    if moment is False or (isinstance(moment, str) and SPAN.fullmatch(moment)):
        yield (
            f"{name} = {shown} names no moment. `false` admits every later release of {name}, "
            f"and a duration gives every later release a shorter cool-off. Write the moment just "
            f'after the fix was uploaded, such as "2026-09-23T12:00:00Z".'
        )
        return
    if not (isinstance(moment, str) and (MOMENT.fullmatch(moment) or DATE.fullmatch(moment))):
        yield (
            f'{name} = {shown} is not a timestamp in the form "2026-09-23T12:00:00Z". Write '
            f"the moment just after the fix was uploaded."
        )
        return
    try:
        at = datetime.fromisoformat(moment)
    except ValueError:
        yield f"{name} = {shown} is not a real date. Write the moment just after the fix was uploaded."
        return
    if at.tzinfo is None:
        yield (
            f"{name} = {shown} is a date with no time. uv reads it in the time zone of each "
            f"machine, so two machines resolve different releases. Write the moment just after "
            f'the fix was uploaded, such as "2026-09-23T12:00:00Z".'
        )
        at = at.replace(tzinfo=UTC)
    yield from age_refusals(name, at, now, since)


def age_refusals(name: str, at: datetime, now: datetime, since: datetime) -> Iterator[str]:
    if at > now:
        yield (
            f"{name} is excluded until {at.isoformat()}, which is in the future. It admits "
            f"releases of {name} that nobody has seen yet. Write the moment just after the fix "
            f"was uploaded."
        )
    if at <= since:
        yield (
            f"{name} is excluded until {at.isoformat()}, which is older than the cool-off. "
            f"An exclusion replaces the cool-off for {name}. It does not fall back to it. So "
            f"no release of {name} after that moment resolves, a security fix included. The "
            f"cool-off now admits the fix by itself. Delete the exclusion."
        )


def refusals_of(
    name: str, moment: object, floors: set[str], now: datetime, since: datetime
) -> Iterator[str]:
    yield from moment_refusals(name, moment, now, since)
    if name_of(name) not in floors:
        yield (
            f"{name} is excluded and has no floor. The floor is the fix: add "
            f"`{name}>=<fixed version>` to dependencies, or to constraint-dependencies when "
            f"another package brings {name} in."
        )


def refusals(settings: dict[str, Any], now: datetime) -> list[str]:
    exclusions = settings["tool"]["uv"].get("exclude-newer-package", {}).items()
    if not exclusions:
        return []
    floors = floored(settings)
    since = now - cool_off(settings)
    return [
        refusal
        for name, moment in exclusions
        for refusal in refusals_of(name, moment, floors, now, since)
    ]


def test_every_exclusion_is_one_timestamp_inside_the_cool_off_with_a_floor():
    found = refusals(pyproject(), datetime.now(UTC))

    assert found == [], "\n".join(found)


def stamp(at: datetime) -> str:
    return f"{at:%Y-%m-%dT%H:%M:%SZ}"


def excluding(moment: object, *floors: str) -> dict[str, Any]:
    settings = pyproject()
    uv = settings["tool"]["uv"]
    uv["exclude-newer"] = "7 days"
    uv["exclude-newer-package"] = {"patched": moment}
    uv["constraint-dependencies"] = [*uv.get("constraint-dependencies", []), *floors]
    return settings


@pytest.mark.parametrize(
    "floor",
    [
        "patched>=1.2.3",
        "patched>1.2",
        "patched==1.2.3",
        "patched~=1.2.3",
        "Patched[extra] >= 1.2.3",
    ],
)
def test_an_exclusion_inside_the_cool_off_with_a_floor_is_accepted(floor: str):
    inside = stamp(NOW - WEEK / 2)

    assert refusals(excluding(inside, floor), NOW) == []


@pytest.mark.parametrize(
    ("moment", "reason"),
    [
        (False, "names no moment"),
        ("3 days", "names no moment"),
        ("P3D", "names no moment"),
        (1, "not a timestamp"),
        ("2026-10-08T00:00:00", "not a timestamp"),
        ("2026-02-30T00:00:00Z", "not a real date"),
        (stamp(NOW - WEEK / 2)[:10], "a date with no time"),
        (stamp(NOW + timedelta(days=1)), "in the future"),
        (stamp(NOW - WEEK - timedelta(hours=1)), "older than the cool-off"),
    ],
    ids=[
        "false",
        "duration",
        "iso-duration",
        "number",
        "no-offset",
        "impossible-date",
        "bare-date",
        "future",
        "older",
    ],
)
def test_an_exclusion_that_is_not_one_moment_inside_the_cool_off_is_refused(
    moment: object, reason: str
):
    found = refusals(excluding(moment, "patched>=1.2.3"), NOW)

    assert len(found) == 1, found
    assert reason in found[0]


@pytest.mark.parametrize(
    "requirements",
    [[], ["patched"], ["patched<2"], ["patched; python_version >= '3.12'"]],
    ids=["absent", "bare", "ceiling", "marker"],
)
def test_an_exclusion_with_no_floor_is_refused(requirements: list[str]):
    found = refusals(excluding(stamp(NOW - WEEK / 2), *requirements), NOW)

    assert len(found) == 1, found
    assert "no floor" in found[0]
