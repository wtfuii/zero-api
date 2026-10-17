"""Shared test fixtures: loading recorded traffic."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def load_fixture() -> "Callable[[str, str], Any]":
    """Load a recorded (sanitized) response fixture of a slice: load_fixture("account", "name")."""

    def _load(slice_name: str, name: str) -> Any:
        path = Path(__file__).parent / slice_name / "fixtures" / f"{name}.json"
        return json.loads(path.read_text())

    return _load
