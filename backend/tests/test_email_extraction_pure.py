"""Recipient extraction from job pages: what must be found, and what must never be taken for an address."""

import pytest

from linkedin_agent.services.apply_service import find_application_email


@pytest.mark.parametrize(
    "html",
    [
        '<img src="logo@2x.png">',
        "icon@3x.webp and banner@2x.jpg",
        "sprite@1.5x.jpg",
        "bg@2x.svg",
        "<style>@font-face{src:url(font@2x.woff2)}</style>",
    ],
)
def test_image_and_asset_names_are_not_addresses(html: str) -> None:
    assert find_application_email(html) is None


@pytest.mark.parametrize(
    "html",
    [
        "contact support@sentry.io for errors",
        "reply to noreply@acme.com",
        "donotreply@acme.com",
        "example.com placeholder: you@example.com",
    ],
)
def test_system_and_placeholder_addresses_are_not_addresses(html: str) -> None:
    assert find_application_email(html) is None


def test_obfuscated_address_is_not_guessed() -> None:
    # Only addresses written in full are taken. Reconstructing "jobs [at] acme.com" would be a guess.
    assert find_application_email("write to jobs [at] acme.com") is None


def test_recruitment_address_is_preferred_over_generic_one() -> None:
    html = "info@acme.com or careers@acme.com"
    assert find_application_email(html) == "careers@acme.com"


def test_trailing_punctuation_and_case_are_normalised() -> None:
    assert find_application_email("Apply to Jobs@Acme.com.") == "jobs@acme.com"


def test_duplicate_addresses_are_reported_once() -> None:
    assert find_application_email("hr@acme.com ... HR@acme.com") == "hr@acme.com"
