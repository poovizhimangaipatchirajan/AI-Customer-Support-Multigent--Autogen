
"""FixFlow AI — Customer and Admin Playwright UI tests.

Run:
    python -m pytest tests/e2e/test_portal_ui.py -v --browser chromium

Requires:
    FastAPI and Streamlit running locally.
    Existing customer/admin test accounts in FastAPI's database.
"""

import os

import pytest
from playwright.sync_api import Page, expect


from fixflow_test_endpoints import UI_URL

BASE_URL = UI_URL


def creds(prefix: str):
    """Read test-only credentials from environment variables."""
    email = os.getenv(f"FIXFLOW_TEST_{prefix}_EMAIL")
    password = os.getenv(f"FIXFLOW_TEST_{prefix}_PASSWORD")

    if not email or not password:
        pytest.skip(
            f"Set FIXFLOW_TEST_{prefix}_EMAIL and "
            f"FIXFLOW_TEST_{prefix}_PASSWORD"
        )

    if password.startswith("YOUR_TEST_"):
        pytest.skip(f"{prefix} password is still a placeholder")

    return email, password


def sidebar(page: Page):
    """Scope navigation to the Streamlit sidebar."""
    return page.get_by_test_id("stSidebarUserContent")


def sidebar_button(page: Page, name: str):
    """Find a sidebar button without matching dashboard duplicates."""
    return sidebar(page).get_by_role(
        "button",
        name=name,
        exact=False,
    )


def login(page: Page, email: str, password: str):
    page.goto(BASE_URL, wait_until="domcontentloaded")

    expect(
        page.get_by_text("Welcome to FixFlow AI")
    ).to_be_visible(timeout=20000)

    page.get_by_label("Email", exact=True).first.fill(email)
    page.get_by_label("Password", exact=True).first.fill(password)

    page.get_by_role(
        "button", name="Login", exact=True
    ).click()

    try:
        expect(
            sidebar_button(page, "Log out")
        ).to_be_visible(timeout=20000)

    except AssertionError:
        alerts = page.get_by_role("alert").all_text_contents()
        raise AssertionError(
            "Login did not reach the dashboard. "
            f"Visible alerts: {alerts}"
        ) from None


def logout(page: Page):
    sidebar_button(page, "Log out").click()

    expect(
        page.get_by_text("Welcome to FixFlow AI")
    ).to_be_visible(timeout=20000)

    expect(
        page.get_by_role("button", name="Login", exact=True)
    ).to_be_visible()


def test_login_page(page: Page):
    """Check the login and registration tabs."""
    page.goto(BASE_URL, wait_until="domcontentloaded")

    expect(
        page.get_by_text("Welcome to FixFlow AI")
    ).to_be_visible(timeout=20000)

    expect(
        page.get_by_role("button", name="Login", exact=True)
    ).to_be_visible()

    register_tab = page.get_by_role("tab", name="Register")

    expect(register_tab).to_be_visible(timeout=15000)
    register_tab.click(timeout=15000)

    expect(
        page.get_by_role("button", name="Create account")
    ).to_be_visible(timeout=15000)


def test_customer_navigation_and_logout(page: Page):
    """Customer can access customer pages, but not admin navigation."""
    email, password = creds("CUSTOMER")
    login(page, email, password)

    customer_pages = [
        "Home",
        "My tickets",
        "New complaint",
        "Help center",
        "Account",
    ]

    for name in customer_pages:
        expect(
            sidebar_button(page, name)
        ).to_be_visible(timeout=10000)

    # Admin-only navigation must not appear for customers.
    for name in ["Escalations", "Audit logs"]:
        expect(
            sidebar_button(page, name)
        ).to_have_count(0)

    sidebar_button(page, "My tickets").click()
    expect(
        page.get_by_text("Select a ticket to see progress")
    ).to_be_visible(timeout=15000)

    sidebar_button(page, "New complaint").click()
    expect(
        page.get_by_text("Raise a new complaint")
    ).to_be_visible(timeout=15000)

    sidebar_button(page, "Help center").click()
    expect(
        page.get_by_text("NovaCart Help Center")
    ).to_be_visible(timeout=15000)

    sidebar_button(page, "Account").click()
    expect(
        page.get_by_text("Your local NovaCart support profile")
    ).to_be_visible(timeout=15000)

    logout(page)


def test_admin_navigation_and_logout(page: Page):
    """Admin can access admin pages, but not customer complaint navigation."""
    email, password = creds("ADMIN")
    login(page, email, password)

    admin_pages = [
        "Dashboard",
        "Tickets",
        "Escalations",
        "Audit logs",
    ]

    for name in admin_pages:
        expect(
            sidebar_button(page, name)
        ).to_be_visible(timeout=10000)

    # Customer-only navigation must not appear for admins.
    for name in ["New complaint", "Help center", "My tickets"]:
        expect(
            sidebar_button(page, name)
        ).to_have_count(0)

    sidebar_button(page, "Escalations").click()
    expect(
        page.get_by_text("Review requests requiring human attention")
    ).to_be_visible(timeout=15000)

    # Audit logs and Export share the complaint-log implementation.
    sidebar_button(page, "Audit logs").click()
    expect(
        page.get_by_text("Section 7 complaint log · Admin-only")
    ).to_be_visible(timeout=15000)

    logout(page)


def test_registration_rejects_invalid_fields(page: Page):
    """Registration form rejects invalid data without creating an account."""
    page.goto(BASE_URL, wait_until="domcontentloaded")

    register_tab = page.get_by_role("tab", name="Register")
    expect(register_tab).to_be_visible(timeout=15000)
    register_tab.click()

    page.get_by_label("Full name *").fill("A")
    page.get_by_label("Email *").fill("invalid-email")
    page.get_by_label("Confirm password *", exact=True).fill("different")
    page.get_by_label("Confirm password *").fill("different")

    page.get_by_role(
        "button", name="Create account"
    ).click()

    expect(
        page.get_by_text(
            "Full name: enter 2–100 valid characters",
            exact=False,
        )
    ).to_be_visible(timeout=15000)

    expect(
        page.get_by_text(
            "Email: enter a valid email address",
            exact=False,
        )
    ).to_be_visible()

    expect(
        page.get_by_text(
            "Confirm password: passwords do not match",
            exact=False,
        )
    ).to_be_visible()
