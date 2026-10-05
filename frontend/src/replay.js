/**
 * 事件日志重放器: 把后端事件日志重建成 t 时刻的物理状态。
 *
 * 与后端校验器同构 —— 每名乘客在任一时刻只有一个位置:
 *   未到达 → 楼层候梯队列 → 某轿厢 → 已到达(或结束时未服务)。
 * 轿厢位置在相邻 car_pass 事件之间做线性插值,实现井道平滑运动;
 * 开关门区间由 door_open / door_close 事件界定。
 */

export function buildReplayer(events, passengers, config) {
  const sorted = [...events].sort((a, b) => a.seq - b.seq)
  const pmap = new Map(passengers.map((p) => [p.id, p]))
  const travel = config.floor_travel_time
  const horizon = config.horizon

  // 每部梯的位移段: 把 car_dispatch/door_close(运动起点路点)与 car_pass(到达路点)
  // 依次连接;停靠时两段之间天然断开,不产生插值。
  const segments = new Map()
  for (let i = 0; i < config.cars; i++) segments.set(i, [])
  // 每部梯的开门区间: {start,end}
  const doorIntervals = new Map()
  for (let i = 0; i < config.cars; i++) doorIntervals.set(i, [])

  // 上一运动起点路点 {t, floor}
  const lastWaypoint = new Map()
  const doorOpenT = new Map()
  for (let i = 0; i < config.cars; i++) lastWaypoint.set(i, { t: 0, floor: 0 })

  for (const ev of sorted) {
    if (ev.type === 'car_dispatch') {
      lastWaypoint.set(ev.car, { t: ev.time, floor: ev.floor })
    } else if (ev.type === 'car_pass') {
      const wp = lastWaypoint.get(ev.car)
      // 连接上一路点 → 本路点(同点零长度段无害)
      segments.get(ev.car).push({
        start: wp.t, end: ev.time, from: wp.floor, to: ev.floor,
      })
      lastWaypoint.set(ev.car, { t: ev.time, floor: ev.floor })
    } else if (ev.type === 'door_open') {
      doorOpenT.set(ev.car, ev.time)
    } else if (ev.type === 'door_close') {
      const start = doorOpenT.get(ev.car) ?? ev.time - 1
      doorIntervals.get(ev.car).push({ start, end: ev.time })
      doorOpenT.delete(ev.car)
      // 关门后若继续运行,下一 car_pass 从此刻出发
      lastWaypoint.set(ev.car, { t: ev.time, floor: ev.floor })
    }
  }

  function carPosition(carId, t) {
    const segs = segments.get(carId)
    // 二分查找所在位移段
    let lo = 0, hi = segs.length - 1, found = null
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      const s = segs[mid]
      if (t < s.start) hi = mid - 1
      else if (t > s.end) lo = mid + 1
      else { found = s; break }
    }
    if (!found) {
      const last = segs[segs.length - 1]
      return last ? last.to : 0
    }
    const ratio = Math.min(1, Math.max(0, (t - found.start) / (found.end - found.start)))
    return found.from + (found.to - found.from) * ratio
  }

  function doorsOpen(carId, t) {
    for (const iv of doorIntervals.get(carId)) {
      if (t >= iv.start && t <= iv.end) return true
    }
    // 已开门但日志末端尚未关门
    return false
  }

  // ---- 离散状态重放(队列/乘员/归属),用指针增量推进 ----
  let cursor = 0
  let stateTime = -1
  let queues = new Map()      // "f,d" -> [passengerId...]
  let onboard = new Map()     // car -> Set(id)
  let owners = new Map()      // "f,d" -> Set(carId)
  let unservedIds = new Set()
  let doneIds = new Set()

  function key(f, d) { return `${f},${d}` }

  function reset() {
    cursor = 0
    stateTime = -1
    queues = new Map()
    onboard = new Map()
    for (let i = 0; i < config.cars; i++) onboard.set(i, [])
    owners = new Map()
    unservedIds = new Set()
    doneIds = new Set()
  }
  reset()

  function apply(ev) {
    switch (ev.type) {
      case 'passenger_arrive': {
        const k = key(ev.origin, ev.direction)
        if (!queues.has(k)) queues.set(k, [])
        queues.get(k).push(ev.passenger)
        break
      }
      case 'passenger_board': {
        const k = key(ev.floor, ev.direction)
        const q = queues.get(k) || []
        const idx = q.indexOf(ev.passenger)
        if (idx >= 0) q.splice(idx, 1)
        onboard.get(ev.car).push(ev.passenger)
        break
      }
      case 'passenger_leave': {
        const arr = onboard.get(ev.car)
        const idx = arr.indexOf(ev.passenger)
        if (idx >= 0) arr.splice(idx, 1)
        doneIds.add(ev.passenger)
        break
      }
      case 'hall_assigned':
      case 'hall_overflow': {
        const k = key(ev.hall_floor, ev.hall_dir)
        if (!owners.has(k)) owners.set(k, new Set())
        owners.get(k).add(ev.car)
        break
      }
      case 'hall_reassigned': {
        const k = key(ev.hall_floor, ev.hall_dir)
        owners.get(k)?.delete(ev.from_car)
        if (!owners.has(k)) owners.set(k, new Set())
        owners.get(k).add(ev.car)
        break
      }
      case 'passenger_unserved': {
        // 从队列或轿厢移除
        for (const q of queues.values()) {
          const i = q.indexOf(ev.passenger)
          if (i >= 0) q.splice(i, 1)
        }
        for (const arr of onboard.values()) {
          const i = arr.indexOf(ev.passenger)
          if (i >= 0) arr.splice(i, 1)
        }
        unservedIds.add(ev.passenger)
        break
      }
      default:
        break
    }
  }

  function advanceTo(t) {
    if (t < stateTime) reset()
    while (cursor < sorted.length && sorted[cursor].time <= t + 1e-9) {
      apply(sorted[cursor])
      stateTime = sorted[cursor].time
      cursor++
    }
    stateTime = Math.max(stateTime, t < 0 ? -1 : stateTime)
  }

  /** 返回 t 时刻完整快照 */
  function snapshot(t) {
    advanceTo(t)
    const cars = []
    for (let i = 0; i < config.cars; i++) {
      cars.push({
        id: i,
        y: carPosition(i, t),
        floor: Math.round(carPosition(i, t)),
        doorsOpen: doorsOpen(i, t),
        onboard: [...onboard.get(i)],
        load: onboard.get(i).length,
      })
    }
    const hall = {}
    for (const [k, q] of queues) {
      if (q.length) hall[k] = [...q]
    }
    const hallOwners = {}
    for (const [k, set] of owners) {
      if (set.size) hallOwners[k] = [...set]
    }
    return {
      t,
      cars,
      hall,
      hallOwners,
      done: doneIds.size,
      unserved: unservedIds.size,
      passengerCount: passengers.length,
    }
  }

  /** 单名乘客轨迹: 到达/候梯/乘梯(连续)/完成 */
  function passengerTrace(passengerId) {
    const p = pmap.get(passengerId)
    if (!p) return null
    const evs = sorted.filter((e) => e.passenger === passengerId)
    const arrive = evs.find((e) => e.type === 'passenger_arrive')
    const board = evs.find((e) => e.type === 'passenger_board')
    const leave = evs.find((e) => e.type === 'passenger_leave')
    const unserved = evs.find((e) => e.type === 'passenger_unserved')
    return {
      passenger: p,
      arriveTime: arrive?.time ?? p.arrival_time,
      boardTime: board?.time ?? null,
      boardCar: board?.car ?? null,
      leaveTime: leave?.time ?? null,
      unserved,
      positionAt(t) {
        if (t < (arrive?.time ?? p.arrival_time)) return null
        if (!board || t < board.time) return { floor: p.origin, where: 'hall' }
        if (!leave || t < leave.time) {
          return { floor: carPosition(board.car, t), where: 'car', car: board.car }
        }
        return { floor: p.dest, where: 'done' }
      },
    }
  }

  return {
    snapshot,
    passengerTrace,
    duration: horizon,
    eventCount: sorted.length,
  }
}
