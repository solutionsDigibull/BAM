"""The master keyring of a running process, from settings (ADR 0030, decision 2)."""

from sbam_api.connections.crypto import KeyRing, SecretsKeyError
from sbam_api.core.settings import Settings, deployed_env


def keyring_from_settings(settings: Settings) -> KeyRing:
    """Settings already refused an unusable keyring at start-up (fail-fast outside development), so
    this only fails where development runs without keys: then nothing can be stored."""
    if settings.secrets_keys is None or not settings.secrets_keys.get_secret_value().strip():
        raise SecretsKeyError("SECRETS_KEYS is not set: connection values cannot be stored")
    return KeyRing.parse(
        settings.secrets_keys.get_secret_value(),
        allow_placeholder=not deployed_env(settings.sbam_env),
    )
