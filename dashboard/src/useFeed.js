import { useEffect, useRef, useState } from 'react'
import { fmtMoney, vendorName } from './lib.js'

// Subscribes to the backend's SSE feed (which tails a MongoDB Change Stream).
// One `snapshot` event seeds the state; each `change` event merges one document.
// Agent-driven writes also produce a notification (toast) describing what happened.
export function useFeed() {
  const [state, setState] = useState({
    connected: false,
    live_metrics: {},
    financials: [],
    recommendations: [],
    promotions: [],
    purchase_orders: [],
    agent_events: [],
    raw_ingredients: [],
    orders: [],
    waste_7d_cents: 0,
    sends_seen: 0,
    sim_now: null,
    notifications: [],
  })
  const esRef = useRef(null)

  function dismiss(id) {
    setState((s) => ({ ...s, notifications: s.notifications.filter((n) => n.id !== id) }))
  }

  // lets the sim controls snap the clock to the new day's midnight on ▶
  function setSimNow(ts) {
    setState((s) => ({ ...s, sim_now: ts }))
  }

  // The clock only moves FORWARD: after ▶ snaps to the new day's midnight,
  // stale as_of values riding on unrelated change events must not revert it.
  // (RFC3339 strings compare lexicographically.)
  function laterTs(cur, incoming) {
    if (!incoming) return cur
    return !cur || incoming > cur ? incoming : cur
  }

  useEffect(() => {
    let stopped = false

    function connect() {
      const es = new EventSource('/api/feed')
      esRef.current = es

      es.addEventListener('snapshot', (e) => {
        const snap = JSON.parse(e.data)
        setState((s) => ({ ...s, ...snap, connected: true }))
      })

      es.addEventListener('change', (e) => {
        const { collection, operation, document: doc } = JSON.parse(e.data)
        if (!doc && operation !== 'delete') return
        setState((s) => {
          const notifications = notify(s.notifications, collection, operation, doc)
          switch (collection) {
            case 'live_metrics':
              return { ...s, notifications, live_metrics: doc,
                       sim_now: laterTs(s.sim_now, doc.as_of) }
            case 'financials':
              return { ...s, notifications, financials: upsert(s.financials, doc, '_id', 'asc') }
            case 'promotion_recommendations':
              return { ...s, notifications, recommendations: upsert(s.recommendations, doc, 'recommendation_id') }
            case 'promotions': {
              // change events lack the computed notified_count — preserve it
              const prev = s.promotions.find((p) => p.promo_id === doc.promo_id)
              const merged = { ...doc, notified_count: prev?.notified_count ?? 0 }
              return { ...s, notifications, promotions: upsert(s.promotions, merged, 'promo_id') }
            }
            case 'purchase_orders':
              // key by _id, not po_id — LLM-minted po_ids have collided before
              return { ...s, notifications, purchase_orders: upsert(s.purchase_orders, doc, '_id').slice(0, 10) }
            case 'agent_events':
              return { ...s, notifications, agent_events: upsert(s.agent_events, doc, 'event_id').slice(0, 15) }
            case 'raw_ingredients': {
              const raw = s.raw_ingredients.map((r) =>
                r.ingredient_id === doc.ingredient_id ? { ...r, ...doc } : r)
              return { ...s, notifications, raw_ingredients: raw }
            }
            case 'orders':
              if (operation !== 'insert') return s // ignore flag updates (depleted etc.)
              return {
                ...s, notifications,
                orders: upsert(s.orders, doc, 'order_id').slice(0, 5),
                sim_now: laterTs(s.sim_now, doc.opened_at),
              }
            case 'waste_events':
              if (operation !== 'insert') return s
              return { ...s, notifications,
                       waste_7d_cents: s.waste_7d_cents + (doc.cost_cents || 0) }
            case 'campaign_sends': {
              if (operation !== 'insert') return s // redemption-flag updates aren't sends
              const promotions = s.promotions.map((p) =>
                p.promo_id === doc.promo_id
                  ? { ...p, notified_count: (p.notified_count || 0) + 1 } : p)
              return { ...s, notifications, promotions, sends_seen: s.sends_seen + 1 }
            }
            default:
              return s
          }
        })
      })

      es.onerror = () => {
        es.close()
        setState((s) => ({ ...s, connected: false }))
        if (!stopped) setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      stopped = true
      esRef.current?.close()
    }
  }, [])

  return { ...state, dismiss, setSimNow }
}

// Turns agent-driven writes into tagged ledger-entry toasts. Rapid
// campaign_sends inserts for the same promo merge into one counting toast.
function notify(notifications, collection, operation, doc) {
  const id = `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
  let n = null

  if (collection === 'purchase_orders' && operation === 'insert') {
    const items = doc.line_items?.length || 0
    n = {
      id, tag: 'Inventory', tone: '',
      text: `Ordered ${items} ingredient${items === 1 ? '' : 's'} from ` +
        `${vendorName(doc.vendor_id)} — ${fmtMoney(doc.total_money)}, eta ${doc.expected_delivery?.slice(5, 10) || '?'}`,
    }
  } else if (collection === 'purchase_orders' && operation === 'update' && doc.status === 'received') {
    n = { id, tag: 'Delivery', tone: 'good',
          text: `Received from ${vendorName(doc.vendor_id)}` }
  } else if (collection === 'promotion_recommendations' && operation === 'insert') {
    n = { id, tag: 'Proposal', tone: 'accent',
          text: `“${doc.proposal?.title}” — awaiting your approval` }
  } else if (collection === 'promotions' && operation === 'insert') {
    n = { id, tag: 'Promo live', tone: 'accent',
          text: `“${doc.title}” — ${doc.discount_value}% off` }
  } else if (collection === 'waste_events' && operation === 'insert') {
    n = { id, tag: 'Waste', tone: 'warn',
          text: `Tossed ${doc.qty} ${doc.unit} of ${doc.name} — ` +
          `${fmtMoney(doc.cost_cents)} past shelf life` }
  } else if (collection === 'agent_events' && operation === 'insert') {
    // phase "started" = instant heads-up the moment a trigger fires
    n = { id, tag: doc.phase === 'started' ? 'Working' : `${doc.agent} agent`,
          tone: doc.phase === 'started' ? 'accent' : '',
          text: doc.summary }
  } else if (collection === 'agent_events' && operation === 'update' && doc.phase === 'done') {
    // phase "done" = the agent's own words once the run completes
    n = { id, tag: `${doc.agent} agent`, tone: '', text: doc.summary }
  } else if (collection === 'campaign_sends' && operation === 'insert') {
    const last = notifications[notifications.length - 1]
    if (last?.kind === 'sends' && last.promo_id === doc.promo_id) {
      const count = last.count + 1
      return [...notifications.slice(0, -1),
        { ...last, count, text: `Notified ${count} customers` }]
    }
    n = { id, kind: 'sends', promo_id: doc.promo_id, count: 1,
          tag: 'Outreach', tone: '', text: 'Notified 1 customer' }
  }

  return n ? [...notifications.slice(-7), n] : notifications
}

function upsert(list, doc, key, order = 'desc') {
  const next = list.filter((d) => d[key] !== doc[key])
  next.push(doc)
  next.sort((a, b) => {
    const ka = a.created_at || a[key] || ''
    const kb = b.created_at || b[key] || ''
    return order === 'asc' ? (ka < kb ? -1 : 1) : ka > kb ? -1 : 1
  })
  return next
}
