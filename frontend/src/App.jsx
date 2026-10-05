import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { api, STRATEGY_LABELS } from './api'
import { buildReplayer } from './replay'
import Hoistway from './components/Hoistway'
import MetricsTable from './components/MetricsTable'
import Player from './components/Player'
import EventLog from './components/EventLog'
import TrajectoryPanel from './components/TrajectoryPanel'

const STRATEGIES = ['collective', 'eta', 'zoning']

export default function App() {
  const [scenarios, setScenarios] = useState([])
  const [scenarioKey, setScenarioKey] = useState('morning_up')
  const [scenario, setScenario] = useState(null)
  const [compare, setCompare] = useState(null)
  const [strategy, setStrategy] = useState('collective')
  const [events, setEvents] = useState([])
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(5)
  const [tracePid, setTracePid] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.scenarios().then(setScenarios).catch((e) => setError(String(e)))
  }, [])

  const loadScenario = useCallback(async (key, rerun = false) => {
    setBusy(true); setError(''); setPlaying(false); setTime(0); setTracePid(null)
    try {
      const sc = await api.scenario(key)
      setScenario(sc)
      let cmp
      if (rerun) {
        cmp = await api.compare(key)
      } else {
        // 优先用库里最近一次对照;若没有则现场跑
        const latest = await fetch(`/scenarios/${key}/compare/latest`).then((r) => r.json())
        cmp = Object.keys(latest.runs || {}).length === 3
          ? { runs: latest.runs }
          : await api.compare(key)
      }
      setCompare(cmp)
      setStrategy('collective')
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => { loadScenario(scenarioKey) }, [scenarioKey, loadScenario])

  // 切换策略时拉取该 run 的事件日志
  useEffect(() => {
    const runId = compare?.runs?.[strategy]?.run_id
    if (!runId) { setEvents([]); return }
    setTime(0); setTracePid(null)
    api.events(runId, { limit: 100000 }).then((d) => setEvents(d.events))
  }, [compare, strategy])

  const replayer = useMemo(() => {
    if (!scenario || !events.length) return null
    return buildReplayer(events, scenario.passengers, scenario.config)
  }, [events, scenario])

  const snapshot = useMemo(
    () => (replayer ? replayer.snapshot(time) : null),
    [replayer, time])

  const trace = useMemo(
    () => (replayer && tracePid != null ? replayer.passengerTrace(tracePid) : null),
    [replayer, tracePid])

  const queueTotal = snapshot
    ? Object.values(snapshot.hall).reduce((n, q) => n + q.length, 0)
    : 0
  const carLoadTotal = snapshot
    ? snapshot.cars.reduce((n, c) => n + c.load, 0)
    : 0
  const notArrived = scenario && snapshot
    ? scenario.passengers.filter((p) => p.arrival_time > time).length
    : 0

  return (
    <div className="app">
      <header>
        <h1>楼宇电梯派梯策略对比仿真</h1>
        <div className="sub">
          SimPy 离散事件仿真 · React 井道回放 · 同一客流(相同随机种子)对照集选 / ETA / 静态分区
        </div>
      </header>

      <div className="panel">
        <div className="row">
          <label className="muted">客流案例</label>
          <select value={scenarioKey} onChange={(e) => setScenarioKey(e.target.value)}>
            {scenarios.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
          </select>
          <button onClick={() => loadScenario(scenarioKey, true)} disabled={busy}>
            {busy ? '仿真中…' : '重新对照运行'}
          </button>
          <div className="spacer" />
          {scenario && (
            <span className="muted">
              seed={scenario.seed} · {scenario.passenger_count} 名乘客 ·
              {' '}{scenario.config.floors} 层 / {scenario.config.cars} 梯 ·
              容量 {scenario.config.capacity} · 行驶 {scenario.config.floor_travel_time}s/层 ·
              开/关门 {scenario.config.door_open_time}/{scenario.config.door_close_time}s
            </span>
          )}
        </div>
        {scenario && <div className="muted" style={{ marginTop: 8 }}>{scenario.description}</div>}
        {error && <div className="err" style={{ marginTop: 8 }}>{error}</div>}
      </div>

      <div className="panel">
        <h2>同一客流 · 策略指标对照</h2>
        <MetricsTable compare={compare} active={strategy} onSelect={setStrategy} />
        <div className="muted" style={{ marginTop: 8 }}>
          候梯时间 = 到达至上车;乘梯时间 = 上车至目的层;总行程 = 两者之和。
          仿真结束仍未到站者计入"未服务",不参与已服务均值、也不被静默剔除。点击行切换播放策略。
        </div>
      </div>

      <div className="panel">
        <div className="row" style={{ marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>井道回放</h2>
          <div className="tabs">
            {STRATEGIES.map((s) => (
              <button key={s} className={s === strategy ? 'active' : ''}
                      onClick={() => setStrategy(s)}>
                {STRATEGY_LABELS[s]}
              </button>
            ))}
          </div>
          <div className="spacer" />
          {snapshot && (
            <span className="muted">
              已到达 <b style={{ color: 'var(--green)' }}>{snapshot.done}</b> ·
              候梯 <b style={{ color: 'var(--amber)' }}>{queueTotal}</b> ·
              在轿 <b style={{ color: 'var(--accent)' }}>{carLoadTotal}</b> ·
              未服务 <b style={{ color: snapshot.unserved ? 'var(--red)' : undefined }}>
                {snapshot.unserved}
              </b>
              {' '}· 未到达 {notArrived}
              {' '}(守恒 {snapshot.done + queueTotal + carLoadTotal + snapshot.unserved
                + notArrived === snapshot.passengerCount ? '✓' : '✗'})
            </span>
          )}
        </div>

        {snapshot && scenario && (
          <div className="viz-grid">
            <div>
              <Hoistway snapshot={snapshot} config={scenario.config}
                        tracePid={tracePid} strategy={strategy} />
              <div style={{ marginTop: 14 }}>
                <Player time={time} setTime={setTime}
                        duration={replayer.duration}
                        playing={playing} setPlaying={setPlaying}
                        speed={speed} setSpeed={setSpeed} />
              </div>
            </div>

            <div className="side-stack">
              <TrajectoryPanel passengers={scenario.passengers}
                               tracePid={tracePid} setTracePid={setTracePid}
                               trace={trace} time={time} />
              <div className="panel" style={{ margin: 0 }}>
                <h2>事件日志</h2>
                <EventLog events={events} time={time} tracePid={tracePid} />
              </div>
            </div>
          </div>
        )}
        {!snapshot && <div className="muted">{busy ? '正在运行仿真…' : '加载中…'}</div>}
      </div>
    </div>
  )
}
