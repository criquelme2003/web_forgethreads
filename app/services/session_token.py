import itsdangerous

SESSION_COOKIE_NAME = "session"


class TokenInvalidError(Exception):
    pass


def issue_auth_token(auth_password: str, secret_key: str) -> str:
    """
    Genera el --auth-token que se pasa a new_job.sh/notifier.sh: firma auth_password con un
    TimestampSigner propio (independiente del cookie de sesión de Starlette). Autoverificable:
    quien tenga secret_key puede validar que el token viene de un usuario que conoce
    auth_password, sin necesitar estado compartido ni cookies de browser.
    """
    signer = itsdangerous.TimestampSigner(str(secret_key))
    return signer.sign(auth_password.encode("utf-8")).decode("utf-8")


def verify_auth_token(token: str, auth_password: str, secret_key: str, max_age: int) -> None:
    """
    Verifica que `token` fue emitido por issue_auth_token con el mismo secret_key, y que el
    valor firmado coincide con auth_password. Lanza TokenInvalidError si la firma es inválida,
    expiró, o el valor firmado no coincide con auth_password.
    """
    if not token:
        raise TokenInvalidError("Token vacío")
    signer = itsdangerous.TimestampSigner(str(secret_key))
    try:
        value = signer.unsign(token.encode("utf-8"), max_age=max_age)
    except itsdangerous.exc.BadSignature as e:
        raise TokenInvalidError("Firma inválida o token expirado") from e
    if value.decode("utf-8") != auth_password:
        raise TokenInvalidError("Token no corresponde a auth_password")
