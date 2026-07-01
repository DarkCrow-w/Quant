from __future__ import annotations

import threading
import time
from datetime import date, datetime

import pandas as pd

from quant.data import updater
from quant.data.feeds.tushare import (
    TushareRateLimitError,
    TushareSource,
    _HttpTushareClient,
)
from quant.data.store import DataStore
from server.services.data_job_service import DataJobManager
import server.services.data_job_service as data_jobs


def _seed_one_bar(store: DataStore, symbol: str = "600000") -> None:
    store.upsert_kline(
        symbol,
        pd.DataFrame(
            [
                {
                    "dt": date(2024, 1, 5),
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100000.0,
                    "amount": 1000000.0,
                }
            ]
        ),
        freq="day",
        source="seed",
        recompute_indicators=False,
    )


def _bars(start: date = date(2024, 1, 5)) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "dt": start,
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 100000.0,
                "amount": 1000000.0,
            }
        ]
    )


def test_update_symbol_skips_cache_on_weekend_without_provider_call(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    _seed_one_bar(store)
    monkeypatch.setattr(updater, "get_store", lambda: store)

    def fail_fetch(*args, **kwargs):
        raise AssertionError("provider should not be called for complete cache")

    monkeypatch.setattr(updater, "_fetch_daily_bounded", fail_fetch)

    result = updater.update_symbol("600000", end_date="2024-01-06", source="tdx")

    assert result["status"] == "up_to_date"
    assert result["cached"] is True
    assert result["end"] == "2024-01-05"


def test_download_all_marks_cached_symbols_skipped_before_queueing(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    _seed_one_bar(store)
    monkeypatch.setattr(updater, "get_store", lambda: store)

    def fail_update_symbol(*args, **kwargs):
        raise AssertionError("cached symbol should not enter download queue")

    monkeypatch.setattr(updater, "update_symbol", fail_update_symbol)
    progress: list[tuple[int, int, str, str]] = []

    result = updater.download_all_a(
        end_date="2024-01-06",
        source="tdx",
        max_workers=1,
        symbols_info=[{"symbol": "600000"}],
        on_progress=lambda done, total, symbol, status: progress.append(
            (done, total, symbol, status)
        ),
    )

    assert result["total"] == 1
    assert result["success"] == 0
    assert result["skipped"] == 1
    assert result["failed"] == 0
    assert progress == [(1, 1, "600000", "skipped")]


def test_target_trade_date_uses_previous_session_before_daily_publish(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    calendar_path = store.meta_path("trade_calendar")
    calendar_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"dt": date(2026, 6, 30), "is_open": True},
            {"dt": date(2026, 7, 1), "is_open": True},
        ]
    ).to_parquet(calendar_path, index=False)

    class BeforePublishDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 1, 9, 30)

    monkeypatch.setattr(updater, "datetime", BeforePublishDatetime)

    assert updater._target_trade_date(store, "2026-07-01") == date(2026, 6, 30)


def test_target_trade_date_allows_today_after_daily_publish(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    calendar_path = store.meta_path("trade_calendar")
    calendar_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"dt": date(2026, 6, 30), "is_open": True},
            {"dt": date(2026, 7, 1), "is_open": True},
        ]
    ).to_parquet(calendar_path, index=False)

    class AfterPublishDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 1, 18, 30)

    monkeypatch.setattr(updater, "datetime", AfterPublishDatetime)

    assert updater._target_trade_date(store, "2026-07-01") == date(2026, 7, 1)


def test_tushare_batch_failure_splits_instead_of_failing_whole_group(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    monkeypatch.setattr(updater, "get_store", lambda: store)

    class FakeTushareSource:
        def __init__(self, checkpoint=None, request_progress=None):
            self.checkpoint = checkpoint or (lambda: None)
            self.request_progress = request_progress

        def fetch_daily_many(self, symbols, start, end):
            symbols = list(symbols)
            if len(symbols) > 1:
                raise RuntimeError("batch rate limited")
            return {symbols[0]: _bars()}

    monkeypatch.setattr(updater, "TushareSource", FakeTushareSource)

    rows = updater._update_symbols_tushare_batch(
        ["600000", "000001"],
        end_date="2024-01-05",
        max_workers=1,
        store=store,
    )

    assert [row["status"] for row in rows] == ["updated", "updated"]
    assert store.get_last_date("600000", "day") == date(2024, 1, 5)
    assert store.get_last_date("000001", "day") == date(2024, 1, 5)


def test_tushare_full_market_download_streams_small_chunks(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    monkeypatch.setattr(updater, "get_store", lambda: store)
    monkeypatch.setattr(updater, "_TUSHARE_UPDATE_CHUNK_SIZE", 2)
    call_sizes: list[int] = []

    class FakeTushareSource:
        def __init__(self, checkpoint=None, request_progress=None):
            self.checkpoint = checkpoint or (lambda: None)
            self.request_progress = request_progress

        def fetch_daily_many(self, symbols, start, end):
            symbols = list(symbols)
            call_sizes.append(len(symbols))
            return {symbol: _bars() for symbol in symbols}

    monkeypatch.setattr(updater, "TushareSource", FakeTushareSource)
    progress: list[tuple[int, int, str, str]] = []

    rows = updater._update_symbols_tushare_batch(
        ["600000", "000001", "000002", "000003", "000004"],
        end_date="2024-01-05",
        max_workers=1,
        store=store,
        on_progress=lambda done, total, symbol, status: progress.append(
            (done, total, symbol, status)
        ),
    )

    assert call_sizes == [2, 2, 1]
    assert len(rows) == 5
    assert [row["status"] for row in rows] == ["updated"] * 5
    assert progress[-1] == (5, 5, "000004", "updated")


def test_tushare_download_job_continues_after_cached_symbols(tmp_path, monkeypatch):
    store = DataStore(tmp_path / "store")
    _seed_one_bar(store)
    monkeypatch.setattr(updater, "get_store", lambda: store)
    monkeypatch.setattr(data_jobs, "get_store", lambda: store)
    monkeypatch.setattr(
        updater,
        "_target_trade_date",
        lambda store_arg, end_date=None: date(2024, 1, 5),
    )
    monkeypatch.setattr(updater, "_TUSHARE_UPDATE_CHUNK_SIZE", 2)
    calls: list[tuple[list[str], str, str]] = []

    class FakeTushareSource:
        def __init__(self, checkpoint=None, request_progress=None):
            self.checkpoint = checkpoint or (lambda: None)
            self.request_progress = request_progress

        def fetch_daily_many(self, symbols, start, end):
            symbols = list(symbols)
            calls.append((symbols, start, end))
            if self.request_progress is not None:
                self.request_progress("daily", 1, 1)
            return {symbol: _bars() for symbol in symbols}

    monkeypatch.setattr(updater, "TushareSource", FakeTushareSource)

    manager = DataJobManager(tmp_path / "jobs.sqlite")
    job_id = manager.start(
        "download",
        source="tushare",
        symbols=["600000", "000001", "000002"],
        workers=1,
    )["job"]["id"]
    deadline = time.monotonic() + 5
    latest = None
    while time.monotonic() < deadline:
        latest = manager.get(job_id)
        if latest and not latest["running"]:
            break
        time.sleep(0.02)

    assert latest is not None
    assert latest["status"] == "completed"
    assert latest["completed"] == 3
    assert latest["percent"] == 100.0
    assert latest["failed"] == 0
    assert latest["result"]["skipped"] == 1
    assert latest["result"]["success"] == 2
    assert calls == [(["000001", "000002"], "20210105", "20240105")]


def test_tushare_whole_market_download_refreshes_remote_universe(tmp_path, monkeypatch):
    store = DataStore(tmp_path / "store")
    _seed_one_bar(store)
    pd.DataFrame(
        [{"symbol": "600000", "name": "cached", "market": "SH"}]
    ).to_parquet(store.meta_path("symbols"), index=False)
    monkeypatch.setattr(data_jobs, "get_store", lambda: store)

    def fake_fetch_all(source):
        assert source == "tushare"
        return [
            {"symbol": "600000", "name": "cached", "market": "SH"},
            {"symbol": "000001", "name": "remote", "market": "SZ"},
            {"symbol": "000002", "name": "remote2", "market": "SZ"},
            {"symbol": "301571", "name": "国科天成", "market": "SZ"},
            {"symbol": "920001", "name": "BJ stock", "market": "BJ"},
        ]

    monkeypatch.setattr(data_jobs, "fetch_all_a_symbols", fake_fetch_all)

    symbols, origin = data_jobs._local_download_symbols("tushare")

    assert origin == "remote:tushare"
    assert symbols == ["600000", "000001", "000002", "301571", "920001"]
    assert set(store.get_universe()["symbol"]) == {
        "600000",
        "000001",
        "000002",
        "301571",
        "920001",
    }


def test_download_target_keeps_all_a_share_prefixes_from_local_universe(tmp_path, monkeypatch):
    store = DataStore(tmp_path / "store")
    local = [
        {"symbol": "600000", "name": "SH", "market": "SH"},
        {"symbol": "301571", "name": "国科天成", "market": "SZ"},
        {"symbol": "920001", "name": "BJ", "market": "BJ"},
        {"symbol": "510300", "name": "ETF", "market": "SH"},
    ]
    repeats = [
        {"symbol": f"{600000 + index:06d}", "name": f"local-{index}", "market": "SH"}
        for index in range(1000)
    ]
    pd.DataFrame(local + repeats).to_parquet(store.meta_path("symbols"), index=False)
    monkeypatch.setattr(data_jobs, "get_store", lambda: store)

    symbols, origin = data_jobs._local_download_symbols("tushare")

    assert origin == "symbols.parquet"
    assert "301571" in symbols
    assert "920001" in symbols
    assert "510300" not in symbols


def test_remote_universe_persist_merges_instead_of_dropping_existing_bj(tmp_path, monkeypatch):
    store = DataStore(tmp_path / "store")
    pd.DataFrame(
        [
            {"symbol": "920001", "name": "BJ", "market": "BJ"},
            {"symbol": "301571", "name": "old", "market": "SZ"},
        ]
    ).to_parquet(store.meta_path("symbols"), index=False)
    monkeypatch.setattr(data_jobs, "get_store", lambda: store)

    data_jobs._persist_universe_rows(
        [
            {"symbol": "600000", "name": "SH", "market": "SH"},
            {"symbol": "301571", "name": "国科天成", "market": "SZ"},
        ]
    )

    universe = store.get_universe()
    by_symbol = {
        str(row["symbol"]).zfill(6): row
        for row in universe.to_dict("records")
    }
    assert set(by_symbol) == {"920001", "301571", "600000"}
    assert by_symbol["301571"]["name"] == "国科天成"


def test_tushare_whole_market_download_uses_large_local_universe_without_remote_call(tmp_path, monkeypatch):
    store = DataStore(tmp_path / "store")
    local = [
        {"symbol": f"{600000 + index:06d}", "name": f"local-{index}", "market": "SH"}
        for index in range(1000)
    ]
    pd.DataFrame(local).to_parquet(store.meta_path("symbols"), index=False)
    monkeypatch.setattr(data_jobs, "get_store", lambda: store)

    def fail_fetch_all(source):
        raise AssertionError(f"remote universe should not be fetched, got {source}")

    monkeypatch.setattr(data_jobs, "fetch_all_a_symbols", fail_fetch_all)

    symbols, origin = data_jobs._local_download_symbols("tushare")

    assert origin == "symbols.parquet"
    assert len(symbols) == 1000
    assert symbols[:2] == ["600000", "600001"]


def test_tushare_symbol_list_falls_back_to_cached_universe(tmp_path, monkeypatch):
    store = DataStore(tmp_path / "store")
    pd.DataFrame(
        [
            {"symbol": "600000", "name": "浦发银行", "market": "SH"},
            {"symbol": "000001", "name": "平安银行", "market": "SZ"},
        ]
    ).to_parquet(store.meta_path("symbols"), index=False)
    monkeypatch.setattr(updater, "get_store", lambda: store)

    class EmptyTushareSource:
        def list_symbols(self):
            return []

    monkeypatch.setattr(updater, "TushareSource", EmptyTushareSource)

    rows = updater.fetch_all_a_symbols_tushare()

    assert [row["symbol"] for row in rows] == ["600000", "000001"]


def test_tushare_download_job_starts_from_large_local_universe_without_remote_call(tmp_path, monkeypatch):
    store = DataStore(tmp_path / "store")
    local = [
        {"symbol": f"{600000 + index:06d}", "name": f"local-{index}", "market": "SH"}
        for index in range(1000)
    ]
    pd.DataFrame(local).to_parquet(store.meta_path("symbols"), index=False)
    monkeypatch.setattr(data_jobs, "get_store", lambda: store)

    def fail_fetch_all(source):
        raise AssertionError(f"remote universe should not be fetched, got {source}")

    def fake_download_all_a(**kwargs):
        symbols_info = list(kwargs["symbols_info"])
        progress = kwargs.get("on_progress")
        if progress is not None:
            progress(1, len(symbols_info), symbols_info[0]["symbol"], "skipped")
            progress(len(symbols_info), len(symbols_info), symbols_info[-1]["symbol"], "skipped")
        return {
            "total": len(symbols_info),
            "success": 0,
            "skipped": len(symbols_info),
            "failed": 0,
            "errors": [],
        }

    monkeypatch.setattr(data_jobs, "fetch_all_a_symbols", fail_fetch_all)
    monkeypatch.setattr(data_jobs, "download_all_a", fake_download_all_a)

    manager = DataJobManager(tmp_path / "jobs.sqlite")
    started = manager.start("download", source="tushare", workers=1)
    job_id = started["job"]["id"]

    deadline = time.monotonic() + 5
    latest = None
    while time.monotonic() < deadline:
        latest = manager.get(job_id)
        if latest and not latest["running"]:
            break
        time.sleep(0.02)

    assert started["job"]["total"] == 1000
    assert started["job"]["result"]["universe_origin"] == "symbols.parquet"
    assert latest is not None
    assert latest["status"] == "completed"
    assert latest["completed"] == 1000
    assert latest["result"]["skipped"] == 1000


def test_tushare_download_job_resolves_small_universe_in_background(tmp_path, monkeypatch):
    store = DataStore(tmp_path / "store")
    _seed_one_bar(store)
    pd.DataFrame(
        [{"symbol": "600000", "name": "cached", "market": "SH"}]
    ).to_parquet(store.meta_path("symbols"), index=False)
    monkeypatch.setattr(updater, "get_store", lambda: store)
    monkeypatch.setattr(data_jobs, "get_store", lambda: store)
    monkeypatch.setattr(
        updater,
        "_target_trade_date",
        lambda store_arg, end_date=None: date(2024, 1, 5),
    )

    remote_started = threading.Event()
    allow_remote = threading.Event()

    def fake_fetch_all(source):
        remote_started.set()
        assert source == "tushare"
        assert allow_remote.wait(timeout=2)
        return [
            {"symbol": "600000", "name": "cached", "market": "SH"},
            {"symbol": "000001", "name": "remote", "market": "SZ"},
            {"symbol": "920001", "name": "remote-bj", "market": "BJ"},
        ]

    class FakeTushareSource:
        def __init__(self, checkpoint=None, request_progress=None):
            self.checkpoint = checkpoint or (lambda: None)
            self.request_progress = request_progress

        def fetch_daily_many(self, symbols, start, end):
            return {symbol: _bars() for symbol in symbols}

    monkeypatch.setattr(data_jobs, "fetch_all_a_symbols", fake_fetch_all)
    monkeypatch.setattr(updater, "TushareSource", FakeTushareSource)

    manager = DataJobManager(tmp_path / "jobs.sqlite")
    started = manager.start("download", source="tushare", workers=1)
    job_id = started["job"]["id"]

    assert started["status"] == "started"
    assert started["job"]["total"] == 0
    assert started["job"]["result"]["universe_origin"] == "background:remote"
    assert remote_started.wait(timeout=2)

    running = manager.get(job_id)
    assert running is not None
    assert running["running"] is True
    assert running["current_symbol"] == "UNIVERSE"
    assert running["current_status"] == "running"

    allow_remote.set()
    deadline = time.monotonic() + 5
    latest = None
    while time.monotonic() < deadline:
        latest = manager.get(job_id)
        if latest and not latest["running"]:
            break
        time.sleep(0.02)

    assert latest is not None
    assert latest["status"] == "completed"
    assert latest["completed"] == 3
    assert latest["result"]["universe_origin"] == "remote:tushare"
    assert latest["result"]["skipped"] == 1
    assert latest["result"]["success"] == 2


def test_download_all_returns_immediately_when_all_targets_cached(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    _seed_one_bar(store)
    monkeypatch.setattr(updater, "get_store", lambda: store)

    class FailTushareSource:
        def __init__(self, *args, **kwargs):
            raise AssertionError("no provider should be created for all-cached targets")

    monkeypatch.setattr(updater, "TushareSource", FailTushareSource)
    progress: list[tuple[int, int, str, str]] = []

    result = updater.download_all_a(
        end_date="2024-01-06",
        source="tushare",
        symbols_info=[{"symbol": "600000"}],
        on_progress=lambda done, total, symbol, status: progress.append(
            (done, total, symbol, status)
        ),
    )

    assert result == {
        "total": 1,
        "success": 0,
        "skipped": 1,
        "failed": 0,
        "errors": [],
    }
    assert progress == [(1, 1, "600000", "skipped")]


def test_download_all_respects_empty_symbols_info_without_remote_fetch(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    monkeypatch.setattr(updater, "get_store", lambda: store)

    def fail_fetch_all(source):
        raise AssertionError("empty explicit targets should not fetch remote universe")

    monkeypatch.setattr(updater, "fetch_all_a_symbols", fail_fetch_all)

    result = updater.download_all_a(
        end_date="2024-01-05",
        source="tushare",
        symbols_info=[],
    )

    assert result == {
        "total": 0,
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }


def test_tushare_batch_returns_cached_rows_without_provider(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    _seed_one_bar(store)

    class FailTushareSource:
        def __init__(self, *args, **kwargs):
            raise AssertionError("cached batch should not create tushare provider")

    monkeypatch.setattr(updater, "TushareSource", FailTushareSource)
    progress: list[tuple[int, int, str, str]] = []

    rows = updater._update_symbols_tushare_batch(
        ["600000"],
        end_date="2024-01-06",
        store=store,
        on_progress=lambda done, total, symbol, status: progress.append(
            (done, total, symbol, status)
        ),
    )

    assert rows[0]["status"] == "up_to_date"
    assert rows[0]["cached"] is True
    assert progress == [(1, 1, "600000", "up_to_date")]


def test_tushare_request_progress_during_batch_fetch(monkeypatch):
    class FakePro:
        def trade_cal(self, **kwargs):
            return pd.DataFrame(
                [
                    {"cal_date": "20240102"},
                    {"cal_date": "20240103"},
                ]
            )

        def daily(self, **kwargs):
            if "trade_date" in kwargs:
                return pd.DataFrame(
                    [
                        {
                            "ts_code": "600000.SH",
                            "trade_date": kwargs["trade_date"],
                            "open": 10.0,
                            "high": 10.5,
                            "low": 9.8,
                            "close": 10.2,
                            "vol": 1000.0,
                            "amount": 1000.0,
                        }
                    ]
                )
            raise AssertionError("expected date-batch request")

    monkeypatch.setattr(
        "quant.data.feeds.tushare.TUSHARE_SAFE_ROWS",
        1,
    )
    progress: list[tuple[str, int, int]] = []

    source = TushareSource(
        pro=FakePro(),
        request_interval=0,
        retries=0,
        request_progress=lambda api, done, total: progress.append(
            (api, done, total)
        ),
    )

    rows = source.fetch_daily_many(
        ["600000", "000001", "000002"],
        "20240102",
        "20240103",
    )

    assert progress == [
        ("trade_cal", 0, 1),
        ("trade_cal", 1, 1),
        ("daily", 1, 2),
        ("daily", 2, 2),
    ]
    assert not rows["600000"].empty


def test_tushare_http_client_uses_timeout_and_parses_dataframe():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 0,
                "msg": "",
                "data": {
                    "fields": ["ts_code", "trade_date", "close"],
                    "items": [["600000.SH", "20240105", 10.2]],
                },
            }

    class FakeSession:
        def __init__(self):
            self.calls = []

        def post(self, url, json, timeout):
            self.calls.append((url, json, timeout))
            return FakeResponse()

    session = FakeSession()
    client = _HttpTushareClient("token-1", timeout=7.5, session=session)

    frame = client.daily(
        ts_code="600000.SH",
        start_date="20240101",
        end_date="20240105",
        fields="ts_code,trade_date,close",
    )

    assert frame.to_dict("records") == [
        {"ts_code": "600000.SH", "trade_date": "20240105", "close": 10.2}
    ]
    url, payload, timeout = session.calls[0]
    assert url == "https://api.tushare.pro"
    assert timeout == 7.5
    assert payload["api_name"] == "daily"
    assert payload["token"] == "token-1"
    assert payload["params"]["ts_code"] == "600000.SH"
    assert payload["fields"] == "ts_code,trade_date,close"


def test_tushare_rate_limit_waits_and_retries(monkeypatch):
    class FakePro:
        def __init__(self):
            self.calls = 0

        def daily(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TushareRateLimitError("50次/分钟", limit_per_minute=50)
            return pd.DataFrame(
                [
                    {
                        "ts_code": kwargs["ts_code"],
                        "trade_date": "20240105",
                        "open": 10.0,
                        "high": 10.5,
                        "low": 9.8,
                        "close": 10.2,
                        "vol": 1000.0,
                        "amount": 1000.0,
                    }
                ]
            )

    source = TushareSource(pro=FakePro(), request_interval=0, retries=1)
    waits: list[float] = []
    monkeypatch.setattr(source, "_sleep_with_checkpoint", waits.append)

    frame = source.fetch_daily("600000", "20240101", "20240105")

    assert waits[0] == 65.0
    assert waits[1] >= 60.0 / 50 * 1.15
    assert frame.to_dict("records")[0]["close"] == 10.2
    assert source._request_interval >= 60.0 / 50 * 1.15


def test_tushare_batch_reports_calendar_progress_after_cache_hits(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    _seed_one_bar(store)
    monkeypatch.setattr(updater, "get_store", lambda: store)

    class FakeTushareSource:
        def __init__(self, checkpoint=None, request_progress=None):
            self.checkpoint = checkpoint or (lambda: None)
            self.request_progress = request_progress

        def fetch_daily_many(self, symbols, start, end):
            if self.request_progress is not None:
                self.request_progress("trade_cal", 0, 1)
                self.request_progress("trade_cal", 1, 1)
                self.request_progress("daily", 1, 1)
            return {symbol: _bars() for symbol in symbols}

    monkeypatch.setattr(updater, "TushareSource", FakeTushareSource)
    progress: list[tuple[int, int, str, str]] = []

    rows = updater._update_symbols_tushare_batch(
        ["600000", "000001"],
        end_date="2024-01-05",
        max_workers=1,
        store=store,
        on_progress=lambda done, total, symbol, status: progress.append(
            (done, total, symbol, status)
        ),
    )

    assert [row["status"] for row in rows] == ["up_to_date", "updated"]
    assert progress[:2] == [
        (1, 2, "600000", "up_to_date"),
        (1, 2, "TUSHARE", "downloading:trade_cal request 0/1"),
    ]
    assert progress[-1] == (2, 2, "000001", "updated")


def test_data_job_downloading_status_does_not_count_as_failure(tmp_path):
    manager = DataJobManager(tmp_path / "jobs.sqlite")
    job_id = "job-1"
    with manager._connect() as conn:
        conn.execute(
            """
            INSERT INTO data_jobs(id, kind, source, status, total, workers, started_at)
            VALUES (?, 'download', 'tushare', 'running', 3, 1, ?)
            """,
            (job_id, "2024-01-01T00:00:00"),
        )

    manager._record_progress(job_id, 1, 3, "TUSHARE", "downloading:daily request 1/4")
    job = manager.get(job_id)

    assert job is not None
    assert job["completed"] == 1
    assert job["failed"] == 0
    assert job["updated"] == 0
    assert job["skipped"] == 0
    assert job["current_status"] == "running"
    assert job["recent"][0]["status"] == "running"


def test_data_job_completion_reconciles_progress_counters(tmp_path, monkeypatch):
    manager = DataJobManager(tmp_path / "jobs.sqlite")

    def fake_download_all_a(**kwargs):
        progress = kwargs["on_progress"]
        progress(1, 3, "TUSHARE", "downloading:daily request 1/3")
        return {
            "total": 3,
            "success": 1,
            "skipped": 2,
            "failed": 0,
            "errors": [],
        }

    monkeypatch.setattr(data_jobs, "download_all_a", fake_download_all_a)

    started = manager.start(
        "download",
        source="tushare",
        symbols=["600000", "000001", "000002"],
        workers=1,
    )
    job_id = started["job"]["id"]
    deadline = time.monotonic() + 5
    latest = None
    while time.monotonic() < deadline:
        latest = manager.get(job_id)
        if latest and not latest["running"]:
            break
        time.sleep(0.02)

    assert latest is not None
    assert latest["status"] == "completed"
    assert latest["completed"] == 3
    assert latest["percent"] == 100.0
    assert latest["updated"] == 1
    assert latest["skipped"] == 2
    assert latest["failed"] == 0
