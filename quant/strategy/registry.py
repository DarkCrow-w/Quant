from __future__ import annotations

from quant.strategy.base import Strategy
from quant.strategy.examples.bbi_kdj_trend import BBIKDJTrendStrategy
from quant.strategy.examples.dip_buy import DipBuyStrategy, SwingDipBuyStrategy
from quant.strategy.examples.ma_cross import MACrossStrategy
from quant.strategy.examples.vol_kdj_bbi import VolKDJBBIStrategy


BASIC_STRATEGY_CLASSES: dict[str, type[Strategy]] = {
    "ma_cross": MACrossStrategy,
    "vol_kdj_bbi": VolKDJBBIStrategy,
    "bbi_kdj_trend": BBIKDJTrendStrategy,
    "dip_buy": DipBuyStrategy,
    "swing_dip_buy": SwingDipBuyStrategy,
}


STRATEGY_DISPLAY_NAMES: dict[str, str] = {
    "ma_cross": "MA 均线交叉",
    "vol_kdj_bbi": "量价KDJ+BBI",
    "bbi_kdj_trend": "BBI趋势+KDJ择时",
    "dip_buy": "抄底（RSI+KDJ+VOL+BBI）",
    "swing_dip_buy": "波段抄底（KDJ+RSI+VOL+BBI）",
}


def get_basic_strategy_class(name: str) -> type[Strategy]:
    try:
        return BASIC_STRATEGY_CLASSES[name]
    except KeyError as exc:
        available = ", ".join(sorted(BASIC_STRATEGY_CLASSES))
        raise ValueError(f"Unknown strategy: {name}. Available: {available}") from exc
