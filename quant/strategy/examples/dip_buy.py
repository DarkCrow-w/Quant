"""抄底策略 — 实现见 docs/抄底策略.md。

核心指标：RSI(3)、KDJ-J、VOL、BBI、MACD-DIF。

买入（7 条全满足）：
  1. N 型上涨结构（MA 上升 + 后半段 low > 前半段 low）
  2. lookback 内出现天量红柱（max 红柱量 ≥ 中位量 × ratio）
  3. lookback 内不出现高位放量大阴线
  4. KDJ-J < 阈值 或 RSI(3) < 阈值
  5. 缩量十字星（板块自适应振幅/实体阈值 + 当日 vol < 5 日均 × shrink_ratio）
  6. MACD DIF 在 0 轴之上
  7. close 在 BBI ±band 区间

卖出：
  - 止损：close ≤ 止损价（取 max(buy_low×(1-stop_pct), N 型前低) 中更接近的）
  - 放飞一半：连续 N 天站上 BBI + 连续两根中大阳线
  - 清仓收队：放飞后连续 N 天跌破 BBI
"""
from __future__ import annotations

import statistics

import pandas as pd

from quant.core.bar import Bar
from quant.core.events import SignalEvent
from quant.core.order import OrderSide
from quant.data.indicators import compute
from quant.strategy.base import Context, Strategy

# 创业板/科创板/CDR 走「高弹性」阈值；其它沪深主板走「低弹性」
_BOARD_HIGH_VOL = ("3", "688", "689")
_THRESHOLDS = {
    "low":  {"amp_pct": 0.04, "body_pct": 0.02, "big_yang": 0.02, "big_yin_neg": 0.03},
    "high": {"amp_pct": 0.07, "body_pct": 0.04, "big_yang": 0.04, "big_yin_neg": 0.05},
}


def _board_kind(symbol: str) -> str:
    return "high" if symbol.startswith(_BOARD_HIGH_VOL) else "low"


def _is_doji(bar: Bar, amp_pct: float, body_pct: float) -> bool:
    if bar.open <= 0:
        return False
    amp = (bar.high - bar.low) / bar.open
    body = abs(bar.close - bar.open) / bar.open
    return amp <= amp_pct and body <= body_pct


def _rsi_3(closes: list[float]) -> float:
    """RSI(3) Wilder 平滑：Y = (X + 2*Y_prev)/3。"""
    if len(closes) < 4:
        return 50.0
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    ups = [max(d, 0.0) for d in diffs]
    dns = [max(-d, 0.0) for d in diffs]
    au, ad = ups[0], dns[0]
    for u, d in zip(ups[1:], dns[1:]):
        au = (u + 2 * au) / 3
        ad = (d + 2 * ad) / 3
    if ad == 0:
        return 100.0 if au > 0 else 50.0
    return 100 - 100 / (1 + au / ad)


def _has_volume_climax(bars: list[Bar], lookback: int, ratio: float) -> bool:
    """近 lookback 内最大红柱量 ≥ 中位量 × ratio。"""
    window = bars[-lookback:]
    red_vols = [b.volume for b in window if b.close > b.open]
    if not red_vols:
        return False
    med = statistics.median(b.volume for b in window)
    return med > 0 and max(red_vols) >= med * ratio


def _has_high_position_yin_climax(bars: list[Bar], lookback: int, big_yin_neg: float) -> bool:
    """高位放量大阴线（有则否决）。"""
    window = bars[-lookback:]
    if len(window) < 6:
        return False
    recent_high = max(b.high for b in window)
    for i in range(5, len(window)):
        b = window[i]
        prev5 = window[i - 5: i]
        avg_vol = sum(p.volume for p in prev5) / 5
        body_pct = (b.open - b.close) / b.open if b.open > 0 else 0
        if (b.close < b.open
                and body_pct >= big_yin_neg
                and avg_vol > 0
                and b.volume >= avg_vol * 1.5
                and b.close >= recent_high * 0.9):
            return True
    return False


def _check_n_shape(bars: list[Bar], ma_period: int) -> bool:
    """N 型上涨结构：MA 上升 + 后半段 low > 前半段 low。

    买点本身在 BBI ±5% 区间已由独立条件保证，这里不再要求 close 在 MA 之上
    （回调到位的买点经常在 MA 下沿）。
    """
    if len(bars) < ma_period + 5:
        return False
    closes = [b.close for b in bars]
    ma_now = sum(closes[-ma_period:]) / ma_period
    ma_prev = sum(closes[-ma_period - 5: -5]) / ma_period
    if ma_now <= ma_prev:
        return False
    half = ma_period // 2
    recent_low = min(b.low for b in bars[-half:])
    earlier_low = min(b.low for b in bars[-ma_period: -half])
    return recent_low > earlier_low


def _bars_to_df(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame({
        "open":   [b.open for b in bars],
        "high":   [b.high for b in bars],
        "low":    [b.low for b in bars],
        "close":  [b.close for b in bars],
        "volume": [b.volume for b in bars],
    })


def _ma(values: list[float], period: int) -> float:
    if len(values) < period:
        return float("nan")
    return sum(values[-period:]) / period


class DipBuyStrategy(Strategy):
    """抄底策略 — 见 docs/抄底策略.md。

    Params:
        ma_period (20):           N 型/均线周期
        vol_lookback (20):        量能/否决窗口
        volume_climax_ratio (4.0):天量倍数（max 红柱 / 中位）
        kdj_j_threshold (10):     KDJ-J 买入阈值
        rsi3_threshold (20):      RSI(3) 买入阈值
        bbi_band_pct (0.05):      close 距 BBI 的最大允许偏离
        doji_shrink_ratio (0.85): 当日 vol / 5 日均 上限（缩量）
        stop_loss_pct (0.04):     固定止损百分比
        n_form_close_pct (0.05):  止损价距 N 型前低多近时改用前低
        bbi_confirm_days (2):     BBI 站上/跌破连续天数
    """

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self._buy_low: dict[str, float] = {}
        self._stop: dict[str, float] = {}
        self._half_sold: dict[str, bool] = {}
        self._above_bbi: dict[str, int] = {}
        self._below_bbi: dict[str, int] = {}
        self._big_yang_streak: dict[str, int] = {}

    def _has_position(self, ctx: Context, symbol: str) -> bool:
        positions = ctx.portfolio_snapshot.get("positions", {})
        return symbol in positions and positions[symbol]["qty"] > 0

    def _clear_state(self, symbol: str) -> None:
        for d in (self._buy_low, self._stop, self._half_sold,
                  self._above_bbi, self._below_bbi, self._big_yang_streak):
            d.pop(symbol, None)

    def on_bar(self, ctx: Context) -> list[SignalEvent]:
        p = self.params
        lookback         = p.get("vol_lookback", 20)
        vol_climax_ratio = p.get("volume_climax_ratio", 2.0)
        kdj_j_thr        = p.get("kdj_j_threshold", 10)
        rsi3_thr         = p.get("rsi3_threshold", 20)
        bbi_band         = p.get("bbi_band_pct", 0.05)
        shrink_ratio     = p.get("doji_shrink_ratio", 0.85)
        stop_pct         = p.get("stop_loss_pct", 0.04)
        n_form_close     = p.get("n_form_close_pct", 0.05)
        bbi_confirm      = p.get("bbi_confirm_days", 2)
        ma_period        = p.get("ma_period", 20)

        signals: list[SignalEvent] = []

        for symbol, bar in ctx.bars.items():
            bars = ctx.latest(symbol, 9999)
            if len(bars) < max(lookback + 5, ma_period + 5, 30):
                continue

            board = _board_kind(symbol)
            thr = _THRESHOLDS[board]

            if self._has_position(ctx, symbol):
                # 持仓分支需要 BBI
                df = _bars_to_df(bars)
                bbi = float(compute("BBI", df)["bbi"].iat[-1])
                if pd.isna(bbi) or bbi <= 0:
                    continue

                # 1) 止损
                if bar.close <= self._stop.get(symbol, 0):
                    signals.append(SignalEvent(
                        symbol=symbol, dt=bar.dt,
                        direction=OrderSide.SELL, strength=1.0,
                    ))
                    self._clear_state(symbol)
                    continue

                # 2) BBI 站上/跌破计数
                if bar.close > bbi:
                    self._above_bbi[symbol] = self._above_bbi.get(symbol, 0) + 1
                    self._below_bbi[symbol] = 0
                else:
                    self._below_bbi[symbol] = self._below_bbi.get(symbol, 0) + 1
                    self._above_bbi[symbol] = 0

                # 3) 中大阳线连续计数
                pct_chg = (bar.close - bar.open) / bar.open if bar.open > 0 else 0
                if pct_chg >= thr["big_yang"]:
                    self._big_yang_streak[symbol] = self._big_yang_streak.get(symbol, 0) + 1
                else:
                    self._big_yang_streak[symbol] = 0

                # 4) 放飞一半
                if (not self._half_sold.get(symbol, False)
                        and self._above_bbi.get(symbol, 0) >= bbi_confirm
                        and self._big_yang_streak.get(symbol, 0) >= 2):
                    signals.append(SignalEvent(
                        symbol=symbol, dt=bar.dt,
                        direction=OrderSide.SELL, strength=0.5,
                    ))
                    self._half_sold[symbol] = True
                # 5) 清仓
                elif (self._half_sold.get(symbol, False)
                        and self._below_bbi.get(symbol, 0) >= bbi_confirm):
                    signals.append(SignalEvent(
                        symbol=symbol, dt=bar.dt,
                        direction=OrderSide.SELL, strength=1.0,
                    ))
                    self._clear_state(symbol)
                continue

            # ── 无持仓 → 买入条件（先跑便宜过滤，再做指标 compute）──

            # 1) 缩量十字星（最便宜，杀掉 99% 的样本）
            if len(bars) < 11:
                continue
            avg_prior = sum(b.volume for b in bars[-11:-1]) / 10
            if avg_prior <= 0:
                continue
            if not (_is_doji(bar, thr["amp_pct"], thr["body_pct"])
                    and bar.volume < avg_prior * shrink_ratio):
                continue

            # 2) N 型结构
            if not _check_n_shape(bars, ma_period):
                continue

            # 3) 天量红柱
            if not _has_volume_climax(bars, lookback, vol_climax_ratio):
                continue

            # 4) 期间无高位放量大阴线
            if _has_high_position_yin_climax(bars, lookback, thr["big_yin_neg"]):
                continue

            # 5) KDJ-J / RSI(3) — 至此才需要建 DataFrame
            df = _bars_to_df(bars)
            kdj_j = float(compute("KDJ", df)["kdj_j"].iat[-1])
            if kdj_j >= kdj_j_thr:
                rsi3 = _rsi_3([b.close for b in bars])
                if rsi3 >= rsi3_thr:
                    continue

            # 6) MACD DIF > 0
            dif_now = float(compute("MACD", df)["dif"].iat[-1])
            if dif_now <= 0:
                continue

            # 7) close 在 BBI ±band
            bbi = float(compute("BBI", df)["bbi"].iat[-1])
            if pd.isna(bbi) or bbi <= 0:
                continue
            if abs(bar.close - bbi) / bbi > bbi_band:
                continue

            # 全部命中
            signals.append(SignalEvent(
                symbol=symbol, dt=bar.dt,
                direction=OrderSide.BUY, strength=1.0,
            ))
            half = ma_period // 2
            n_form_low = min(b.low for b in bars[-ma_period: -half])
            stop_pct_price = bar.low * (1 - stop_pct)
            if bar.low > 0 and abs(stop_pct_price - n_form_low) / bar.low <= n_form_close:
                self._stop[symbol] = n_form_low
            else:
                self._stop[symbol] = stop_pct_price
            self._buy_low[symbol] = bar.low
            self._half_sold[symbol] = False
            self._above_bbi[symbol] = 0
            self._below_bbi[symbol] = 0
            self._big_yang_streak[symbol] = 0

        return signals


class SwingDipBuyStrategy(Strategy):
    """波段抄底策略：KDJ/RSI/VOL/BBI 低吸 + 分批止盈。

    相比 ``DipBuyStrategy``，本策略不要求所有形态条件硬性全满足，而是用评分过滤：
    低位超卖、BBI 低吸区、前期恐慌量/缩量、反转 K 线、趋势未破坏、动能修复。
    这样可以提高交易机会，同时用硬止损、BBI 破位和追踪止盈控制回撤。
    """

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self._entry_price: dict[str, float] = {}
        self._peak_price: dict[str, float] = {}
        self._half_sold: dict[str, bool] = {}
        self._below_bbi: dict[str, int] = {}

    def _has_position(self, ctx: Context, symbol: str) -> bool:
        positions = ctx.portfolio_snapshot.get("positions", {})
        return symbol in positions and positions[symbol]["qty"] > 0

    def _clear_state(self, symbol: str) -> None:
        for state in (self._entry_price, self._peak_price, self._half_sold, self._below_bbi):
            state.pop(symbol, None)

    def _entry_score(
        self,
        bars: list[Bar],
        df: pd.DataFrame,
        *,
        lookback: int,
        kdj_j_threshold: float,
        rsi3_threshold: float,
        rsi6_threshold: float,
        bbi_lower_band_pct: float,
        bbi_upper_band_pct: float,
        panic_volume_ratio: float,
        dryup_ratio: float,
        reversal_pct: float,
        trend_floor_pct: float,
    ) -> tuple[int, list[str]]:
        bar = bars[-1]
        prev = bars[-2]
        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]
        score = 0
        reasons: list[str] = []

        kdj = compute("KDJ", df)
        rsi = compute("RSI", df)
        macd = compute("MACD", df)
        bbi_now = float(compute("BBI", df)["bbi"].iat[-1])
        if pd.isna(bbi_now) or bbi_now <= 0:
            return 0, []

        kdj_j_now = float(kdj["kdj_j"].iat[-1])
        kdj_j_prev = float(kdj["kdj_j"].iat[-2])
        rsi6 = float(rsi["rsi6"].iat[-1])
        rsi3 = _rsi_3(closes)

        if kdj_j_now <= kdj_j_threshold or rsi3 <= rsi3_threshold or rsi6 <= rsi6_threshold:
            score += 2
            reasons.append("KDJ/RSI 超卖")

        bbi_gap = (bar.close - bbi_now) / bbi_now
        if -bbi_lower_band_pct <= bbi_gap <= bbi_upper_band_pct:
            score += 2
            reasons.append("BBI 低吸区")

        window = bars[-lookback:]
        median_vol = statistics.median(b.volume for b in window)
        panic_seen = any(
            b.close < b.open
            and median_vol > 0
            and b.volume >= median_vol * panic_volume_ratio
            for b in window[-min(12, len(window)):]
        )
        if panic_seen:
            score += 1
            reasons.append("近端恐慌量")

        avg10_vol = sum(volumes[-11:-1]) / 10 if len(volumes) >= 11 else median_vol
        dryup = avg10_vol > 0 and bar.volume <= avg10_vol * dryup_ratio
        if dryup:
            score += 1
            reasons.append("缩量回踩")

        reversal = (
            bar.close > bar.open
            and bar.close >= prev.close * (1 + reversal_pct)
        ) or (
            bar.low < prev.low and bar.close > prev.close
        )
        if reversal:
            score += 2
            reasons.append("反转确认")

        ma20 = _ma(closes, 20)
        ma60 = _ma(closes, 60)
        trend_ok = (
            not pd.isna(ma20)
            and not pd.isna(ma60)
            and bar.close >= ma60 * (1 - trend_floor_pct)
            and ma20 >= ma60 * (1 - trend_floor_pct)
        )
        if trend_ok:
            score += 1
            reasons.append("中期趋势未破")

        dif = macd["dif"]
        momentum_repair = (
            len(dif) >= 3
            and float(dif.iat[-1]) > float(dif.iat[-2])
        ) or kdj_j_now > kdj_j_prev
        if momentum_repair:
            score += 1
            reasons.append("动能修复")

        return score, reasons

    def on_bar(self, ctx: Context) -> list[SignalEvent]:
        p = self.params
        lookback = p.get("lookback", 30)
        entry_score = p.get("entry_score", 4)
        kdj_j_threshold = p.get("kdj_j_threshold", 18)
        rsi3_threshold = p.get("rsi3_threshold", 28)
        rsi6_threshold = p.get("rsi6_threshold", 32)
        bbi_lower_band_pct = p.get("bbi_lower_band_pct", 0.10)
        bbi_upper_band_pct = p.get("bbi_upper_band_pct", 0.03)
        panic_volume_ratio = p.get("panic_volume_ratio", 1.8)
        dryup_ratio = p.get("dryup_ratio", 0.85)
        reversal_pct = p.get("reversal_pct", 0.003)
        trend_floor_pct = p.get("trend_floor_pct", 0.08)
        stop_loss_pct = p.get("stop_loss_pct", 0.055)
        take_profit_pct = p.get("take_profit_pct", 0.36)
        second_profit_pct = p.get("second_profit_pct", 0.648)
        trailing_stop_pct = p.get("trailing_stop_pct", 0.08)
        bbi_break_days = p.get("bbi_break_days", 2)
        bbi_exit_band_pct = p.get("bbi_exit_band_pct", 0.015)

        signals: list[SignalEvent] = []

        for symbol, bar in ctx.bars.items():
            bars = ctx.latest(symbol, 9999)
            if len(bars) < max(lookback + 5, 80):
                continue
            df = _bars_to_df(bars)
            bbi_now = float(compute("BBI", df)["bbi"].iat[-1])
            if pd.isna(bbi_now) or bbi_now <= 0:
                continue

            if self._has_position(ctx, symbol):
                entry = self._entry_price.get(symbol, bar.close)
                peak = max(self._peak_price.get(symbol, entry), bar.high)
                self._peak_price[symbol] = peak

                if bar.close <= entry * (1 - stop_loss_pct):
                    signals.append(SignalEvent(symbol, bar.dt, OrderSide.SELL, 1.0))
                    self._clear_state(symbol)
                    continue

                below_bbi = bar.close < bbi_now * (1 - bbi_exit_band_pct)
                self._below_bbi[symbol] = self._below_bbi.get(symbol, 0) + 1 if below_bbi else 0
                if self._below_bbi.get(symbol, 0) >= bbi_break_days:
                    signals.append(SignalEvent(symbol, bar.dt, OrderSide.SELL, 1.0))
                    self._clear_state(symbol)
                    continue

                gain = (bar.close - entry) / entry if entry > 0 else 0
                draw_from_peak = (peak - bar.close) / peak if peak > 0 else 0
                if not self._half_sold.get(symbol, False) and gain >= take_profit_pct:
                    signals.append(SignalEvent(symbol, bar.dt, OrderSide.SELL, 0.5))
                    self._half_sold[symbol] = True
                elif self._half_sold.get(symbol, False) and (
                    gain >= second_profit_pct or draw_from_peak >= trailing_stop_pct
                ):
                    signals.append(SignalEvent(symbol, bar.dt, OrderSide.SELL, 1.0))
                    self._clear_state(symbol)
                continue

            score, _ = self._entry_score(
                bars,
                df,
                lookback=lookback,
                kdj_j_threshold=kdj_j_threshold,
                rsi3_threshold=rsi3_threshold,
                rsi6_threshold=rsi6_threshold,
                bbi_lower_band_pct=bbi_lower_band_pct,
                bbi_upper_band_pct=bbi_upper_band_pct,
                panic_volume_ratio=panic_volume_ratio,
                dryup_ratio=dryup_ratio,
                reversal_pct=reversal_pct,
                trend_floor_pct=trend_floor_pct,
            )
            if score < entry_score:
                continue

            strength = min(1.0, 0.65 + score * 0.05)
            signals.append(SignalEvent(symbol, bar.dt, OrderSide.BUY, strength))
            self._entry_price[symbol] = bar.close
            self._peak_price[symbol] = bar.high
            self._half_sold[symbol] = False
            self._below_bbi[symbol] = 0

        return signals
