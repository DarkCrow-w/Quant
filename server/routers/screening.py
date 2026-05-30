from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.models.screening import (
    FactorDef,
    FactorStrategy,
    FactorStrategyDraft,
    CompositeMetricDef,
    CompositeScanRequest,
    CompositeScanResult,
    ScoreRequest,
    ScoreResult,
    ScreenRequest,
    ScreenResult,
)
from server.services.screening_service import (
    get_factor_defs,
    run_scoring,
    run_screening,
)
from server.services.factor_strategy_service import (
    FactorStrategyStore,
    get_metric_defs,
    run_composite_scan,
)

router = APIRouter(prefix="/api/screening", tags=["screening"])
strategy_store = FactorStrategyStore()


@router.post("/scan", response_model=ScreenResult)
def api_scan(req: ScreenRequest):
    try:
        return run_screening(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/score", response_model=ScoreResult)
def api_score(req: ScoreRequest):
    try:
        return run_scoring(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/factors", response_model=list[FactorDef])
def api_factors():
    try:
        return get_factor_defs()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/composer/metrics", response_model=list[CompositeMetricDef])
def api_composer_metrics():
    return get_metric_defs()


@router.get("/composer/strategies", response_model=list[FactorStrategy])
def api_composer_strategies():
    return strategy_store.list()


@router.post("/composer/strategies", response_model=FactorStrategy)
def api_create_composer_strategy(draft: FactorStrategyDraft):
    return strategy_store.save(draft)


@router.put("/composer/strategies/{strategy_id}", response_model=FactorStrategy)
def api_update_composer_strategy(strategy_id: str, draft: FactorStrategyDraft):
    if strategy_store.get(strategy_id) is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return strategy_store.save(draft, strategy_id)


@router.delete("/composer/strategies/{strategy_id}")
def api_delete_composer_strategy(strategy_id: str):
    if not strategy_store.delete(strategy_id):
        raise HTTPException(status_code=404, detail="strategy not found")
    return {"status": "deleted"}


@router.post("/composer/scan", response_model=CompositeScanResult)
def api_composer_scan(req: CompositeScanRequest):
    try:
        return run_composite_scan(req, strategy_store)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
