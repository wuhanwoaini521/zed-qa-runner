import pytest


@pytest.mark.playwright
class TestCheckout:
    """Real Chromium tests against local `data:` pages (account C)."""

    def test_open_page(self, page):
        page.set_content("<h1 id='title'>Checkout</h1>")
        assert page.text_content("#title") == "Checkout"

    def test_click(self, page):
        page.set_content("<button id='go'>go</button>")
        page.click("#go")
        assert page.is_visible("#go")

    def test_pw_failure(self, page):
        # Intentional failure so a screenshot is produced for the report.
        page.set_content("<div id='ok'>ready</div>")
        assert page.text_content("#ok") == "not-ready"
