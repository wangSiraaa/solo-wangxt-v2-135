# 楼宇电梯派梯策略对比仿真

用于楼宇交通课程:在**同一客流**(相同随机种子生成的乘客 OD 表)上对照不同电梯
派梯策略,用 SimPy 离散事件仿真驱动,React 回放井道/候梯队列/乘客轨迹,
PostgreSQL 持久化楼层需求、轿厢容量、开关门耗时与随机种子。**不接真实电梯硬件**。

## 三种派梯策略

| 策略 | 行为 |
|---|---|
| `collective` 集选 SCAN | 新外呼由最近空闲梯认领;无空闲时外呼"浮动",任一途经同方向梯自由插入停站 |
| `eta` ETA 指派 | 按当前承诺停站干跑预估每部梯的到达时间,最近者**独占**外呼 |
| `zoning` 静态分区 | 低区组(1–6F)/高区组(6–11F),大堂上行两组同时迎客并按目的层筛选,超时 60s 溢出支援 |

## 三个客流案例(均可三策略对照)

- **早高峰上行** `morning_up`:30 分钟约 200 人,60% 大堂上行 + 20% 层间上行 + 20% 下行
- **跨层需求** `cross_floor`:午餐时段下行去大堂/上行返回/层间互访混合,方向频繁反转
- **长开门时间** `long_door`:与早高峰**完全相同客流**,开门 5s、关门 4s、最短停靠 4s,隔离门控参数影响

## 指标口径(关键)

- **候梯时间** = 乘客到达楼层时刻 → 上车时刻
- **乘梯时间** = 上车 → 目的层下车
- **总行程** = 候梯 + 乘梯;报告均值/最大/最小/P95
- 仿真 `horizon`(默认 1800s)截止时仍在候梯或困在轿厢中的乘客标记为
  **未服务(unserved)**:单列计数与服务率,**不参与已服务均值、也不被静默剔除**,
  避免"只统计服务完的人"而夸大策略效果
- **满载留队**:轿厢达到容量后未上车者保留在原楼层 FIFO 队列,外呼保持 active,
  关门后触发改派(日志 `hall_left_behind` / `hall_reassigned`)
- 接人方向约束:上行途中只接同向(目的层更高)乘客,反向客只在换向端点接

## 物理位置一致性(可核对)

每名乘客在任一时刻只可能处于一个物理位置:

```
未到达 absent → 楼层候梯队列 hall(f,d) → 某轿厢 car(i) → 到达 done
                                                  └→ horizon 截止: unserved(显式标记)
```

- 后端 `app/simulator/validation.py` 重放全部事件日志校验:状态机顺序、队列/轿厢乘员集合
  不重叠、载重不超容量、队列人数非负、board/leave 各一次、人数守恒
- 前端回放器 `src/replay.js` 在 30s 间隔 + 每个事件时刻重放快照,顶栏实时显示
  `已到达 + 候梯 + 在轿 + 未服务 + 未到达 = 总客流` 守恒标记
- 可在"乘客轨迹核对"中选择任一名乘客,查看其候梯/乘梯分段耗时、当前唯一物理位置,
  事件日志同步高亮该乘客的全部事件

## 运行

### Docker(PostgreSQL)

```bash
docker compose up --build
# 前端 http://localhost:5173  后端 http://localhost:8000/docs
```

### 本地开发(无 PG 时自动降级 SQLite)

```bash
# 后端
cd backend
pip install -r requirements.txt          # 不含 psycopg2 也可,默认用 SQLite
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install && npm run dev               # http://localhost:5173
```

使用 PostgreSQL 时设置:
`DATABASE_URL=postgresql+psycopg2://elevator:elevator@localhost:5432/elevator`

### 测试

```bash
cd backend && python3 tests/test_engine.py          # 8 项仿真核心测试
cd frontend && node tests/replay.test.mjs            # 回放不变量(需后端运行,3 万+快照)
```

## API 摘要

| 端点 | 说明 |
|---|---|
| `GET /scenarios` / `GET /scenarios/{key}` | 案例与乘客 OD 客流(容量/门参数/种子) |
| `POST /scenarios/{key}/compare` | 同一客流跑三种策略,指标与全部事件落库 |
| `POST /scenarios/{key}/run?strategy=eta` | 单策略运行 |
| `GET /runs/{id}` | 指标 + 一致性校验结果 |
| `GET /runs/{id}/events?type=&passenger=` | 事件日志,可按类型/乘客过滤核对轨迹 |

## 数据表

- `scenarios`:案例参数(config JSONB:楼层数/轿厢数/容量/行驶与开关门耗时/horizon)
  与乘客 OD 到达表、随机种子
- `runs`:每次(案例 × 策略)的指标与校验结果
- `events`:逐事件日志(时间、类型、载荷 JSONB),用于前端回放与审计
