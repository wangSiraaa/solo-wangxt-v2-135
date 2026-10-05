/**
 * 井道可视化: 楼层从上到下排列,轿厢按重放位置平滑移动;
 * 左侧楼层行显示该层候梯队列(上行绿点/下行紫点)及外呼已被哪部梯认领。
 */
import React from 'react'

const FLOOR_H = 42
const CAR_H = 26

export default function Hoistway({ snapshot, config, tracePid, strategy }) {
  const { floors, cars: nCars, capacity } = config
  const height = floors * FLOOR_H
  const floorsDesc = [...Array(floors).keys()].reverse()

  return (
    <div className="hoistway-wrap">
      <div className="hoistway">
        {/* 楼层标签 + 候梯队列 */}
        <div>
          <div className="shaft-label" style={{ height: 22 }}>楼层 / 候梯队列</div>
          <div>
            {floorsDesc.map((f) => {
              const upQ = snapshot.hall[`${f},1`] || []
              const dnQ = snapshot.hall[`${f},-1`] || []
              const upOwn = snapshot.hallOwners[`${f},1`]
              const dnOwn = snapshot.hallOwners[`${f},-1`]
              return (
                <div className="floor-row" key={f} style={{ height: FLOOR_H }}>
                  <div className="floor-label" style={{ width: 38 }}>
                    {f === 0 ? '大堂' : `${f}F`}
                  </div>
                  <div style={{ width: 118, display: 'flex', flexDirection: 'column',
                                justifyContent: 'center', gap: 2 }}>
                    <HallLine q={upQ} dir="u" owners={upOwn} tracePid={tracePid} />
                    <HallLine q={dnQ} dir="d" owners={dnOwn} tracePid={tracePid} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* 各轿厢井道 */}
        {snapshot.cars.map((car) => (
          <div className="shaft" key={car.id}>
            <div className="shaft-label">{car.id + 1}# 梯</div>
            <div className="shaft-col" style={{ height, position: 'relative' }}>
              {/* 层线 */}
              {floorsDesc.map((f) => (
                <div key={f} style={{
                  position: 'absolute', left: 0, right: 0,
                  bottom: f * FLOOR_H + FLOOR_H - 1, height: 1,
                  background: 'rgba(135,148,163,.18)',
                }} />
              ))}
              <div
                className={[
                  'car',
                  car.load >= capacity ? 'full' : '',
                  car.doorsOpen ? 'doors' : '',
                ].join(' ')}
                style={{ bottom: car.y * FLOOR_H + 7 }}
                title={`${car.load}/${capacity} 人${car.doorsOpen ? ' · 开门中' : ''}`}
              >
                {car.load}
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="legend">
        <span><i className="pchip u" /> 上行候梯</span>
        <span><i className="pchip d" /> 下行候梯</span>
        <span style={{ color: '#6fb0ff' }}>■</span><span>轿厢(数字=在轿人数)</span>
        <span style={{ color: 'var(--amber)' }}>■</span><span>满载</span>
        <span><span style={{ color: 'var(--green)' }}>| |</span> 开门</span>
        <span className="hall-assigned">角标 = 外呼认领梯号</span>
      </div>
    </div>
  )
}

function HallLine({ q, dir, owners, tracePid }) {
  if (!q.length) return <div style={{ height: 14 }} />
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 2, height: 14 }}>
      <span className={`hall-dir ${dir}`}>{dir === 'u' ? '▲' : '▼'}</span>
      {q.slice(0, 7).map((pid) => (
        <i key={pid}
           className={`pchip ${dir}${pid === tracePid ? ' trace' : ''}`}
           title={`乘客 ${pid}`} />
      ))}
      {q.length > 7 && <span className="muted" style={{ fontSize: 10 }}>+{q.length - 7}</span>}
      {owners?.length > 0 && (
        <span className="hall-assigned">→{owners.map((c) => c + 1).join(',')}</span>
      )}
    </div>
  )
}
