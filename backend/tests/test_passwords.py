"""Password hashing with the bcrypt library directly: the hashes already stored must still verify, and a long password is
never confused with its first 72 bytes."""

from linkedin_agent import auth_utils

# Produced by the previous passlib-based hashing (bcrypt, cost 4, the $2b$ format). It must keep verifying.
PASSLIB_HASH = "$2b$04$2wtqFSOWGHDBL5fexMz7K.spzyxCArJvzXRMZs1m.TD8SSLJTDYMq"


def test_a_hash_stored_before_the_change_still_verifies() -> None:
    assert auth_utils.verify_password("compat-password-1234", PASSLIB_HASH)
    assert not auth_utils.verify_password("another-password-1234", PASSLIB_HASH)


def test_a_new_hash_is_bcrypt_in_the_same_format() -> None:
    hashed = auth_utils.hash_password("fresh-password-1234")
    assert hashed.startswith("$2b$")
    assert auth_utils.verify_password("fresh-password-1234", hashed)


def test_a_password_over_the_byte_limit_never_matches_its_prefix() -> None:
    long_password = "x" * 80
    hashed = auth_utils.hash_password("x" * 72)
    assert not auth_utils.verify_password(long_password, hashed)
    assert auth_utils.verify_password("x" * 72, hashed)


def test_a_malformed_stored_hash_does_not_raise() -> None:
    assert auth_utils.verify_password("anything-1234", "not-a-hash") is False
