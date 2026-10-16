import pytest

# Module-level counter survives pytest-rerunfailures reruns inside one worker process.
_attempts: dict[str, int] = {}


class TestSearch:
    """Uses account B; free to run in parallel with account A tests."""

    @pytest.mark.parametrize("region", ["JP", "US"])
    def test_search(self, region):
        assert region in {"JP", "US"}

    def test_locale(self):
        assert True

    def test_flaky(self):
        # Fails on the first attempt, passes on rerun -> Flaky, not Passed.
        n = _attempts.get("flaky", 0) + 1
        _attempts["flaky"] = n
        assert n >= 2, f"first attempt fails on purpose (attempt {n})"
