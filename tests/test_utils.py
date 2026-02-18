"""Unit tests for utility functions."""

import pytest
from azure_local_deploy.utils import require_keys


def test_require_keys_passes():
    require_keys({"a": 1, "b": 2}, ["a", "b"])


def test_require_keys_missing():
    with pytest.raises(ValueError, match="Missing required config keys"):
        require_keys({"a": 1}, ["a", "b", "c"])


def test_require_keys_none_value():
    with pytest.raises(ValueError, match="Missing"):
        require_keys({"a": None}, ["a"])


def test_require_keys_context():
    with pytest.raises(ValueError, match="test context"):
        require_keys({}, ["x"], context="test context")
