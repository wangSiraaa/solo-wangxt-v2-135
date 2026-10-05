/** 三策略指标对照表: 候梯 / 乘梯 / 总行程分列,最优值标绿;未服务单列不剔除。 */
import React from 'react'
import { STRATEGY_LABELS } from '../api'

const ORDER = ['collective', 'eta', 'zoning']

function fmt(v) { return v == null ? '—' : v.toFixed(1) }

export default function MetricsTable({ compare, active, onSelect }) {
  const runs = compare?.runs || {}
  const rows = ORDER.filter((s) => runs[s]).map((s) => ({ s, m: runs[s].metrics }))
  if (!rows.length) return null

  // 每行一个指标,挑选"最优"(总行程平均越小越好,服务数越大越好)
  const minOf = (sel) => Math.min(...rows.map(({ m }) => sel(m) ?? Infinity))
  const bestWait = minOf((m) => m.waiting.avg)
  const bestRide = minOf((m) => m.riding.avg)
  const bestTotal = minOf((m) => m.total_journey.avg)
  const bestP95 = minOf((m) => m.total_journey.p95)
  const bestMaxWait = minOf((m) => m.waiting.max)
  const bestDist = minOf((m) => m.car_distance_floors)

  return (
    <table>
      <thead>
        <tr>
          <th>策略</th>
          <th>服务/总客流</th>
          <th>未服务</th>
          <th>候梯均值</th>
          <th>候梯最大</th>
          <th>乘梯均值</th>
          <th>总行程均值</th>
          <th>总行程 P95</th>
          <th>停站</th>
          <th>行驶层</th>
          <th>满载滞留</th>
          <th>校验</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ s, m }) => {
          const v = runs[s].validation
          return (
            <tr key={s}
                style={s === active ? { background: 'rgba(77,163,255,.08)',
                                        cursor: 'pointer' } : { cursor: 'pointer' }}
                onClick={() => onSelect?.(s)}>
              <td>
                <b>{STRATEGY_LABELS[s]}</b>
                {s === active && <span className="tag ok" style={{ marginLeft: 6 }}>播放中</span>}
              </td>
              <td>{m.served_count} / {m.passengers_total}</td>
              <td style={{ color: m.unserved_count ? 'var(--amber)' : undefined }}>
                {m.unserved_count}
                <span className="muted"> ({(m.service_rate * 100).toFixed(1)}%)</span>
              </td>
              <td className={m.waiting.avg === bestWait ? 'best' : ''}>{fmt(m.waiting.avg)}</td>
              <td className={m.waiting.max === bestMaxWait ? 'best' : ''}>{fmt(m.waiting.max)}</td>
              <td className={m.riding.avg === bestRide ? 'best' : ''}>{fmt(m.riding.avg)}</td>
              <td className={m.total_journey.avg === bestTotal ? 'best' : ''}>{fmt(m.total_journey.avg)}</td>
              <td className={m.total_journey.p95 === bestP95 ? 'best' : ''}>{fmt(m.total_journey.p95)}</td>
              <td>{m.stop_count}</td>
              <td className={m.car_distance_floors === bestDist ? 'best' : ''}>{m.car_distance_floors}</td>
              <td style={{ color: m.full_load_rejections ? 'var(--amber)' : undefined }}>
                {m.full_load_rejections}
              </td>
              <td>
                <span className={`tag ${v.ok && v.conservation_ok ? 'ok' : 'bad'}`}>
                  {v.ok && v.conservation_ok ? '通过' : '异常'}
                </span>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
