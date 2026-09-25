"""`app.main`: the module-level `app` is built when asked for, and not before."""

from fastapi import FastAPI

from app import main


def test_importing_the_module_builds_no_app_until_app_is_read() -> None:
    main.served.cache_clear()

    assert main.served.cache_info().currsize == 0
    assert isinstance(main.app, FastAPI)
    assert main.app is main.app
    assert main.served.cache_info().currsize == 1


def test_any_other_name_is_still_missing() -> None:
    assert not hasattr(main, "application")
