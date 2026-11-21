"""The concurrency key of a preparation run. Pure: no database.

An empty recipient or job title must never produce a key that unrelated applications share. Separators inside a value
must never make two different triples collide.
"""

import pytest

from linkedin_agent.core.exceptions import ValidationFailedError
from linkedin_agent.services.preparations import lock_key_for


@pytest.mark.parametrize("recipient", ["", "   ", "\t\n"])
def test_a_blank_recipient_is_refused_instead_of_locking_on_nothing(recipient: str) -> None:
    with pytest.raises(ValidationFailedError, match="recipient_email"):
        lock_key_for(1, recipient, "ML Intern")


@pytest.mark.parametrize("title", ["", "   "])
def test_a_blank_job_title_is_refused(title: str) -> None:
    with pytest.raises(ValidationFailedError, match="job_title"):
        lock_key_for(1, "hr@acme.com", title)


def test_case_and_surrounding_or_repeated_spaces_do_not_change_the_key() -> None:
    a = lock_key_for(1, " HR@Acme.com ", "ML   Intern")
    b = lock_key_for(1, "hr@acme.com", "ml intern")
    assert a == b


def test_a_separator_inside_a_value_cannot_make_two_applications_collide() -> None:
    # Before the fix the raw string was "1|x|y|z" for both triples below.
    assert lock_key_for(1, "x|y", "z") != lock_key_for(1, "x", "y|z")


def test_another_user_with_the_same_application_gets_a_different_key() -> None:
    assert lock_key_for(1, "hr@acme.com", "ML Intern") != lock_key_for(2, "hr@acme.com", "ML Intern")


def test_the_key_is_a_sha256_hex_digest() -> None:
    key = lock_key_for(1, "hr@acme.com", "ML Intern")
    assert len(key) == 64
    int(key, 16)  # hexadecimal
