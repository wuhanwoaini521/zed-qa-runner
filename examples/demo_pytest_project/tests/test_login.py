import time

import pytest


@pytest.mark.smoke
class TestLogin:
    """Uses account A (shared with TestCart); region JP."""

    def test_success(self):
        assert 1 + 1 == 2

    def test_logout(self):
        assert True

    def test_profile(self):
        time.sleep(1.0)
        assert True

    @pytest.mark.parametrize("user", ["alice", "bob"])
    def test_login_param(self, user):
        assert user in {"alice", "bob"}

    @pytest.mark.smoke
    def test_failure(self):
        # Intentional failure: drives Failure Center / rerun / debug demos.
        assert "expected" == "actual"
