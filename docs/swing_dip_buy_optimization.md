# 波段抄底策略优化记录

## 目标

基于 KDJ、RSI、成交量、BBI 的抄底策略，迭代出更高收益的波段抄底参数组合，并用 demo 数据回测验证。

## 当前最优参数

策略参数：

```json
{
  "entry_score": 4,
  "kdj_j_threshold": 18,
  "rsi3_threshold": 28,
  "rsi6_threshold": 32,
  "bbi_lower_band_pct": 0.08,
  "panic_volume_ratio": 1.3,
  "take_profit_pct": 0.36,
  "second_profit_pct": 0.648,
  "trailing_stop_pct": 0.06
}
```

风险参数：

```json
{
  "max_position_pct": 3.0
}
```

## 回测结果

数据范围：demo 全市场样本，2023-01-01 到 2026-06-17，16 只股票。

事件驱动回测复核结果：

- 初始资金：1,000,000
- 期末权益：11,708,120.11
- 总收益率：1070.812%
- 年化收益率：103.719%
- 最大回撤：-35.138%
- 交易次数：16

原始 `dip_buy` 基线：

- 期末权益：1,097,533.56
- 总收益率：9.753%
- 年化收益率：2.728%
- 最大回撤：-4.492%
- 交易次数：4

## 风险说明

年化翻倍结果依赖 `max_position_pct=3.0`，属于高杠杆/压力配置，不适合作为默认实盘风控参数。正常仓位下，当前参数组合更适合作为波段低吸策略的研究起点；上线前必须使用真实 A 股历史数据、滑点、涨跌停、停牌、交易约束和样本外区间复核。

## 复现命令

```powershell
& 'D:\yonum\Python\Python310\python.exe' scripts\optimize_swing_dip_buy.py --max-candidates 8 --top 8
```

事件驱动复核命令：

```powershell
& 'D:\yonum\Python\Python310\python.exe' -c "from datetime import date; from scripts.optimize_swing_dip_buy import demo_data, run_candidate; from scripts.seed_demo_data import DEMO_SYMBOLS; params={'entry_score':4,'kdj_j_threshold':18,'rsi3_threshold':28,'rsi6_threshold':32,'bbi_lower_band_pct':0.08,'panic_volume_ratio':1.3,'take_profit_pct':0.36,'trailing_stop_pct':0.06,'second_profit_pct':0.648}; data=demo_data(list(DEMO_SYMBOLS), date(2023,1,1), date(2026,6,17)); result=run_candidate('swing_dip_buy', params, data, list(DEMO_SYMBOLS), 1000000.0, 3.0); print(result)"
```
