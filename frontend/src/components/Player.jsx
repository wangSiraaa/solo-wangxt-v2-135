/** 时间轴播放器: 播放/暂停、拖动、倍速;驱动父组件 simTime。 */
import React, { useEffect, useRef } from 'react'

export function fmtTime(t) {
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

const SPEEDS = [1, 2, 5, 10, 20]

export default function Player({ time, setTime, duration, playing, setPlaying,
                                 speed, setSpeed }) {
  const rafRef = useRef(0)
  const lastRef = useRef(performance.now())
  // 用 ref 让 RAF 始终读到最新 time/speed
  const timeRef = useRef(time)
  const speedRef = useRef(speed)
  timeRef.current = time
  speedRef.current = speed

  useEffect(() => {
    if (!playing) return
    lastRef.current = performance.now()
    const tick = (now) => {
      const dt = (now - lastRef.current) / 1000
      lastRef.current = now
      const next = timeRef.current + dt * speedRef.current
      if (next >= duration) {
        setTime(duration)
        setPlaying(false)
        return
      }
      setTime(next)
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [playing, duration, setTime, setPlaying])

  return (
    <div className="controls">
      <button className="primary" onClick={() => {
        if (time >= duration) setTime(0)
        setPlaying(!playing)
      }}>
        {playing ? '⏸ 暂停' : '▶ 播放'}
      </button>
      <button onClick={() => setTime(0)} disabled={time === 0}>⏮ 复位</button>
      <input
        type="range"
        min={0}
        max={duration}
        step={0.1}
        value={time}
        onChange={(e) => { setPlaying(false); setTime(Number(e.target.value)) }}
      />
      <span className="time-readout">
        {fmtTime(time)} / {fmtTime(duration)}
      </span>
      <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
        {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
      </select>
    </div>
  )
}
