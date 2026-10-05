"""仿真运行入口: 按客流表与策略运行 SimPy 并附一致性校验。"""
from __future__ import annotations

from typing import Dict

from .demand import config_from_dict
from .engine import ElevatorSim, Passenger
from .validation import validate_events


def run_simulation(scenario: dict, strategy: str) -> dict:
    cfg = config_from_dict(scenario["config"])
    passengers = [Passenger(**p) for p in scenario["passengers"]]
    sim = ElevatorSim(cfg, passengers, strategy, seed=scenario.get("seed", 0))
    metrics = sim.run()
    validation = validate_events(
        sim.events, scenario["passengers"], cfg.capacity, cfg.cars,
        cfg.floors, cfg.horizon)
    return {
        "scenario_key": scenario["key"],
        "strategy": strategy,
        "seed": scenario.get("seed", 0),
        "config": scenario["config"],
        "metrics": metrics,
        "validation": validation,
        "events": sim.events,
    }


def run_all_strategies(scenario: dict) -> Dict[str, dict]:
    """同一客流连续跑三种策略,返回对照结果。"""
    return {s: run_simulation(scenario, s) for s in ("collective", "eta", "zoning")}
