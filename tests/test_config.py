from __future__ import annotations

import os

from quant.config import get_settings, load_env_file, reset_settings


def test_env_file_loads_values_without_overriding_process_env(tmp_path, monkeypatch):
    path = tmp_path / "quant.env"
    path.write_text(
        "TUSHARE_TOKEN=file-token\nTDX_WORKERS=6\nQUANT_RELOAD=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TUSHARE_TOKEN", "process-token")
    monkeypatch.delenv("TDX_WORKERS", raising=False)
    monkeypatch.delenv("QUANT_RELOAD", raising=False)

    load_env_file(path)

    assert os.environ["TUSHARE_TOKEN"] == "process-token"
    assert os.environ["TDX_WORKERS"] == "6"
    assert os.environ["QUANT_RELOAD"] == "true"


def test_settings_use_selected_config_file(tmp_path, monkeypatch):
    path = tmp_path / "quant.env"
    path.write_text(
        "\n".join(
            [
                "TUSHARE_TOKEN=test-token",
                "TUSHARE_WORKERS=3",
                "TDX_WORKERS=7",
                "DATA_MEMORY_BUDGET_GB=8",
                "QUANT_CORS_ORIGINS=http://localhost:5173,http://example.test",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANT_CONFIG_FILE", str(path))
    for key in (
        "TUSHARE_TOKEN",
        "TUSHARE_WORKERS",
        "TDX_WORKERS",
        "DATA_MEMORY_BUDGET_GB",
        "QUANT_CORS_ORIGINS",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_settings()

    settings = get_settings()

    assert settings.tushare.token == "test-token"
    assert settings.workers_for("tushare") == 3
    assert settings.workers_for("tdx") == 7
    assert settings.data.memory_budget_gb == 8
    assert settings.app.cors_origins[-1] == "http://example.test"
    reset_settings()
