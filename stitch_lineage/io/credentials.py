"""OS credential storage, scoped to a project, Metabase URL, and env reference.

Config parsing stays offline. Only commands that need Metabase call restore_api_key.
Environment values always win. No secret is written to a project or a fallback file.
"""

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from stitch_lineage.config import StitchConfig

_OS_BACKENDS = {
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
}


class CredentialStoreUnavailable(Exception):
    """No usable OS credential store; the current session can still continue."""


def _backend():
    import keyring

    backend = keyring.get_keyring()
    # A chain may contain third-party plaintext fallbacks. Select an OS backend
    # explicitly, so a failed secure write never silently falls through to a file.
    candidates = (
        backend.backends if type(backend).__module__ == "keyring.backends.chainer" else [backend]
    )
    for candidate in candidates:
        if type(candidate).__module__ in _OS_BACKENDS:
            return candidate
    raise CredentialStoreUnavailable("OS credential storage is unavailable")


def _account(config: Path, url: str, variable: str) -> str:
    parts = urlsplit(url)
    canonical_url = urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )
    identity = json.dumps([str(config.resolve()), canonical_url, variable])
    return hashlib.sha256(identity.encode()).hexdigest()


def read_api_key(config: Path, url: str, variable: str) -> str | None:
    try:
        return _backend().get_password("stitch-lineage", _account(config, url, variable))
    except Exception:
        # Provider errors can include backend arguments. Never print secret-bearing errors.
        raise CredentialStoreUnavailable("OS credential storage could not be read") from None


def save_api_key(config: Path, url: str, variable: str, key: str) -> None:
    try:
        backend = _backend()
        account = _account(config, url, variable)
        backend.set_password("stitch-lineage", account, key)
        if backend.get_password("stitch-lineage", account) != key:
            raise CredentialStoreUnavailable("credential was not retained")
    except Exception:
        raise CredentialStoreUnavailable("OS credential storage could not save the key") from None


def restore_api_key(cfg: StitchConfig) -> None:
    variable = cfg.metabase._api_key_env
    if (
        cfg._config_path is None
        or variable not in cfg.metabase.missing_env
        or "${" in cfg.metabase.url
    ):
        return
    try:
        key = read_api_key(cfg._config_path, cfg.metabase.url, variable)
    except CredentialStoreUnavailable:
        return
    if key:
        cfg.metabase.api_key = key
        cfg.metabase.missing_env = [name for name in cfg.metabase.missing_env if name != variable]


@contextmanager
def credential_environment(values: Mapping[str, str]) -> Iterator[None]:
    """Carry a just-entered key through init's build/serve, including headless systems."""
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
