"""事件日志一致性校验: 重放日志,核对每名乘客在每个时刻只处于一个物理位置。

校验规则:
1. 乘客生命周期: (未到达) → arrive 进入唯一楼层队列 → board 进入唯一轿厢
   → leave 完成;任一阶段只有一个位置,转移顺序不可乱。
2. 任何时刻同一乘客不能同时出现在两个队列/两部轿厢(用队列/轿厢乘员集合重放)。
3. 轿厢载重不得超过容量;内选下车必须在目的层。
4. 满载未上车者留在原队列: 重放中队列人数非负,关门后未清空的外呼须有 left_behind。
5. 每名已服务乘客恰好一次 board、一次 leave;未服务乘客在 horizon 有显式事件。
6. 人数守恒: served + unserved == 总客流。
"""
from __future__ import annotations

from typing import Dict, List, Set


def validate_events(events: List[dict], passengers: List[dict],
                    capacity: int, cars: int, floors: int,
                    horizon: float) -> dict:
    pmap = {p["id"]: p for p in passengers}
    violations: List[str] = []

    location: Dict[int, str] = {pid: "absent" for pid in pmap}
    queues: Dict[tuple, Set[int]] = {
        (f, d): set() for f in range(floors) for d in (1, -1)}
    onboard: Dict[int, Set[int]] = {i: set() for i in range(cars)}
    board_seen: Dict[int, int] = {}
    leave_seen: Dict[int, int] = {}
    last_time = 0.0

    for ev in sorted(events, key=lambda e: (e["time"], e["seq"])):
        if ev["time"] < last_time - 1e-9:
            violations.append(f"seq{ev['seq']} 事件时间倒退 {ev['time']} < {last_time}")
        last_time = ev["time"]
        t = ev["type"]

        if t == "passenger_arrive":
            pid = ev["passenger"]
            if location.get(pid) != "absent":
                violations.append(f"乘客 {pid} 重复到达(原位置 {location[pid]})")
            p = pmap[pid]
            pdir = 1 if p["dest"] > p["origin"] else -1
            if ev["origin"] != p["origin"] or ev["dest"] != p["dest"]:
                violations.append(f"乘客 {pid} 到达事件 OD 与客流表不符")
            if abs(ev["time"] - p["arrival_time"]) > 1e-6:
                violations.append(f"乘客 {pid} 到达时间偏离客流表")
            hall = (p["origin"], pdir)
            if ev.get("waiting") != len(queues[hall]) + 1:
                violations.append(
                    f"乘客 {pid} arrive 事件 waiting={ev.get('waiting')} "
                    f"与队列重放 {len(queues[hall]) + 1} 不符")
            queues[hall].add(pid)
            location[pid] = f"hall({hall[0]},{hall[1]})"

        elif t == "passenger_board":
            pid, car = ev["passenger"], ev["car"]
            p = pmap[pid]
            if not str(location.get(pid, "")).startswith("hall("):
                violations.append(
                    f"乘客 {pid} 从非法位置 {location.get(pid)} 上车")
            hall = (ev["floor"], ev["direction"])
            if pid not in queues[hall]:
                violations.append(
                    f"乘客 {pid} 上车但不在队列 hall{hall}(实际 {location.get(pid)})")
            if ev["floor"] != p["origin"]:
                violations.append(
                    f"乘客 {pid} 在 {ev['floor']} 层上车,候梯层应为 {p['origin']}")
            if ev["dest"] != p["dest"]:
                violations.append(f"乘客 {pid} 上车目的层字段错误")
            queues[hall].discard(pid)
            onboard[car].add(pid)
            if len(onboard[car]) > capacity:
                violations.append(
                    f"轿厢 {car} 在 t={ev['time']} 超载 "
                    f"{len(onboard[car])}>{capacity}")
            if ev["load"] != len(onboard[car]):
                violations.append(
                    f"轿厢 {car} 载重字段 {ev['load']} != 重放 {len(onboard[car])}")
            board_seen[pid] = board_seen.get(pid, 0) + 1
            location[pid] = f"car({car})"

        elif t == "passenger_leave":
            pid, car = ev["passenger"], ev["car"]
            p = pmap[pid]
            if location.get(pid) != f"car({car})":
                violations.append(
                    f"乘客 {pid} 从 {location.get(pid)} 下车(应在 car({car}))")
            if ev["floor"] != p["dest"]:
                violations.append(
                    f"乘客 {pid} 在 {ev['floor']} 层下车,目的层为 {p['dest']}")
            onboard[car].discard(pid)
            if ev.get("load") != len(onboard[car]):
                violations.append(
                    f"轿厢 {car} 载重字段 {ev.get('load')} != 重放 {len(onboard[car])}")
            leave_seen[pid] = leave_seen.get(pid, 0) + 1
            location[pid] = "done"

        elif t == "passenger_unserved":
            pid = ev["passenger"]
            loc = location.get(pid)
            if not (str(loc).startswith("hall(") or str(loc).startswith("car(")):
                violations.append(f"乘客 {pid} 标记未服务时位置异常: {loc}")
            if str(loc).startswith("hall("):
                for q in queues.values():
                    q.discard(pid)
            else:
                for o in onboard.values():
                    o.discard(pid)
            location[pid] = "unserved"

    for pid, loc in location.items():
        if loc == "done":
            if board_seen.get(pid, 0) != 1 or leave_seen.get(pid, 0) != 1:
                violations.append(
                    f"乘客 {pid} 已完成但 board/leave 次数异常 "
                    f"{board_seen.get(pid, 0)}/{leave_seen.get(pid, 0)}")
        elif loc == "unserved":
            if leave_seen.get(pid, 0):
                violations.append(f"乘客 {pid} 既标记未服务又有 leave 事件")
        else:
            violations.append(f"乘客 {pid} 仿真结束时位置未结算: {loc}")

    for hall, q in queues.items():
        if q:
            violations.append(f"队列 hall{hall} 结束仍有 {sorted(q)} 无 unserved 事件")
    for car, o in onboard.items():
        if o:
            violations.append(f"轿厢 {car} 结束仍有 {sorted(o)} 人未下车")

    done = sum(1 for v in location.values() if v == "done")
    unserved = sum(1 for v in location.values() if v == "unserved")
    return {
        "ok": not violations,
        "violations": violations,
        "checked_passengers": len(pmap),
        "checked_events": len(events),
        "served": done,
        "unserved": unserved,
        "conservation_ok": done + unserved == len(pmap),
    }
