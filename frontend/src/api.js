const BASE = ''

async function get(path) {
  const res = await fetch(BASE + path)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

async function post(path) {
  const res = await fetch(BASE + path, { method: 'POST' })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

export const api = {
  scenarios: () => get('/scenarios'),
  scenario: (key) => get(`/scenarios/${key}`),
  compare: (key) => post(`/scenarios/${key}/compare`),
  events: (runId, { type, passenger, limit } = {}) => {
    const q = new URLSearchParams()
    if (type) q.set('type', type)
    if (passenger != null) q.set('passenger', passenger)
    if (limit) q.set('limit', limit)
    const qs = q.toString()
    return get(`/runs/${runId}/events${qs ? `?${qs}` : ''}`)
  },
}

export const STRATEGY_LABELS = {
  collective: '集选 SCAN',
  eta: 'ETA 指派',
  zoning: '静态分区',
}
