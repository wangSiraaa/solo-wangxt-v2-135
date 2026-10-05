"""SimPy 电梯仿真引擎。

模型要点:
- 乘客是不可分割实体,任一时刻只可能处于三处之一: 楼层候梯队列 hall(f,d)、
  某轿厢 car(i)、已到达 done。事件日志记录每次位置转移,可据此核对同一乘客
  在同一时刻只有一个物理位置。
- 三种派梯策略对照同一客流(同一 seed 生成的乘客表):
    * collective: 传统集选(SCAN),新外呼由最近空闲梯认领,途经可自由插入停站;
    * eta:        ETA 预估最近梯独占指派;
    * zoning:     静态分区(低区/高区组),大堂上行两组各认领一次,超时溢出兜底。
- 轿厢满载时未上车者留在原楼层队列,该外呼保持 active 并在关门后重新分配。
- 仿真 horizon 截止时仍未到站的乘客标记 unserved,指标单独统计,绝不静默丢弃。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import simpy

STRATEGIES = ("collective", "eta", "zoning")


@dataclass(frozen=True)
class Passenger:
    id: int
    origin: int
    dest: int
    arrival_time: float

    @property
    def direction(self) -> int:
        return 1 if self.dest > self.origin else -1


@dataclass
class BuildingConfig:
    floors: int = 12                      # 楼层 0..floors-1,0 为大堂
    cars: int = 4
    capacity: int = 13
    floor_travel_time: float = 2.0        # 相邻层行驶时间(s)
    door_open_time: float = 1.0           # 开门动作耗时
    door_close_time: float = 1.0          # 关门动作耗时
    per_person_time: float = 0.8          # 每名乘客进出占用时间
    min_dwell: float = 2.0                # 最短停靠时间(开门后保持)
    horizon: float = 1800.0               # 仿真时长(s)
    zoning_overflow_after: float = 60.0   # 分区外呼等待超时(溢出到他组)


@dataclass
class CarState:
    id: int
    floor: int
    direction: int = 0           # 1 上行 / -1 下行 / 0 空闲
    doors_open: bool = False
    onboard: List[Passenger] = field(default_factory=list)
    # 停站承诺: (楼层, 方向) — 本梯已认领要在该层按该方向接人的外呼
    committed: set = field(default_factory=set)


class ElevatorSim:
    def __init__(self, cfg: BuildingConfig, passengers: List[Passenger], strategy: str,
                 seed: int = 0):
        assert strategy in STRATEGIES, f"未知策略: {strategy}"
        self.cfg = cfg
        self.passengers = passengers
        self.strategy = strategy
        self.seed = seed

        self.env = simpy.Environment()
        self.cars: List[CarState] = [CarState(i, 0) for i in range(cfg.cars)]
        # 候梯队列: (楼层, 方向) -> 仍在该层该方向排队的乘客(FIFO)
        self.hall_queues: Dict[Tuple[int, int], List[Passenger]] = {
            (f, d): [] for f in range(cfg.floors) for d in (1, -1)
        }
        # 外呼归属: (楼层, 方向) -> 已认领轿厢 id 集合(collective/zoning 可多梯)
        self.owners: Dict[Tuple[int, int], set] = {}
        # 外呼首次出现时间(zoning 溢出判定用)
        self.hall_birth: Dict[Tuple[int, int], float] = {}
        # 乘客当前物理位置(不变量核对): hall(f,d) / car(i) / done / unserved / absent
        self.where: Dict[int, str] = {p.id: "absent" for p in passengers}
        # 乘客上车时刻(指标用)
        self.board_time: Dict[int, float] = {}
        self.served: List[Passenger] = []
        self.unserved: List[Passenger] = []
        self.events: List[dict] = []
        self._seq = 0

        # 分区: 低区组服务 1..split,高区组服务 split..top,大堂两组共服务
        self.split_floor = cfg.floors // 2
        half = max(1, cfg.cars // 2)
        self.low_group = self.cars[:half]
        self.high_group = self.cars[half:]
        self.low_ids = {c.id for c in self.low_group}

        self.wake_event = simpy.Event(self.env)

    # ------------------------------------------------------------------ 工具
    def _emit(self, etype: str, **kw):
        self._seq += 1
        ev = {"seq": self._seq, "time": round(self.env.now, 3), "type": etype}
        ev.update(kw)
        self.events.append(ev)

    def _move(self, p: Passenger, location: str):
        """记录乘客物理位置转移(不变量: 同一时刻仅一个位置)。"""
        self.where[p.id] = location

    def _active_halls(self) -> List[Tuple[int, int]]:
        return [h for h, q in self.hall_queues.items() if q]

    def _wake(self):
        ev, self.wake_event = self.wake_event, simpy.Event(self.env)
        ev.succeed()

    def _is_low(self, car_id: int) -> bool:
        return car_id in self.low_ids

    def _zoning_pickup_ok(self, car: CarState, floor: int, direction: int) -> bool:
        """分区策略下某梯是否允许在该层按该方向接外呼(按外呼方向判断目的地区)。"""
        if floor == 0:
            return True                                   # 大堂上行两组都接
        if self._is_low(car.id):                          # 低区 1..split
            return floor < self.split_floor or (
                floor == self.split_floor and direction == -1)
        return floor > self.split_floor or (              # 高区 split..top
            floor == self.split_floor and direction == 1)

    def _zoning_dest_ok(self, car: CarState, dest: int) -> bool:
        """大堂筛选: 低区组只载目的层 <= split,高区组 >= split(split 两组均可)。"""
        if self._is_low(car.id):
            return dest <= self.split_floor
        return dest >= self.split_floor

    # ------------------------------------------------------------------ ETA 预估
    def _stop_cost(self) -> float:
        return self.cfg.door_open_time + self.cfg.min_dwell + self.cfg.door_close_time

    def _estimate_eta(self, car: CarState, hall: Tuple[int, int]) -> float:
        """沿当前运行方向清空已有承诺后再去 hall 的粗粒度预估(只读干跑)。"""
        floor, _ = hall
        cf, cd = car.floor, car.direction
        targets = {f for (f, d) in car.committed} | {p.dest for p in car.onboard}

        if cd == 0 or not targets:
            path = abs(floor - cf)
            stops_region = set()
        else:
            ahead = [f for f in targets if (f - cf) * cd > 0]
            if ahead:
                extreme = max(ahead) if cd == 1 else min(ahead)
                if (floor - cf) * cd >= 0 and (extreme - floor) * cd >= 0:
                    path = abs(floor - cf)               # hall 在当前方向途中
                else:
                    path = abs(extreme - cf) + abs(floor - extreme)
            else:
                path = abs(floor - cf)
            stops_region = targets
        lo, hi = sorted((cf, floor))
        stops = len({f for f in stops_region if lo <= f <= hi})
        return (path * self.cfg.floor_travel_time
                + stops * self._stop_cost()
                + len(car.onboard) * 0.3)

    # ------------------------------------------------------------------ 调度
    def _eligible_idle_cars(self, hall: Tuple[int, int]) -> List[CarState]:
        floor, direction = hall
        if self.strategy == "zoning":
            return [c for c in self.cars if c.direction == 0
                    and self._zoning_pickup_ok(c, floor, direction)]
        return [c for c in self.cars if c.direction == 0]

    def _dispatch_new_hall(self, hall: Tuple[int, int]):
        """新外呼出现时的初始指派(同一物理呼梯键不重复派梯)。"""
        floor, direction = hall
        owners = self.owners.setdefault(hall, set())
        idle = self._eligible_idle_cars(hall)

        if self.strategy == "eta":
            if owners:
            # ETA 独占: 一个外呼只指派一部梯
                return
            best = min(self.cars, key=lambda c: (self._estimate_eta(c, hall), c.id))
            owners.add(best.id)
            best.committed.add(hall)
            self._emit("hall_assigned", hall_floor=floor, hall_dir=direction,
                       car=best.id)
            if best.direction == 0:
                self._wake()
        elif self.strategy == "collective":
            # 最近空闲梯认领;无空闲时外呼保持"浮动",途经梯按 SCAN 自由停站
            if idle:
                best = min(idle, key=lambda c: (abs(c.floor - floor), c.id))
                owners.add(best.id)
                best.committed.add(hall)
                self._emit("hall_assigned", hall_floor=floor, hall_dir=direction,
                           car=best.id)
                self._wake()
        else:  # zoning: 大堂上行每个可服务组各派一部,其余按组派一部
            groups = (self.low_group, self.high_group) if floor == 0 else (
                self.low_group if self._zoning_pickup_ok(
                    self.low_group[0], floor, direction) else self.high_group,)
            chosen = []
            for group in groups:
                gids = {c.id for c in group}
                if owners & gids:
                    continue
                cand = [c for c in idle if c.id in gids]
                if cand:
                    chosen.append(min(cand, key=lambda c: (abs(c.floor - floor), c.id)))
            for best in chosen:
                owners.add(best.id)
                best.committed.add(hall)
                self._emit("hall_assigned", hall_floor=floor, hall_dir=direction,
                           car=best.id)
            if chosen:
                self._wake()

    def _redispatch_leftover(self, hall: Tuple[int, int], served_by: int):
        """关门后外呼仍有人(满载/分区被过滤),重新安排运力。"""
        floor, direction = hall
        owners = self.owners.setdefault(hall, set())
        self.cars[served_by].committed.discard(hall)
        owners.discard(served_by)
        if not self.hall_queues[hall]:
            return
        # 仍有其他认领者(典型: zoning 大堂两组共接)→ 交给它,不重复派
        if owners:
            return

        if self.strategy == "eta":
            candidates = sorted(self.cars, key=lambda c: (self._estimate_eta(c, hall),
                                                          c.id))
            best = next((c for c in candidates if c.id != served_by), candidates[0])
            owners.add(best.id)
            best.committed.add(hall)
            self._emit("hall_reassigned", hall_floor=floor, hall_dir=direction,
                       car=best.id, from_car=served_by, reason="full")
            if best.direction == 0:
                self._wake()
        elif self.strategy == "collective":
            idle = [c for c in self.cars if c.direction == 0 and c.id != served_by]
            if idle:
                best = min(idle, key=lambda c: (abs(c.floor - floor), c.id))
                owners.add(best.id)
                best.committed.add(hall)
                self._emit("hall_reassigned", hall_floor=floor, hall_dir=direction,
                           car=best.id, from_car=served_by, reason="full")
                self._wake()
            # 无空闲则保持浮动,途经 SCAN 梯会停
        else:  # zoning: 先同组空闲,再溢出异组
            same = [c for c in self._eligible_idle_cars(hall) if c.id != served_by]
            best = min(same, key=lambda c: (abs(c.floor - floor), c.id),
                       default=None)
            if best is None:
                other = [c for c in self.cars
                         if c.direction == 0 and c.id != served_by]
                best = min(other, key=lambda c: (abs(c.floor - floor), c.id),
                           default=None)
                if best is not None:
                    self._emit("hall_overflow", hall_floor=floor, hall_dir=direction,
                               car=best.id, from_car=served_by, reason="full")
            if best is not None:
                owners.add(best.id)
                best.committed.add(hall)
                self._emit("hall_reassigned", hall_floor=floor, hall_dir=direction,
                           car=best.id, from_car=served_by, reason="full")
                self._wake()

    # ------------------------------------------------------------------ 目标选择
    def _answered_halls(self, car: CarState) -> set:
        """本梯当前应答的外呼: 已认领 + collective 浮动外呼(无人认领)。"""
        answered = set(car.committed)
        if self.strategy == "collective":
            for hall in self._active_halls():
                if not self.owners.get(hall):
                    answered.add(hall)
        return answered

    def _choose_target(self, car: CarState) -> Optional[int]:
        targets = {f for (f, d) in self._answered_halls(car)} | \
                  {p.dest for p in car.onboard}
        if not targets:
            return None
        if car.direction == 0:
            return min(targets, key=lambda f: (abs(f - car.floor), f))
        if car.direction == 1:
            ahead = [f for f in targets if f >= car.floor]
            return max(ahead) if ahead else min(targets)
        ahead = [f for f in targets if f <= car.floor]
        return min(ahead) if ahead else max(targets)

    def _is_intermediate_stop(self, car: CarState, floor: int) -> bool:
        """运行途中该层是否停: 内选必停;同向呼梯停;反向呼梯只在换向端点停。"""
        if any(p.dest == floor for p in car.onboard):
            return True
        hall = (floor, car.direction)
        if hall in car.committed:
            return True
        if self.strategy == "collective" and self.hall_queues[hall] \
                and not self.owners.get(hall):
            return True
        if self.strategy == "zoning" and hall in car.committed:
            return True
        return False

    # ------------------------------------------------------------------ 接人判定
    def _boarding_dirs(self, car: CarState, is_terminal: bool) -> List[int]:
        """停站后按哪些方向从候梯队列接人(端点原向优先,原向无客才换向)。"""
        f = car.floor

        def boardable(d: int) -> bool:
            if not self.hall_queues[(f, d)]:
                return False
            if (f, d) in car.committed:
                return True
            if self.strategy == "collective" and not self.owners.get((f, d)):
                return True
            return False

        if car.direction != 0 and not is_terminal:
            return [car.direction] if boardable(car.direction) else []

        primary = car.direction if car.direction != 0 else (
            1 if self.hall_queues[(f, 1)] else -1)
        if boardable(primary):
            return [primary]
        other = -primary
        return [other] if boardable(other) else []

    def _can_take(self, car: CarState, p: Passenger, pickup_dir: int) -> bool:
        if len(car.onboard) >= self.cfg.capacity:
            return False
        if self.strategy == "zoning":
            if car.floor == 0 and not self._zoning_dest_ok(car, p.dest):
                return False
            # 溢出到异组的承诺不受限;其余接客承诺本身已按分区过滤
            if (car.floor, pickup_dir) in car.committed:
                return True
            if not self._zoning_pickup_ok(car, car.floor, pickup_dir):
                return False
        if car.direction != 0 and pickup_dir == car.direction:
            if (p.dest - car.floor) * car.direction < 0:
                return False
        return True

    # ------------------------------------------------------------------ 轿厢进程
    def _car_process(self, car: CarState):
        while True:
            target = self._choose_target(car)
            if target is None:
                car.direction = 0
                self._emit("car_idle", car=car.id, floor=car.floor)
                yield self.wake_event
                continue
            if car.direction == 0:
                car.direction = 1 if target >= car.floor else -1
                self._emit("car_dispatch", car=car.id, floor=car.floor,
                           target=target, direction=car.direction)
            while car.floor != target:
                yield self.env.timeout(self.cfg.floor_travel_time)
                car.floor += car.direction
                self._emit("car_pass", car=car.id, floor=car.floor,
                           direction=car.direction, load=len(car.onboard),
                           target=target)
                if car.floor != target and self._is_intermediate_stop(
                        car, car.floor):
                    target = car.floor           # 途经插入停站
            yield self.env.process(self._service_stop(car, target))
            # 根据剩余承诺决定换向
            new_target = self._choose_target(car)
            if new_target is not None:
                car.direction = 1 if new_target > car.floor else -1

    def _service_stop(self, car: CarState, floor: int):
        # 端点判定: 当前运行方向前方是否还有任何承诺目标
        remaining = {f for (f, d) in self._answered_halls(car)} | \
                    {p.dest for p in car.onboard}
        if car.direction == 1:
            is_terminal = not any(f > floor for f in remaining)
        elif car.direction == -1:
            is_terminal = not any(f < floor for f in remaining)
        else:
            is_terminal = True

        alighting = [p for p in car.onboard if p.dest == floor]
        self._emit("car_arrive", car=car.id, floor=floor, direction=car.direction,
                   load=len(car.onboard), alighting=len(alighting),
                   terminal=is_terminal)
        car.doors_open = True
        yield self.env.timeout(self.cfg.door_open_time)
        self._emit("door_open", car=car.id, floor=floor)

        # 先下: 内选必停必下,不受外呼归属影响
        for p in alighting:
            car.onboard.remove(p)
            self._move(p, "done")
            self.served.append(p)
            self._emit("passenger_leave", passenger=p.id, car=car.id, floor=floor,
                       dest=p.dest, boarded_at=self.board_time[p.id],
                       load=len(car.onboard))

        # 后上: 按方向依次从 FIFO 队列接人,满载即止,未上车者留队
        dirs = self._boarding_dirs(car, is_terminal)
        boarded_count = 0
        leftover_halls: List[Tuple[int, int]] = []
        for d in dirs:
            hall = (floor, d)
            queue = self.hall_queues[hall]
            # 大堂分区共用队列: 允许越过非本区队首接本区客(群控实际行为);
            # 其余情形严格 FIFO,队首不可接即停(满载者保留在原队列)。
            scan = self.strategy == "zoning" and floor == 0
            idx = 0
            while idx < len(queue):
                if len(car.onboard) >= self.cfg.capacity:
                    break
                p = queue[idx]
                if self._can_take(car, p, d):
                    queue.pop(idx)
                    car.onboard.append(p)
                    self.board_time[p.id] = self.env.now
                    self._move(p, f"car({car.id})")
                    boarded_count += 1
                    self._emit("passenger_board", passenger=p.id, car=car.id,
                               floor=floor, dest=p.dest, direction=d,
                               load=len(car.onboard))
                    continue                       # 已弹出,不前进 idx
                if scan:
                    idx += 1
                else:
                    break
            if queue:
                leftover_halls.append(hall)
                self._emit("hall_left_behind", hall_floor=floor, hall_dir=d,
                           car=car.id, waiting=len(queue),
                           load=len(car.onboard))
            else:
                # 外呼完成: 清掉所有梯对它的承诺
                self.owners.pop(hall, None)
                self.hall_birth.pop(hall, None)
                for c in self.cars:
                    c.committed.discard(hall)

        # 停靠耗时: 最短停靠与乘客进出占用取大;再关门
        movers = len(alighting) + boarded_count
        hold = max(self.cfg.min_dwell, movers * self.cfg.per_person_time)
        yield self.env.timeout(hold)
        car.doors_open = False
        yield self.env.timeout(self.cfg.door_close_time)
        self._emit("door_close", car=car.id, floor=floor,
                   alighted=len(alighting), boarded=boarded_count,
                   load=len(car.onboard), dwell=round(
                       self.cfg.door_open_time + hold + self.cfg.door_close_time, 2))

        # 关门后为仍滞留的外呼重新安排运力
        for hall in leftover_halls:
            self._redispatch_leftover(hall, car.id)

    # ------------------------------------------------------------------ 乘客到达
    def _passenger_arrivals(self):
        for p in self.passengers:
            yield self.env.timeout(max(0.0, p.arrival_time - self.env.now))
            hall = (p.origin, p.direction)
            self.hall_queues[hall].append(p)
            self._move(p, f"hall({p.origin},{p.direction})")
            self.hall_birth.setdefault(hall, self.env.now)
            self._emit("passenger_arrive", passenger=p.id, origin=p.origin,
                       dest=p.dest, arrival=round(self.env.now, 3),
                       direction=p.direction, waiting=len(self.hall_queues[hall]))
            self._dispatch_new_hall(hall)

    # ------------------------------------------------------------------ zoning 超时溢出
    def _overflow_monitor(self):
        while True:
            yield self.env.timeout(5.0)
            if self.strategy != "zoning":
                continue
            now = self.env.now
            for hall in list(self._active_halls()):
                floor, direction = hall
                if now - self.hall_birth.get(hall, now) < self.cfg.zoning_overflow_after:
                    continue
                owners = self.owners.setdefault(hall, set())
                # 同组已无可用空闲梯时,唤醒异组空闲梯支援
                helper = next((c for c in self.cars
                               if c.direction == 0 and c.id not in owners
                               and not self._zoning_pickup_ok(c, floor, direction)),
                              None)
                if helper is None:
                    continue
                owners.add(helper.id)
                helper.committed.add(hall)
                self._emit("hall_overflow", hall_floor=floor, hall_dir=direction,
                           car=helper.id, wait=round(now - self.hall_birth[hall], 1))
                self._wake()

    # ------------------------------------------------------------------ 主入口
    def run(self):
        for car in self.cars:
            self.env.process(self._car_process(car))
        self.env.process(self._passenger_arrivals())
        self.env.process(self._overflow_monitor())
        self.env.run(until=self.cfg.horizon)

        # 截止时仍在候/在轿: 标记 unserved,计入指标,绝不静默丢弃
        for hall, queue in self.hall_queues.items():
            for p in queue:
                self._move(p, "unserved")
                self.unserved.append(p)
                self._emit("passenger_unserved", passenger=p.id, origin=p.origin,
                           dest=p.dest, state="waiting",
                           waited=round(self.cfg.horizon - p.arrival_time, 1))
        for car in self.cars:
            for p in car.onboard:
                self._move(p, "unserved")
                self.unserved.append(p)
                self._emit("passenger_unserved", passenger=p.id, origin=p.origin,
                           dest=p.dest, state=f"in_car_{car.id}",
                           waited=round(self.board_time.get(p.id, p.arrival_time)
                                        - p.arrival_time, 1))
        return self._summary()

    def _summary(self) -> dict:
        board_t = {ev["passenger"]: ev["time"] for ev in self.events
                   if ev["type"] == "passenger_board"}
        leave_t = {ev["passenger"]: ev["time"] for ev in self.events
                   if ev["type"] == "passenger_leave"}
        wait_times, ride_times, total_times = [], [], []
        for p in self.served:
            w = board_t[p.id] - p.arrival_time
            wait_times.append(w)
            ride_times.append(leave_t[p.id] - board_t[p.id])
            total_times.append(leave_t[p.id] - p.arrival_time)

        def stats(xs):
            if not xs:
                return {"avg": None, "max": None, "min": None, "p95": None,
                        "count": 0}
            s = sorted(xs)
            idx = min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)
            return {"avg": round(sum(xs) / len(xs), 2), "max": round(max(xs), 2),
                    "min": round(min(xs), 2), "p95": round(s[idx], 2),
                    "count": len(xs)}

        return {
            "strategy": self.strategy,
            "seed": self.seed,
            "horizon": self.cfg.horizon,
            "passengers_total": len(self.passengers),
            "served_count": len(self.served),
            "unserved_count": len(self.unserved),
            "unserved_ids": [p.id for p in self.unserved],
            "service_rate": round(len(self.served) / len(self.passengers), 4)
                           if self.passengers else 1.0,
            "waiting": stats(wait_times),
            "riding": stats(ride_times),
            "total_journey": stats(total_times),
            "car_distance_floors": sum(
                1 for ev in self.events if ev["type"] == "car_pass"),
            "stop_count": sum(
                1 for ev in self.events if ev["type"] == "car_arrive"),
            "full_load_rejections": sum(
                1 for ev in self.events if ev["type"] == "hall_left_behind"),
        }
