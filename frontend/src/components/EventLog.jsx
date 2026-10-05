/** 事件日志: 跟随播放时间滚动,关键事件高亮;选定乘客时其事件加底色。 */
import React, { useEffect, useMemo, useRef } from 'react'
import { fmtTime } from './Player'

const IMPORTANT = new Set([
  'hall_left_behind', 'hall_reassigned', 'hall_overflow',
  'passenger_unserved', 'passenger_board', 'passenger_leave',
])

const LABELS = {
  passenger_arrive: (e) => `乘客${e.passenger} 到达 ${e.origin}F → ${e.dest}F`,
  car_dispatch: (e) => `${e.car + 1}# 梯 从${e.floor}F 出发 向${e.direction > 0 ? '上' : '下'} 目标${e.target}F`,
  car_pass: (e) => `${e.car + 1}# 梯 经过 ${e.floor}F (载${e.load})`,
  car_arrive: (e) => `${e.car + 1}# 梯 到达 ${e.floor}F ${e.terminal ? '[换向端点]' : ''} 下${e.alighting}人`,
  door_open: (e) => `${e.car + 1}# 梯 ${e.floor}F 开门`,
  door_close: (e) => `${e.car + 1}# 梯 ${e.floor}F 关门 (上${e.boarded} 下${e.alighted} 留${e.load})`,
  passenger_board: (e) => `乘客${e.passenger} 登上 ${e.car + 1}# 梯 @${e.floor}F → ${e.dest}F (载${e.load})`,
  passenger_leave: (e) => `乘客${e.passenger} 离开 ${e.car + 1}# 梯 到达 ${e.dest}F`,
  hall_assigned: (e) => `${e.hall_floor}F ${e.hall_dir > 0 ? '上行' : '下行'}外呼 → ${e.car + 1}# 梯`,
  hall_reassigned: (e) => `${e.hall_floor}F 外呼 改派 ${e.from_car + 1}#→${e.car + 1}# (${e.reason === 'full' ? '前梯满载' : e.reason})`,
  hall_overflow: (e) => `${e.hall_floor}F 外呼 溢出支援 ${e.car + 1}# 梯${e.wait != null ? ` (已等${e.wait}s)` : ''}`,
  hall_left_behind: (e) => `${e.hall_floor}F ${e.hall_dir > 0 ? '上行' : '下行'}仍有 ${e.waiting} 人等候 (${e.car + 1}# 载${e.load} 未接完)`,
  passenger_unserved: (e) => `乘客${e.passenger} ${e.origin}F→${e.dest}F 仿真结束仍未服务(${e.state})`,
  car_idle: (e) => `${e.car + 1}# 梯 空闲 @${e.floor}F`,
}

export default function EventLog({ events, time, tracePid }) {
  const boxRef = useRef(null)

  // 当前窗口: 已发生且最近 90 条(或选定乘客的全部相关事件)
  const view = useMemo(() => {
    const past = events.filter((e) => e.time <= time + 0.01)
    const filtered = tracePid != null
      ? past.filter((e) => e.passenger === tracePid)
      : past
    return filtered.slice(-90)
  }, [events, time, tracePid])

  useEffect(() => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [view.length])

  return (
    <div className="eventlog" ref={boxRef}>
      {view.length === 0 && <div className="muted">尚无事件…</div>}
      {view.map((e) => (
        <div key={e.seq}
          className={[
            'ev',
            IMPORTANT.has(e.type) ? 'important' : '',
            tracePid != null && e.passenger === tracePid ? 'traceev' : '',
          ].join(' ')}>
          <span className="t">{fmtTime(e.time)}</span>
          <span>{LABELS[e.type]?.(e) ?? e.type}</span>
        </div>
      ))}
    </div>
  )
}
