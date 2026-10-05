"""仿真核心单元测试: 守恒、满载留队、未服务不剔除、策略对照同客流。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.simulator import (BuildingConfig, ElevatorSim, Passenger,
                           build_scenario, run_all_strategies, run_simulation)
from app.simulator.demand import config_from_dict


def test_simple_up_trip():
    """单人 0→5: 必被服务,候梯=0 量级,乘梯=5 层行驶+停站。"""
    cfg = BuildingConfig(floors=6, cars=1, capacity=8)
    pax = [Passenger(1, 0, 5, 10.0)]
    sim = ElevatorSim(cfg, pax, "collective")
    m = sim.run()
    assert m["served_count"] == 1
    assert m["unserved_count"] == 0
    assert m["waiting"]["max"] <= 5.0
    assert m["riding"]["min"] >= 5 * cfg.floor_travel_time - 0.01


def test_full_capacity_leaves_passenger_in_queue():
    """容量=2、3 人同层上行: 第三人留在原队列,由同一梯二次接走。"""
    cfg = BuildingConfig(floors=6, cars=1, capacity=2, min_dwell=1.0)
    pax = [Passenger(1, 0, 3, 0.0), Passenger(2, 0, 4, 0.0),
           Passenger(3, 0, 5, 0.0)]
    sim = ElevatorSim(cfg, pax, "collective")
    m = sim.run()
    assert m["served_count"] == 3
    assert m["unserved_count"] == 0
    assert m["full_load_rejections"] >= 1
    # 第三人的候梯 = 往返到高层再回大堂,明显更长
    board = {e["passenger"]: e["time"] for e in sim.events
             if e["type"] == "passenger_board"}
    assert board[3] > board[1] + 5 * cfg.floor_travel_time


def test_direction_filtering():
    """轿厢上行途中不接反向乘客: 下行乘客须等换向。"""
    cfg = BuildingConfig(floors=8, cars=1, capacity=8, min_dwell=1.0)
    # 0→7 的客先到,t=1 时 3→2 的下行客到达;上行梯过 3 层不得接他
    pax = [Passenger(1, 0, 7, 0.0), Passenger(2, 3, 2, 1.0)]
    sim = ElevatorSim(cfg, pax, "collective")
    m = sim.run()
    assert m["served_count"] == 2
    boards = {e["passenger"]: e for e in sim.events
              if e["type"] == "passenger_board"}
    # 下行客只能在 7F 换向之后被接(接走楼层必须是其候梯层 3)
    assert boards[2]["floor"] == 3
    assert boards[2]["direction"] == -1
    assert boards[2]["time"] > boards[1]["time"]


def test_unserved_at_horizon_not_dropped():
    """到达太晚来不及服务: 计入 unserved,服务率分母仍是全部客流。"""
    cfg = BuildingConfig(floors=10, cars=1, capacity=8, horizon=30.0)
    pax = [Passenger(1, 0, 9, 0.0), Passenger(2, 0, 9, 25.0)]
    sim = ElevatorSim(cfg, pax, "collective")
    m = sim.run()
    assert m["passengers_total"] == 2
    assert m["served_count"] == 1
    assert m["unserved_count"] == 1
    assert m["unserved_ids"] == [2]
    assert m["service_rate"] == 0.5


def test_same_demand_deterministic_across_runs():
    """同一 seed/客流多次运行结果完全一致。"""
    sc = build_scenario("morning_up", seed=7)
    r1 = run_simulation(sc, "eta")
    r2 = run_simulation(sc, "eta")
    assert r1["metrics"] == r2["metrics"]
    assert len(r1["events"]) == len(r2["events"])


def test_all_scenarios_strategies_valid():
    """三案例 × 三策略: 全部通过位置一致性校验与人数守恒。"""
    for key in ("morning_up", "cross_floor", "long_door"):
        sc = build_scenario(key, seed=42)
        results = run_all_strategies(sc)
        for s, r in results.items():
            v = r["validation"]
            assert v["ok"], f"{key}/{s}: {v['violations'][:5]}"
            assert v["conservation_ok"]
            assert v["served"] + v["unserved"] == len(sc["passengers"])
            m = r["metrics"]
            assert m["served_count"] + m["unserved_count"] == m["passengers_total"]
            # 已服务均值的样本数必须等于服务人数,不得混入未服务者
            assert m["waiting"]["count"] == m["served_count"]
            assert m["total_journey"]["count"] == m["served_count"]


def test_long_door_hurts_vs_normal():
    """同一客流长开门案例下各指标不优于普通门(候梯/总行程)。"""
    normal = build_scenario("morning_up", seed=42)
    longd = build_scenario("long_door", seed=42)
    # 客流必须一致
    assert normal["passengers"] == longd["passengers"]
    for s in ("collective", "eta", "zoning"):
        a = run_simulation(normal, s)["metrics"]
        b = run_simulation(longd, s)["metrics"]
        assert b["total_journey"]["avg"] >= a["total_journey"]["avg"]


def test_zoning_destination_filter_at_lobby():
    """大堂分流: 低区组不得载走高区目的层乘客(由高区组接)。"""
    cfg = BuildingConfig(floors=12, cars=4, capacity=13)
    sc_cfg = {**cfg.__dict__}
    # 直接跑早高峰,抽查 board 事件: 0 层上车的客若在低区车(0/1),dest<=6
    sc = build_scenario("morning_up", seed=42)
    sc["config"] = {**sc["config"]}
    r = run_simulation(sc, "zoning")
    split = sc["config"]["floors"] // 2
    half = max(1, sc["config"]["cars"] // 2)
    for ev in r["events"]:
        if ev["type"] == "passenger_board" and ev["floor"] == 0:
            if ev["car"] < half:
                assert ev["dest"] <= split
            else:
                assert ev["dest"] >= split


if __name__ == "__main__":
    import inspect
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and inspect.isfunction(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
