"""Shared Home Assistant test fixtures."""

import pytest


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Allow the polling timer created by CoverCompass's required Sun dependency."""
    return True
