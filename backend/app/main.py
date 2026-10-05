"""FastAPI: 电梯派梯策略仿真对比服务。

端点:
  GET  /scenarios                  案例列表(楼层需求/容量/门参数/种子)
  GET  /scenarios/{key}            案例详情(含乘客 OD 客流)
  POST /scenarios/{key}/run        对指定策略跑一次仿真
  POST /scenarios/{key}/compare    同一客流跑三种策略并落库
  GET  /runs/{run_id}              单次结果 + 一致性校验
  GET  /runs/{run_id}/events       事件日志(可按类型/乘客过滤)
  GET  /scenarios/{key}/compare/latest  最近一次三策略对照结果
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .db import Event, Run, Scenario, SessionLocal, init_db
from .simulator import STRATEGIES, build_scenario, run_simulation

app = FastAPI(title="电梯派梯策略对比", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"])


def _seed_scenarios():
    """初始化三个预置案例(楼层需求/容量/开关门耗时/随机种子持久化)。"""
    db = SessionLocal()
    try:
        if db.query(Scenario).count() == 0:
            for key in ("morning_up", "cross_floor", "long_door"):
                sc = build_scenario(key, seed=42)
                db.add(Scenario(key=sc["key"], name=sc["name"],
                                description=sc["description"], seed=sc["seed"],
                                config=sc["config"], passengers=sc["passengers"]))
        db.commit()
    finally:
        db.close()


@app.on_event("startup")
def _startup():
    init_db()
    _seed_scenarios()


def _scenario_to_dict(row: Scenario, include_passengers: bool = True) -> dict:
    out = {"id": row.id, "key": row.key, "name": row.name,
           "description": row.description, "seed": row.seed,
           "config": row.config}
    if include_passengers:
        out["passengers"] = row.passengers
        out["passenger_count"] = len(row.passengers)
    return out


@app.get("/scenarios")
def list_scenarios():
    db = SessionLocal()
    try:
        rows = db.query(Scenario).order_by(Scenario.id).all()
        return [_scenario_to_dict(r, include_passengers=False)
                | {"passenger_count": len(r.passengers)} for r in rows]
    finally:
        db.close()


@app.get("/scenarios/{key}")
def get_scenario(key: str):
    db = SessionLocal()
    try:
        row = db.query(Scenario).filter_by(key=key).first()
        if row is None:
            raise HTTPException(404, f"案例不存在: {key}")
        return _scenario_to_dict(row)
    finally:
        db.close()


def _persist_run(db, scenario_row: Scenario, strategy: str, result: dict) -> int:
    run = Run(scenario_id=scenario_row.id, strategy=strategy,
              seed=scenario_row.seed, metrics=result["metrics"],
              validation=result["validation"])
    db.add(run)
    db.flush()
    # 事件落库: 除四要素外其余字段进 payload
    for ev in result["events"]:
        payload = {k: v for k, v in ev.items()
                   if k not in ("seq", "time", "type")}
        db.add(Event(run_id=run.id, seq=ev["seq"], time=ev["time"],
                     type=ev["type"], payload=payload))
    return run.id


@app.post("/scenarios/{key}/run")
def run_one(key: str, strategy: str = Query(..., description=STRATEGIES)):
    if strategy not in STRATEGIES:
        raise HTTPException(400, f"策略必须是 {STRATEGIES}")
    db = SessionLocal()
    try:
        row = db.query(Scenario).filter_by(key=key).first()
        if row is None:
            raise HTTPException(404, f"案例不存在: {key}")
        scenario = {"key": row.key, "seed": row.seed, "config": row.config,
                    "passengers": row.passengers}
        result = run_simulation(scenario, strategy)
        run_id = _persist_run(db, row, strategy, result)
        db.commit()
        return {"run_id": run_id, "metrics": result["metrics"],
                "validation": result["validation"]}
    finally:
        db.close()


@app.post("/scenarios/{key}/compare")
def compare(key: str):
    """同一客流对照三种策略。"""
    db = SessionLocal()
    try:
        row = db.query(Scenario).filter_by(key=key).first()
        if row is None:
            raise HTTPException(404, f"案例不存在: {key}")
        scenario = {"key": row.key, "seed": row.seed, "config": row.config,
                    "passengers": row.passengers}
        out = {}
        for strategy in STRATEGIES:
            result = run_simulation(scenario, strategy)
            run_id = _persist_run(db, row, strategy, result)
            out[strategy] = {"run_id": run_id, "metrics": result["metrics"],
                             "validation": result["validation"]}
        db.commit()
        return {"scenario": key, "seed": row.seed,
                "passenger_count": len(row.passengers), "runs": out}
    finally:
        db.close()


@app.get("/runs/{run_id}")
def get_run(run_id: int):
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "仿真记录不存在")
        return {"run_id": run.id, "scenario_id": run.scenario_id,
                "strategy": run.strategy, "seed": run.seed,
                "metrics": run.metrics, "validation": run.validation}
    finally:
        db.close()


@app.get("/runs/{run_id}/events")
def get_events(run_id: int, type: Optional[str] = None,
               passenger: Optional[int] = None,
               limit: int = Query(10000, le=100000),
               offset: int = 0):
    db = SessionLocal()
    try:
        if db.get(Run, run_id) is None:
            raise HTTPException(404, "仿真记录不存在")
        q = db.query(Event).filter_by(run_id=run_id)
        if type:
            q = q.filter(Event.type == type)
        rows = q.order_by(Event.seq).offset(offset).limit(limit).all()
        events = []
        for r in rows:
            ev = {"seq": r.seq, "time": r.time, "type": r.type, **r.payload}
            if passenger is not None:
                if ev.get("passenger") != passenger:
                    continue
            events.append(ev)
        return {"run_id": run_id, "count": len(events), "events": events}
    finally:
        db.close()


@app.get("/scenarios/{key}/compare/latest")
def latest_compare(key: str):
    db = SessionLocal()
    try:
        sc = db.query(Scenario).filter_by(key=key).first()
        if sc is None:
            raise HTTPException(404, f"案例不存在: {key}")
        out = {}
        for strategy in STRATEGIES:
            run = (db.query(Run)
                     .filter_by(scenario_id=sc.id, strategy=strategy)
                     .order_by(Run.id.desc()).first())
            if run:
                out[strategy] = {"run_id": run.id, "metrics": run.metrics,
                                 "validation": run.validation}
        return {"scenario": key, "runs": out}
    finally:
        db.close()


@app.get("/health")
def health():
    return {"ok": True}
