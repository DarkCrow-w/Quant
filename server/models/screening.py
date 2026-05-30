from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class ScreenRequest(BaseModel):
    strategy: str
    strategy_params: dict = Field(default_factory=dict)
    scan_date: str | None = None
    lookback: int = 120


class ScreenMatch(BaseModel):
    symbol: str
    signal_date: str
    close: float
    volume: float
    amount: float
    strength: float


class ScreenResult(BaseModel):
    strategy: str
    scan_date: str
    total_scanned: int
    matches: list[ScreenMatch]
    elapsed_seconds: float


class FactorWeights(BaseModel):
    """五维因子权重（未归一化，后端自动归一化）。"""

    trend: float = 0.25
    momentum: float = 0.20
    volume: float = 0.20
    dip: float = 0.20
    risk: float = 0.15


class ScoreRequest(BaseModel):
    """多因子评分选股请求。"""

    scan_date: str | None = None
    lookback: int = 250
    weights: FactorWeights = Field(default_factory=FactorWeights)
    exclude_centipede: bool = True
    min_sandglass: float = 0.0
    min_amount: float = 0.0  # 元
    min_price: float = 0.0
    use_patterns: bool = True
    top_n: int = 100  # 返回前 N
    max_symbols: int = 0  # 0=全市场；>0 仅扫前 N 只（调试/限速）


class FactorScoreItem(BaseModel):
    """五维因子分值（各 0-100）。"""

    trend: float
    momentum: float
    volume: float
    dip: float
    risk: float


class ScoredStock(BaseModel):
    """单只股票的多因子评分结果。"""

    symbol: str
    score: float
    rating: str
    factors: FactorScoreItem
    reasons: list[str]
    warnings: list[str]
    signal_date: str
    close: float
    pct_chg: float
    volume: float
    amount: float
    sandglass: float
    wave: str
    kirin: str


class ScoreResult(BaseModel):
    """多因子评分选股结果。"""

    scan_date: str
    total_scanned: int
    total_matched: int  # 通过过滤的数量
    returned: int  # = len(stocks)
    stocks: list[ScoredStock]
    elapsed_seconds: float


class FactorDef(BaseModel):
    """因子元数据（供前端展示与默认权重）。"""

    key: str
    label: str
    default_weight: float
    desc: str


class CompositeMetricDef(BaseModel):
    key: str
    label: str
    category: str
    description: str
    unit: str = ""
    value_type: str = "number"
    operators: list[str]
    params: list[dict] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    source: str = "kline"


class CompositeCondition(BaseModel):
    id: str
    metric: str
    operator: str = "gte"
    value: float | int | str | None = 0
    value2: float | None = None
    compare_metric: str | None = None
    params: dict[str, float] = Field(default_factory=dict)
    periods: int = Field(3, ge=2, le=60)
    weight: float = Field(1, ge=0, le=100)
    required: bool = True
    enabled: bool = True


class CompositeGroup(BaseModel):
    id: str
    name: str = "条件组"
    logic: Literal["all", "any"] = "all"
    conditions: list[CompositeCondition] = Field(default_factory=list)


class FactorStrategyDraft(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    logic: Literal["all", "any"] = "all"
    groups: list[CompositeGroup] = Field(default_factory=list)
    min_score: float = Field(0, ge=0, le=100)
    top_n: int = Field(100, ge=1, le=1000)
    lookback: int = Field(250, ge=30, le=1500)


class FactorStrategy(FactorStrategyDraft):
    id: str
    created_at: str
    updated_at: str


class CompositeScanRequest(BaseModel):
    strategy_id: str | None = None
    definition: FactorStrategyDraft | None = None
    scan_date: str | None = None
    max_symbols: int = Field(0, ge=0)


class CompositeStock(BaseModel):
    symbol: str
    matched: bool
    score: float
    passed_conditions: int
    available_conditions: int
    total_conditions: int
    signal_date: str
    close: float
    pct_chg: float
    volume: float
    amount: float
    turnover_rate: float | None = None
    reasons: list[str]
    failures: list[str]
    values: dict


class CompositeScanResult(BaseModel):
    strategy_id: str | None = None
    strategy_name: str
    scan_date: str
    total_scanned: int
    total_matched: int
    returned: int
    stocks: list[CompositeStock]
    elapsed_seconds: float
    warnings: list[str] = Field(default_factory=list)
