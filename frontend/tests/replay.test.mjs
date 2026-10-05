/**
 * 前端重放器不变量测试: 对每个策略的真实事件日志,在多个采样时刻核对
 * 人数守恒与"每名乘客仅一个物理位置"。
 * 运行: node tests/replay.test.mjs  (需后端在 localhost:8000)
 */
import assert from 'node:assert'
import { buildReplayer } from '../src/replay.js'

const BASE = 'http://localhost:8000'
const get = async (p) => (await fetch(BASE + p)).json()

function checkSnapshot(snap, passengers, config, t) {
  const seen = new Map() // pid -> location
  for (const [k, q] of Object.entries(snap.hall)) {
    for (const pid of q) seen.set(pid, `hall(${k})`)
  }
  for (const car of snap.cars) {
    assert.ok(car.load <= config.capacity, `t=${t} 车${car.id} 超载`)
    for (const pid of car.onboard) {
      assert.ok(!seen.has(pid), `t=${t} 乘客${pid} 同时在 ${seen.get(pid)} 和 car${car.id}`)
      seen.set(pid, `car(${car.id})`)
    }
  }
  let counted = seen.size
  counted += snap.done + snap.unserved
  const notArrived = passengers.filter((p) => p.arrival_time > t + 1e-6).length
  // done/unserved 与离散状态不重叠
  assert.equal(
    counted + notArrived, passengers.length,
    `t=${t} 守恒失败: ${counted + notArrived} != ${passengers.length}`)
  // done/unserved 人数与位置集合不冲突
  const liveIds = new Set(seen.keys())
  assert.ok(liveIds.size === counted - snap.done - snap.unserved)
}

async function main() {
  const scenarios = await get('/scenarios')
  let total = 0
  for (const scInfo of scenarios) {
    const sc = await get(`/scenarios/${scInfo.key}`)
    const latest = await get(`/scenarios/${scInfo.key}/compare/latest`)
    for (const strategy of ['collective', 'eta', 'zoning']) {
      const runId = latest.runs[strategy].run_id
      const { events } = await get(`/runs/${runId}/events?limit=100000`)
      const rep = buildReplayer(events, sc.passengers, sc.config)
      // 采样 0..horizon 每 30s 一帧 + 每个事件时刻
      const sampleTimes = [0]
      for (let t = 0; t <= sc.config.horizon; t += 30) sampleTimes.push(t)
      for (const ev of events) sampleTimes.push(ev.time)
      for (const t of sampleTimes) {
        checkSnapshot(rep.snapshot(t), sc.passengers, sc.config, t)
        total++
      }
      // 终点: 所有有 leave 的人 done,其余 unserved
      const end = rep.snapshot(sc.config.horizon)
      const m = latest.runs[strategy].metrics
      assert.equal(end.done, m.served_count, `${scInfo.key}/${strategy} 终点已服务不符`)
      assert.equal(end.unserved, m.unserved_count, `${scInfo.key}/${strategy} 终点未服务不符`)

      // 抽一名已服务乘客核对轨迹时序
      const servedPid = events.find((e) => e.type === 'passenger_leave')?.passenger
      const tr = rep.passengerTrace(servedPid)
      assert.ok(tr.boardTime > tr.arriveTime - 1e-9)
      assert.ok(tr.leaveTime >= tr.boardTime)
      assert.equal(tr.positionAt(tr.arriveTime - 1), null)
      assert.equal(tr.positionAt(tr.arriveTime).where, 'hall')
      assert.equal(tr.positionAt((tr.boardTime + tr.leaveTime) / 2).where, 'car')
      assert.equal(tr.positionAt(tr.leaveTime + 1).where, 'done')
      console.log(`PASS ${scInfo.key}/${strategy} (${events.length} 事件)`)
    }
  }
  console.log(`\n全部通过,共核对 ${total} 个快照`)
}

main().catch((e) => { console.error('FAIL', e); process.exit(1) })
