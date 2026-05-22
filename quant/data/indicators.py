"""20 个常用通达信风格指标，向量化实现。

每个指标声明 ``IndicatorSpec``：
- ``output_columns``: 写入 parquet 的列名
- ``lookback``: 指标稳定所需的最少 bar 数（用于增量重算时的回溯窗口）
- ``version``: 公式版本，参数变更时要 bump，触发缓存失效

公式与通达信对齐的关键点：
- KDJ 用 Wilder 风格 ``SMA(M,N) = (N*X + (M-N)*Y_prev)/M``，**不能**用 ``ewm``
- RSI 用 Wilder 平滑（同上）
- DMI/ADX 同样基于 Wilder 平滑
- BBI = (MA3+MA6+MA12+MA24)/4
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    params: tuple
    output_columns: tuple[str, ...]
    lookback: int
    version: str

    @property
    def version_key(self) -> str:
        """``KDJ:9,3,3@v1`` — written into parquet KV-metadata for staleness check."""
        params = ",".join(str(p) for p in self.params)
        return f"{self.name}:{params}@{self.version}"


# ─── Wilder smoothing (TDX SMA(X,M,N)) ────────────────────────────────────────
def _sma_wilder(x: pd.Series, m: int, n: int = 1) -> pd.Series:
    """TDX 的 ``SMA(X,M,N)``：Y[i] = (N*X[i] + (M-N)*Y[i-1]) / M.

    第一根 NaN 处保持 NaN；首个有效值 = X[i]（等价于 Y[i-1]=X[i]）。
    """
    x = x.astype("float64")
    out = np.empty(len(x), dtype="float64")
    out[:] = np.nan
    prev = np.nan
    for i in range(len(x)):
        v = x.iat[i]
        if np.isnan(v):
            out[i] = np.nan
            continue
        if np.isnan(prev):
            prev = v
        else:
            prev = (n * v + (m - n) * prev) / m
        out[i] = prev
    return pd.Series(out, index=x.index)


# ─── 各指标实现 ───────────────────────────────────────────────────────────────
def _ma(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    return pd.DataFrame({
        "ma5": c.rolling(5, min_periods=1).mean(),
        "ma10": c.rolling(10, min_periods=1).mean(),
        "ma20": c.rolling(20, min_periods=1).mean(),
        "ma60": c.rolling(60, min_periods=1).mean(),
    })


def _ema(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    return pd.DataFrame({
        "ema12": c.ewm(span=12, adjust=False).mean(),
        "ema26": c.ewm(span=26, adjust=False).mean(),
    })


def _macd(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2
    return pd.DataFrame({"dif": dif, "dea": dea, "macd": macd})


def _kdj(df: pd.DataFrame) -> pd.DataFrame:
    """KDJ(9,3,3)，与现有策略 ``bbi_kdj_trend.py`` 内联实现保持一致：
    前 ``n-1`` 根 K=D=J=50 填充，然后用 ``Y = 2/3*Y_prev + 1/3*X`` 平滑。
    """
    n = 9
    length = len(df)
    low_n = df["low"].rolling(n, min_periods=n).min()
    high_n = df["high"].rolling(n, min_periods=n).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv_full = ((df["close"] - low_n) / rng * 100).fillna(50.0).to_numpy()

    k_arr = np.full(length, 50.0)
    d_arr = np.full(length, 50.0)
    prev_k, prev_d = 50.0, 50.0
    for i in range(n - 1, length):
        prev_k = (2.0 / 3.0) * prev_k + (1.0 / 3.0) * rsv_full[i]
        prev_d = (2.0 / 3.0) * prev_d + (1.0 / 3.0) * prev_k
        k_arr[i] = prev_k
        d_arr[i] = prev_d
    j_arr = 3 * k_arr - 2 * d_arr
    return pd.DataFrame({"kdj_k": k_arr, "kdj_d": d_arr, "kdj_j": j_arr})


def _rsi(df: pd.DataFrame) -> pd.DataFrame:
    diff = df["close"].diff()
    up = diff.clip(lower=0)
    dn = (-diff).clip(lower=0)

    def _one(period: int) -> pd.Series:
        au = _sma_wilder(up, period, 1)
        ad = _sma_wilder(dn, period, 1)
        rs = au / ad.replace(0, np.nan)
        return (100 - 100 / (1 + rs)).fillna(50.0)

    return pd.DataFrame({
        "rsi6": _one(6),
        "rsi12": _one(12),
        "rsi24": _one(24),
    })


def _boll(df: pd.DataFrame) -> pd.DataFrame:
    n, k = 20, 2
    mid = df["close"].rolling(n, min_periods=1).mean()
    std = df["close"].rolling(n, min_periods=1).std(ddof=0)
    return pd.DataFrame({
        "boll_mid": mid,
        "boll_up": mid + k * std,
        "boll_dn": mid - k * std,
    })


def _bbi(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    ma3 = c.rolling(3, min_periods=1).mean()
    ma6 = c.rolling(6, min_periods=1).mean()
    ma12 = c.rolling(12, min_periods=1).mean()
    ma24 = c.rolling(24, min_periods=1).mean()
    return pd.DataFrame({"bbi": (ma3 + ma6 + ma12 + ma24) / 4})


def _wr(df: pd.DataFrame) -> pd.DataFrame:
    def _one(n: int) -> pd.Series:
        hh = df["high"].rolling(n, min_periods=1).max()
        ll = df["low"].rolling(n, min_periods=1).min()
        rng = (hh - ll).replace(0, np.nan)
        return ((hh - df["close"]) / rng * 100).fillna(50.0)

    return pd.DataFrame({"wr10": _one(10), "wr6": _one(6)})


def _cci(df: pd.DataFrame) -> pd.DataFrame:
    n = 14
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(n, min_periods=1).mean()
    md = tp.rolling(n, min_periods=1).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    cci = (tp - ma) / (0.015 * md.replace(0, np.nan))
    return pd.DataFrame({"cci": cci.fillna(0.0)})


def _dmi(df: pd.DataFrame) -> pd.DataFrame:
    n = 14
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    dn_move = -low.diff()
    plus_dm = ((up_move > dn_move) & (up_move > 0)).astype(float) * up_move.fillna(0)
    minus_dm = ((dn_move > up_move) & (dn_move > 0)).astype(float) * dn_move.fillna(0)
    tr = pd.concat([
        (high - low),
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr = _sma_wilder(tr, n, 1)
    plus_di = 100 * _sma_wilder(plus_dm, n, 1) / atr.replace(0, np.nan)
    minus_di = 100 * _sma_wilder(minus_dm, n, 1) / atr.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    adx = _sma_wilder(dx, 6, 1)
    adxr = (adx + adx.shift(6)) / 2
    return pd.DataFrame({
        "pdi": plus_di.fillna(0.0),
        "mdi": minus_di.fillna(0.0),
        "adx": adx.fillna(0.0),
        "adxr": adxr.fillna(0.0),
    })


def _atr(df: pd.DataFrame) -> pd.DataFrame:
    n = 14
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        (high - low),
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return pd.DataFrame({"atr": tr.rolling(n, min_periods=1).mean()})


def _obv(df: pd.DataFrame) -> pd.DataFrame:
    diff = df["close"].diff()
    sign = np.where(diff > 0, 1.0, np.where(diff < 0, -1.0, 0.0))
    obv = (sign * df["volume"]).cumsum()
    return pd.DataFrame({"obv": obv})


def _vol(df: pd.DataFrame) -> pd.DataFrame:
    v = df["volume"]
    return pd.DataFrame({
        "mavol5": v.rolling(5, min_periods=1).mean(),
        "mavol10": v.rolling(10, min_periods=1).mean(),
    })


def _sar(df: pd.DataFrame) -> pd.DataFrame:
    """通达信 SAR(4,2,20)：步长 0.02，最大 0.2，反转 4 根。"""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    sar = np.full(n, np.nan)
    if n == 0:
        return pd.DataFrame({"sar": sar})
    af_init, af_step, af_max = 0.02, 0.02, 0.20
    bull = True
    af = af_init
    ep = high[0]
    sar[0] = low[0]
    for i in range(1, n):
        prev_sar = sar[i - 1]
        if bull:
            cur = prev_sar + af * (ep - prev_sar)
            cur = min(cur, low[i - 1], low[i])
            if low[i] < cur:
                bull = False
                cur = ep
                ep = low[i]
                af = af_init
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_step, af_max)
        else:
            cur = prev_sar + af * (ep - prev_sar)
            cur = max(cur, high[i - 1], high[i])
            if high[i] > cur:
                bull = True
                cur = ep
                ep = high[i]
                af = af_init
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_step, af_max)
        sar[i] = cur
    return pd.DataFrame({"sar": sar})


def _trix(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    e1 = c.ewm(span=12, adjust=False).mean()
    e2 = e1.ewm(span=12, adjust=False).mean()
    e3 = e2.ewm(span=12, adjust=False).mean()
    trix = e3.pct_change() * 100
    return pd.DataFrame({
        "trix": trix.fillna(0.0),
        "trix_ma": trix.rolling(9, min_periods=1).mean().fillna(0.0),
    })


def _dma(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    dma = c.rolling(10, min_periods=1).mean() - c.rolling(50, min_periods=1).mean()
    ama = dma.rolling(10, min_periods=1).mean()
    return pd.DataFrame({"dma": dma, "dma_ama": ama})


def _expma(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    return pd.DataFrame({
        "expma12": c.ewm(span=12, adjust=False).mean(),
        "expma50": c.ewm(span=50, adjust=False).mean(),
    })


def _psy(df: pd.DataFrame) -> pd.DataFrame:
    n, m = 12, 6
    diff = df["close"].diff()
    up = (diff > 0).astype(float)
    psy = up.rolling(n, min_periods=1).sum() / n * 100
    psyma = psy.rolling(m, min_periods=1).mean()
    return pd.DataFrame({"psy": psy, "psyma": psyma})


def _mtm(df: pd.DataFrame) -> pd.DataFrame:
    n, m = 12, 6
    mtm = df["close"] - df["close"].shift(n)
    mtmma = mtm.rolling(m, min_periods=1).mean()
    return pd.DataFrame({"mtm": mtm.fillna(0.0), "mtmma": mtmma.fillna(0.0)})


def _roc(df: pd.DataFrame) -> pd.DataFrame:
    n, m = 12, 6
    roc = df["close"].pct_change(n) * 100
    rocma = roc.rolling(m, min_periods=1).mean()
    return pd.DataFrame({"roc": roc.fillna(0.0), "rocma": rocma.fillna(0.0)})


# ─── 注册表 ───────────────────────────────────────────────────────────────────
INDICATORS: dict[str, IndicatorSpec] = {
    "MA":    IndicatorSpec("MA",    (5, 10, 20, 60),    ("ma5", "ma10", "ma20", "ma60"),                60, "v1"),
    "EMA":   IndicatorSpec("EMA",   (12, 26),           ("ema12", "ema26"),                             60, "v1"),
    "MACD":  IndicatorSpec("MACD",  (12, 26, 9),        ("dif", "dea", "macd"),                         40, "v1"),
    "KDJ":   IndicatorSpec("KDJ",   (9, 3, 3),          ("kdj_k", "kdj_d", "kdj_j"),                    30, "v1"),
    "RSI":   IndicatorSpec("RSI",   (6, 12, 24),        ("rsi6", "rsi12", "rsi24"),                     30, "v1"),
    "BOLL":  IndicatorSpec("BOLL",  (20, 2),            ("boll_mid", "boll_up", "boll_dn"),             25, "v1"),
    "BBI":   IndicatorSpec("BBI",   (3, 6, 12, 24),     ("bbi",),                                       25, "v1"),
    "WR":    IndicatorSpec("WR",    (10, 6),            ("wr10", "wr6"),                                12, "v1"),
    "CCI":   IndicatorSpec("CCI",   (14,),              ("cci",),                                       20, "v1"),
    "DMI":   IndicatorSpec("DMI",   (14, 6),            ("pdi", "mdi", "adx", "adxr"),                  35, "v1"),
    "ATR":   IndicatorSpec("ATR",   (14,),              ("atr",),                                       20, "v1"),
    "OBV":   IndicatorSpec("OBV",   (),                 ("obv",),                                       1,  "v1"),
    "VOL":   IndicatorSpec("VOL",   (5, 10),            ("mavol5", "mavol10"),                          12, "v1"),
    "SAR":   IndicatorSpec("SAR",   (4, 2, 20),         ("sar",),                                       5,  "v1"),
    "TRIX":  IndicatorSpec("TRIX",  (12, 9),            ("trix", "trix_ma"),                            50, "v1"),
    "DMA":   IndicatorSpec("DMA",   (10, 50, 10),       ("dma", "dma_ama"),                             60, "v1"),
    "EXPMA": IndicatorSpec("EXPMA", (12, 50),           ("expma12", "expma50"),                         60, "v1"),
    "PSY":   IndicatorSpec("PSY",   (12, 6),            ("psy", "psyma"),                               18, "v1"),
    "MTM":   IndicatorSpec("MTM",   (12, 6),            ("mtm", "mtmma"),                               18, "v1"),
    "ROC":   IndicatorSpec("ROC",   (12, 6),            ("roc", "rocma"),                               18, "v1"),
}

_COMPUTERS: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    "MA": _ma, "EMA": _ema, "MACD": _macd, "KDJ": _kdj, "RSI": _rsi,
    "BOLL": _boll, "BBI": _bbi, "WR": _wr, "CCI": _cci, "DMI": _dmi,
    "ATR": _atr, "OBV": _obv, "VOL": _vol, "SAR": _sar, "TRIX": _trix,
    "DMA": _dma, "EXPMA": _expma, "PSY": _psy, "MTM": _mtm, "ROC": _roc,
}


def all_indicator_columns() -> list[str]:
    """20 个指标展开后的全部输出列名。"""
    cols: list[str] = []
    for spec in INDICATORS.values():
        cols.extend(spec.output_columns)
    return cols


def compute(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """计算单个指标，返回只含 ``output_columns`` 的 DataFrame。"""
    name = name.upper()
    if name not in _COMPUTERS:
        raise KeyError(f"unknown indicator: {name}")
    return _COMPUTERS[name](df)


def compute_all(df: pd.DataFrame, names: list[str] | None = None) -> pd.DataFrame:
    """对一只股票批量计算指标，返回新 DataFrame（不修改入参）。

    返回 OHLCV 列 + 指标列；调用者负责合并到自己的存储。
    """
    if df is None or len(df) == 0:
        return df.copy() if df is not None else pd.DataFrame()
    out = df.copy()
    selected = [n.upper() for n in (names or list(INDICATORS.keys()))]
    for name in selected:
        cols = compute(name, df)
        for col in cols.columns:
            out[col] = cols[col].to_numpy()
    return out


def indicator_versions(names: list[str] | None = None) -> dict[str, str]:
    """``{column_name: version_key}`` — 写入 parquet KV-metadata。"""
    selected = [n.upper() for n in (names or list(INDICATORS.keys()))]
    versions: dict[str, str] = {}
    for name in selected:
        spec = INDICATORS[name]
        for col in spec.output_columns:
            versions[col] = spec.version_key
    return versions


def stale_indicators(stored_versions: dict[str, str], names: list[str] | None = None) -> list[str]:
    """返回所有 ``stored_versions`` 与当前 ``INDICATORS`` 不一致的指标名。"""
    current = indicator_versions(names)
    stale: set[str] = set()
    for name in (names or list(INDICATORS.keys())):
        spec = INDICATORS[name.upper()]
        for col in spec.output_columns:
            if stored_versions.get(col) != current[col]:
                stale.add(name.upper())
                break
    return sorted(stale)
