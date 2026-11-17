from tests.test_gate import (
    OPT_IN_TIER,
    prerequisites_of,
    reached_from,
    recipe_of,
)


def test_the_commit_gate_keeps_static_checks_and_selects_commit_tests() -> None:
    assert prerequisites_of("gate-commit") == [
        "secrets",
        "lint-check",
        "openapi-check",
        "test-commit",
    ]
    assert "test-contract" not in reached_from("gate-commit")
    assert recipe_of("test-commit-backend") == [
        "cd backend && uv run --no-sync pytest -q --ignore=tests/started --ignore-glob='*/slow'"
    ]
    assert any("frontend test:commit" in line for line in recipe_of("test-commit-frontend"))


def test_the_full_suite_keeps_all_default_tests_contracts_and_build() -> None:
    assert {"gate", "test-fast", "test-contract", "build"} <= reached_from("suite")
    assert set(prerequisites_of("gate-commit")) - {"test-commit"} <= reached_from("suite")
    assert "cd backend && uv run pytest -q" in recipe_of("test-fast")
    assert "pnpm -C frontend test" in recipe_of("test-fast")


def test_neither_gate_nor_suite_reaches_an_opt_in_tier() -> None:
    reached = reached_from("gate-commit") | reached_from("suite")
    assert not reached.intersection(OPT_IN_TIER)
