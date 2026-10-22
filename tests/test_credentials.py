import os
from types import SimpleNamespace

import keyring
import pytest

from stitch_lineage.config import load_config
from stitch_lineage.io import credentials
from stitch_lineage.io.credentials import _backend as select_backend

KEY = "synthetic-secret"
URL = "https://mb.example.com"
VAR = "STITCH_METABASE_API_KEY"


def config(tmp_path, url=URL):
    path = tmp_path / "stitch.yml"
    path.write_text(
        f"metabase:\n  url: {url}\n  api_key: ${{{VAR}}}\n"
        "  databases: [{metabase_name: Analytics, dbt_database: analytics}]\n"
    )
    return path


def test_saved_key_restores_only_when_network_credentials_are_needed(tmp_path, credential_backend):
    path = config(tmp_path)
    credentials.save_api_key(path, URL, VAR, KEY)
    before = credential_backend.reads
    cfg = load_config(path, env={})
    assert credential_backend.reads == before
    assert cfg.metabase.missing_env == [VAR]
    credentials.restore_api_key(cfg)
    assert cfg.metabase.api_key == KEY
    cfg.metabase.require_env()
    assert KEY not in repr(cfg)
    assert KEY not in path.read_text()


def test_environment_key_wins_without_opening_keychain(tmp_path, credential_backend):
    path = config(tmp_path)
    credentials.save_api_key(path, URL, VAR, "saved-key")
    before = credential_backend.reads
    cfg = load_config(path, env={VAR: "ci-key"})
    credentials.restore_api_key(cfg)
    assert cfg.metabase.api_key == "ci-key"
    assert credential_backend.reads == before


def test_credentials_are_scoped_to_project_server_and_variable(tmp_path):
    path = config(tmp_path)
    credentials.save_api_key(path, URL, VAR, KEY)
    assert credentials.read_api_key(path, URL + "/", VAR) == KEY
    assert credentials.read_api_key(tmp_path / "other.yml", URL, VAR) is None
    assert credentials.read_api_key(path, "https://other.example.com", VAR) is None
    assert credentials.read_api_key(path, URL, "OTHER_KEY") is None


def test_unavailable_keychain_does_not_break_offline_config(tmp_path, monkeypatch):
    path = config(tmp_path)

    def fail():
        raise RuntimeError(KEY)

    monkeypatch.setattr(credentials, "_backend", fail)
    cfg = load_config(path, env={})
    credentials.restore_api_key(cfg)
    assert cfg.metabase.missing_env == [VAR]
    with pytest.raises(credentials.CredentialStoreUnavailable) as error:
        credentials.save_api_key(path, URL, VAR, KEY)
    assert KEY not in str(error.value)


def test_os_backend_selection_rejects_plaintext_and_disabled_backends(monkeypatch):
    for module in ("keyrings.alt.file", "keyring.backends.null", "keyring.backends.fail"):
        backend = type("Backend", (), {"__module__": module})()
        monkeypatch.setattr(keyring, "get_keyring", lambda backend=backend: backend)
        with pytest.raises(credentials.CredentialStoreUnavailable):
            select_backend()


def test_os_backend_selection_never_delegates_to_a_chain_fallback(monkeypatch):
    unsafe = SimpleNamespace()
    secure = type("Backend", (), {"__module__": "keyring.backends.SecretService"})()
    chain = type("Backend", (), {"__module__": "keyring.backends.chainer"})()
    chain.backends = [unsafe, secure]
    monkeypatch.setattr(keyring, "get_keyring", lambda: chain)
    assert select_backend() is secure


def test_session_credentials_are_restored_even_after_failure(monkeypatch):
    monkeypatch.setenv(VAR, "original")
    monkeypatch.delenv("STITCH_SESSION_TEST", raising=False)
    with (
        pytest.raises(RuntimeError),
        credentials.credential_environment({VAR: KEY, "STITCH_SESSION_TEST": KEY}),
    ):
        assert os.environ[VAR] == KEY
        raise RuntimeError("build failed")
    assert os.environ[VAR] == "original"
    assert "STITCH_SESSION_TEST" not in os.environ


def test_failed_keychain_write_does_not_claim_persistence(tmp_path, monkeypatch):
    backend = SimpleNamespace(set_password=lambda *args: None, get_password=lambda *args: None)
    monkeypatch.setattr(credentials, "_backend", lambda: backend)
    with pytest.raises(credentials.CredentialStoreUnavailable):
        credentials.save_api_key(config(tmp_path), URL, VAR, KEY)
