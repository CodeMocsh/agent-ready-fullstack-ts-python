import pytest

from app import log
from tests.conftest import Logged


@pytest.fixture
def lines(logged: Logged) -> Logged:
    log.configure(None)
    return logged
