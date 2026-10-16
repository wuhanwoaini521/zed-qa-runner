import time


class TestCart:
    """Also uses account A: TestLogin and TestCart must not run in parallel."""

    def test_add(self):
        assert True

    def test_remove(self):
        assert True

    def test_slow(self):
        time.sleep(1.2)
        assert True
