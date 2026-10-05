"""客流生成: 同一 seed 下生成确定性乘客表,供不同策略对照。

三个案例:
- morning_up:  早高峰上行(大堂为主,叠加层间上行与少量下行)
- cross_floor: 跨层需求(午餐时段: 下行去大堂、上行返回、层间互访混合)
- long_door:   与早高峰同一客流,但拉长开/关门与停靠耗时,隔离门控参数影响
"""
from __future__ import annotations

import random
from dataclasses import asdict
from typing import Dict, List

from .engine import BuildingConfig, Passenger

FLOORS = 12
HORIZON = 1800.0


def _make(rng: random.Random, origin: int, dest: int, t: float,
          next_id: int) -> Passenger:
    return Passenger(id=next_id, origin=origin, dest=dest, arrival_time=round(t, 2))


def _morning_demand(seed: int) -> List[Passenger]:
    """早高峰: 0~30min,60% 大堂上行 + 20% 层间上行 + 20% 下行(返家/送错)。"""
    rng = random.Random(seed * 100003 + 17)
    pax: List[Passenger] = []
    pid = 1
    t = 0.0
    while t < HORIZON:
        t += rng.expovariate(1 / 9.0)          # 约 200 人 / 30min
        if t >= HORIZON:
            break
        r = rng.random()
        if r < 0.60:
            origin, dest = 0, rng.randint(1, FLOORS - 1)
        elif r < 0.80:
            origin = rng.randint(1, FLOORS - 3)
            dest = rng.randint(origin + 1, FLOORS - 1)
        else:
            origin = rng.randint(1, FLOORS - 1)
            dest = rng.randint(0, origin - 1)
        pax.append(_make(rng, origin, dest, t, pid))
        pid += 1
    return pax


def _cross_floor_demand(seed: int) -> List[Passenger]:
    """午餐跨层: 35% 各层→大堂,25% 大堂→各层,40% 层间互访(双向)。"""
    rng = random.Random(seed * 100003 + 53)
    pax: List[Passenger] = []
    pid = 1
    t = 0.0
    while t < HORIZON:
        t += rng.expovariate(1 / 11.0)
        if t >= HORIZON:
            break
        r = rng.random()
        if r < 0.35:
            origin, dest = rng.randint(1, FLOORS - 1), 0
        elif r < 0.60:
            origin, dest = 0, rng.randint(1, FLOORS - 1)
        else:
            origin = rng.randint(1, FLOORS - 1)
            choices = [f for f in range(1, FLOORS) if f != origin]
            dest = rng.choice(choices)
        pax.append(_make(rng, origin, dest, t, pid))
        pid += 1
    return pax


def scenario_specs() -> List[dict]:
    """返回三个预置案例定义(客流函数 + 建筑参数)。"""
    base = dict(floors=FLOORS, cars=4, capacity=13, floor_travel_time=2.0,
                door_open_time=1.0, door_close_time=1.0, per_person_time=0.8,
                min_dwell=2.0, horizon=HORIZON, zoning_overflow_after=60.0)
    long_door = {**base, "door_open_time": 5.0, "door_close_time": 4.0,
                 "min_dwell": 4.0}
    return [
        {"key": "morning_up", "name": "早高峰上行",
         "description": "30 分钟内约 200 人:60% 大堂上行、20% 层间上行、20% 下行。",
         "demand": "morning", "config": base},
        {"key": "cross_floor", "name": "跨层需求(午餐)",
         "description": "下行去大堂、上行返回与层间互访混合,方向频繁反转。",
         "demand": "cross", "config": base},
        {"key": "long_door", "name": "长开门时间",
         "description": "与早高峰完全相同客流,开门 5s/关门 4s/最短停靠 4s。",
         "demand": "morning", "config": long_door},
    ]


def build_scenario(key: str, seed: int = 42) -> dict:
    spec = next((s for s in scenario_specs() if s["key"] == key), None)
    if spec is None:
        raise ValueError(f"未知案例: {key}")
    if spec["demand"] == "morning":
        pax = _morning_demand(seed)
    else:
        pax = _cross_floor_demand(seed)
    return {
        "key": spec["key"],
        "name": spec["name"],
        "description": spec["description"],
        "seed": seed,
        "config": spec["config"],
        "passengers": [asdict(p) for p in pax],
    }


def config_from_dict(d: dict) -> BuildingConfig:
    allowed = BuildingConfig.__dataclass_fields__
    return BuildingConfig(**{k: v for k, v in d.items() if k in allowed})
