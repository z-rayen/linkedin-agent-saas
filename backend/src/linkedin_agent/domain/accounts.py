"""The rule for account email addresses, in one place: the registration, the login and the data migration use it."""

from collections.abc import Iterable
from dataclasses import dataclass


def normalise_address(raw: str) -> str:
    """Surrounding spaces and letter case do not make another account: " Bob@Example.COM " is "bob@example.com"."""
    return raw.strip().lower()


@dataclass(frozen=True)
class EmailPlan:
    """What the migration must do. `updates` are (user id, normalised address) for every row that changes. `duplicates`
    maps each normalised address that more than one account would end up with to the account ids that share it."""

    updates: tuple[tuple[int, str], ...]
    duplicates: dict[str, tuple[int, ...]]

    @property
    def is_safe(self) -> bool:
        return not self.duplicates


def plan_email_normalisation(rows: Iterable[tuple[int, str]]) -> EmailPlan:
    updates: list[tuple[int, str]] = []
    groups: dict[str, list[int]] = {}
    for user_id, raw in rows:
        normalised = normalise_address(raw)
        groups.setdefault(normalised, []).append(user_id)
        if normalised != raw:
            updates.append((user_id, normalised))
    duplicates = {address: tuple(ids) for address, ids in groups.items() if len(ids) > 1}
    return EmailPlan(updates=tuple(updates), duplicates=duplicates)
