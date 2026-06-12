"""Общие фикстуры для тестов ядра (без сети и без Technitium)."""

from __future__ import annotations

import pytest

from ttbot.reconciler import Reconciler
from ttbot.state import StateStore


@pytest.fixture
def state(tmp_path):
    """Свежее файловое состояние во временном каталоге."""
    return StateStore(tmp_path / "state.json")


@pytest.fixture
def rec(state):
    """Reconciler для проверки чистой логики (_desired / check_domain).

    ``cfg`` и ``client`` этим методам не нужны — они работают только с ``state``.
    """
    return Reconciler(cfg=None, client=None, state=state)
