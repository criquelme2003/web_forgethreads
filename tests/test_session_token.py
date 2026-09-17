import time

import itsdangerous
import pytest

from app.services.session_token import TokenInvalidError, issue_auth_token, verify_auth_token

SECRET = "test-secret-key-0123456789"
AUTH_PASSWORD = "s3cret-dev-password"
MAX_AGE = 14 * 24 * 60 * 60


def test_issue_and_verify_round_trip() -> None:
    token = issue_auth_token(AUTH_PASSWORD, SECRET)
    verify_auth_token(token, AUTH_PASSWORD, SECRET, max_age=MAX_AGE)  # no debe lanzar


def test_verify_rejects_tampered_signature() -> None:
    token = issue_auth_token(AUTH_PASSWORD, SECRET)
    tampered = token[:-2] + "xx"
    with pytest.raises(TokenInvalidError):
        verify_auth_token(tampered, AUTH_PASSWORD, SECRET, max_age=MAX_AGE)


def test_verify_rejects_wrong_secret() -> None:
    token = issue_auth_token(AUTH_PASSWORD, SECRET)
    with pytest.raises(TokenInvalidError):
        verify_auth_token(token, AUTH_PASSWORD, "a-different-secret-key", max_age=MAX_AGE)


def test_verify_rejects_wrong_auth_password() -> None:
    token = issue_auth_token(AUTH_PASSWORD, SECRET)
    with pytest.raises(TokenInvalidError):
        verify_auth_token(token, "not-the-real-password", SECRET, max_age=MAX_AGE)


def test_verify_rejects_expired_token() -> None:
    token = issue_auth_token(AUTH_PASSWORD, SECRET)
    time.sleep(2)
    with pytest.raises(TokenInvalidError):
        verify_auth_token(token, AUTH_PASSWORD, SECRET, max_age=0)


def test_verify_rejects_empty_token() -> None:
    with pytest.raises(TokenInvalidError):
        verify_auth_token("", AUTH_PASSWORD, SECRET, max_age=MAX_AGE)


def test_verify_rejects_garbage_token() -> None:
    with pytest.raises(TokenInvalidError):
        verify_auth_token("no-es-un-token-valido", AUTH_PASSWORD, SECRET, max_age=MAX_AGE)


def test_verify_rejects_token_signed_with_different_signer_salt() -> None:
    # token firmado con un TimestampSigner con salt distinto al que usa issue_auth_token
    signer = itsdangerous.TimestampSigner(SECRET, salt=b"otro-salt")
    foreign_token = signer.sign(AUTH_PASSWORD.encode("utf-8")).decode("utf-8")
    with pytest.raises(TokenInvalidError):
        verify_auth_token(foreign_token, AUTH_PASSWORD, SECRET, max_age=MAX_AGE)
