"""The account email rule, and the plan the data migration builds from it. No database."""

from linkedin_agent.domain.accounts import normalise_address, plan_email_normalisation


def test_case_and_surrounding_spaces_are_removed() -> None:
    assert normalise_address("  Bob@Example.COM ") == "bob@example.com"


def test_the_plan_updates_only_the_rows_that_change() -> None:
    plan = plan_email_normalisation([(1, "Alice@Example.com"), (2, "bob@example.com"), (3, " carol@example.com")])
    assert plan.updates == ((1, "alice@example.com"), (3, "carol@example.com"))
    assert plan.is_safe


def test_accounts_that_would_collide_are_listed_and_nothing_is_planned_as_safe() -> None:
    plan = plan_email_normalisation([(4, "dup@example.com"), (9, "DUP@example.com "), (5, "other@example.com")])
    assert not plan.is_safe
    assert plan.duplicates == {"dup@example.com": (4, 9)}
