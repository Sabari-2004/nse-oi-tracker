from config.settings import Settings


def test_cors_is_same_origin_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_OI_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("NSE_OI_CORS_ORIGINS", raising=False)

    assert Settings.from_environment().cors_origins == ()


def test_cors_parses_explicit_origins_only(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_OI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "NSE_OI_CORS_ORIGINS",
        " https://one.example/ , https://two.example ",
    )

    assert Settings.from_environment().cors_origins == (
        "https://one.example",
        "https://two.example",
    )


def test_startup_backfill_defaults_on_and_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_OI_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("NSE_OI_STARTUP_BACKFILL", raising=False)
    assert Settings.from_environment().startup_backfill is True
    monkeypatch.setenv("NSE_OI_STARTUP_BACKFILL", "0")
    assert Settings.from_environment().startup_backfill is False
