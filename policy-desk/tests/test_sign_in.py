"""Sign-in: the modal dialog over the main window."""

import pytest

from screens import PolicyDesk, until


def test_wrong_password_is_refused(desk: PolicyDesk) -> None:
    login = desk.login
    login.sign_in("tester", "wrong")
    assert until(lambda: login.error, "the error text") == "Invalid user name or password."
    assert "sign_in_failed" in desk.record.activity_kinds()


def test_user_name_is_required(desk: PolicyDesk) -> None:
    login = desk.login
    login.sign_in("", "harbor")
    assert until(lambda: login.error, "the error text") == "Enter your user name."


def test_sign_in_shows_the_user_and_office(desk: PolicyDesk) -> None:
    desk.sign_in("tester", "harbor", office="Hartford")
    assert desk.wait_status("Signed in")
    assert "User: tester (Hartford)" in desk.status_texts()
    assert desk.record.activity_kinds()[-1] == "sign_in"


@pytest.mark.catches("no_lockout")
def test_three_wrong_passwords_lock_sign_in(desk: PolicyDesk) -> None:
    login = desk.login
    for _ in range(3):
        login.sign_in("tester", "wrong")
    assert until(lambda: login.error == "Too many attempts. Restart Policy Desk.", "the lockout message")
    assert not login.enabled("btnSignIn")
    assert desk.record.activity_kinds().count("sign_in_failed") == 3
