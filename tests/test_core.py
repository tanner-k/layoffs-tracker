import pytest

from layoffs_tracker.core import greet


def test_greet_trims_name() -> None:
    assert greet("  Ada ") == "Hello, Ada!"


def test_greet_rejects_blank_name() -> None:
    with pytest.raises(ValueError, match="blank"):
        greet("   ")
