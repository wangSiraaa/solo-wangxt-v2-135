/** 乘客轨迹: 选择乘客后展示其 候梯→乘梯→到达 各阶段耗时与当前物理位置。 */
import React, { useMemo } from 'react'
import { fmtTime } from './Player'

export default function TrajectoryPanel({ passengers, tracePid, setTracePid,
                                          trace, time }) {
  const sorted = useMemo(
    () => [...passengers].sort((a, b) => a.arrival_time - b.arrival_time),
    [passengers])

  const p = tracePid != null ? passengers.find((x) => x.id === tracePid) : null
  const pos = trace ? trace.positionAt(time) : null

  let wait = null, ride = null, total = null
  if (trace?.boardTime != null) wait = trace.boardTime - trace.arriveTime
  if (trace?.leaveTime != null) {
    ride = trace.leaveTime - trace.boardTime
    total = trace.leaveTime - trace.arriveTime
  }

  // 行程条比例(候梯黄 / 乘梯蓝),未完成部分画虚框
  const endT = trace?.leaveTime ?? Math.max(time, trace?.arriveTime ?? 0)
  const span = Math.max(endT - (p?.arrival_time ?? 0), 1)
  const waitW = wait != null ? wait / span : Math.max(0, time - (p?.arrival_time ?? 0)) / span

  return (
    <div className="panel">
      <h2>乘客轨迹核对</h2>
      <div className="row" style={{ marginBottom: 8 }}>
        <select
          value={tracePid ?? ''}
          onChange={(e) => setTracePid(e.target.value === '' ? null : Number(e.target.value))}
        >
          <option value="">— 选择乘客 —</option>
          {sorted.map((p) => (
            <option key={p.id} value={p.id}>
              #{p.id}　{p.origin}F → {p.dest}F　到达 {fmtTime(p.arrival_time)}
            </option>
          ))}
        </select>
      </div>

      {!p && <div className="muted">选择一名乘客,核对其任一时刻唯一的物理位置与各阶段耗时。</div>}

      {p && (
        <div className="trajectory-box">
          <div className="kv">
            <span>编号</span><b>#{p.id}</b>
            <span>起终层</span><b>{p.origin}F → {p.dest}F ({p.dest > p.origin ? '上行' : '下行'})</b>
            <span>到达时刻</span><b>{fmtTime(p.arrival_time)}</b>
            <span>上车时刻</span><b>{trace.boardTime != null ? `${fmtTime(trace.boardTime)} · ${trace.boardCar + 1}# 梯` : '—'}</b>
            <span>到站时刻</span><b>{trace.leaveTime != null ? fmtTime(trace.leaveTime) : '—'}</b>
          </div>

          <div style={{ margin: '10px 0 6px' }}>
            <div>
              <span className="bar-seg wait" style={{ width: `${Math.min(100, waitW * 100)}%` }} />
              <span className="bar-seg ride"
                    style={{ width: `${trace.leaveTime != null
                      ? Math.max(0, (1 - waitW) * 100) : 0}%` }} />
            </div>
            <div className="muted" style={{ marginTop: 4 }}>
              候梯 {wait != null ? `${wait.toFixed(1)}s` : trace.unserved ? '未上车' : '候梯中…'}
              {' · '}
              乘梯 {ride != null ? `${ride.toFixed(1)}s` : '—'}
              {' · '}
              总行程 {total != null ? `${total.toFixed(1)}s`
                : trace.unserved ? '未完成(计入未服务,不剔除)' : '进行中…'}
            </div>
          </div>

          <div className="stat-line">
            <span>当前物理位置</span>
            <b>
              {pos == null && <span className="muted">尚未到达</span>}
              {pos?.where === 'hall' && <span style={{ color: 'var(--amber)' }}>
                {p.origin}F 候梯队列 {p.dest > p.origin ? '▲' : '▼'}</span>}
              {pos?.where === 'car' && <span style={{ color: 'var(--accent)' }}>
                {pos.car + 1}# 轿厢内(约 {pos.floor.toFixed(1)}F)</span>}
              {pos?.where === 'done' && <span style={{ color: 'var(--green)' }}>
                已到达 {p.dest}F</span>}
            </b>
          </div>
        </div>
      )}
    </div>
  )
}
