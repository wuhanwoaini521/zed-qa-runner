"""Shared fixtures for the demo project.

Only local `data:` pages are used, so nothing depends on an external site.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def extra_zedqa_args():
    """A fixture that already exists in the project; zedqa must not fight it."""
    return {"headless": True}


@pytest.fixture
def account():
    return "account:A"
