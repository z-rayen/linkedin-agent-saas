"""'Same company' detection for the sending policy. Pure rules, no database."""

import pytest

from linkedin_agent.core.settings import get_settings
from linkedin_agent.domain.applications import company_key

FREE = get_settings().free_mail_domains


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("jobs@acme.com", "acme.com"),
        ("recruiting@sub.acme.fr", "sub.acme.fr"),
        ("JOBS@ACME.COM", "acme.com"),
        ("  hr@acme.com  ", "acme.com"),
    ],
)
def test_company_domain_identifies_the_employer(address: str, expected: str) -> None:
    assert company_key(address, FREE) == expected


@pytest.mark.parametrize(
    "address",
    [
        "jane.doe@gmail.com",
        "jane.doe@outlook.com",
        "jane.doe@yahoo.fr",
        "jane.doe@yahoo.co.uk",
        "jane.doe@gmx.de",
    ],
)
def test_free_mail_domains_fall_back_to_the_full_address(address: str) -> None:
    assert company_key(address, FREE) == address.lower()


def test_wildcard_only_matches_the_exact_provider() -> None:
    # "notyahoo.com" must not be treated as Yahoo.
    assert company_key("hr@notyahoo.com", FREE) == "notyahoo.com"


def test_two_free_mail_candidates_are_never_the_same_company() -> None:
    assert company_key("a@gmail.com", FREE) != company_key("b@gmail.com", FREE)


def test_free_mail_list_is_configurable() -> None:
    # If the list is changed, the behaviour follows it.
    assert company_key("hr@acme.com", ["acme.com"]) == "hr@acme.com"


def test_address_without_domain_is_returned_unchanged() -> None:
    assert company_key("not-an-address", FREE) == "not-an-address"
