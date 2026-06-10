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
    sends_seen: 0,
    sim_now: null,
    notifications: [],
  })
  const esRef = useRef(null)

  function dismiss(id) {
    setState((s) => ({ ...s, notifications: s.notifications.filter((n) => n.id !== id) }))
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
              return { ...s, notifications, live_metrics: doc, sim_now: doc.as_of || s.sim_now }
            case 'financials':
              return { ...s, notifications, financials: upsert(s.financials, doc, '_id', 'asc') }
            case 'promotion_recommendations':
              return { ...s, notifications, recommendations: upsert(s.recommendations, doc, 'recommendation_id') }
            case 'promotions':
              return { ...s, notifications, promotions: upsert(s.promotions, doc, 'promo_id') }
            case 'purchase_orders':
              return { ...s, notifications, purchase_orders: upsert(s.purchase_orders, doc, 'po_id').slice(0, 10) }
            case 'agent_events':
              return { ...s, notifications, agent_events: upsert(s.agent_events, doc, 'event_id').slice(0, 15) }
            case 'raw_ingredients': {
              const raw = s.raw_ingredients.map((r) =>
                r.ingredient_id === doc.ingredient_id ? { ...r, ...doc } : r)
              return { ...s, notifications, raw_ingredients: raw }
            }
            case 'campaign_sends':
              return { ...s, notifications, sends_seen: s.sends_seen + 1 }
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

  return { ...state, dismiss }
}

// Turns agent-driven writes into human-readable toasts. Rapid campaign_sends
// inserts for the same promo merge into one counting toast instead of 50.
function notify(notifications, collection, operation, doc) {
  const id = `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
  let n = null

  if (collection === 'purchase_orders' && operation === 'insert') {
    const items = doc.line_items?.length || 0
    n = {
      id, icon: '📦',
      text: `Inventory agent ordered ${items} ingredient${items === 1 ? '' : 's'} from ` +
        `${vendorName(doc.vendor_id)} — ${fmtMoney(doc.total_money)}, eta ${doc.expected_delivery?.slice(5, 10) || '?'}`,
    }
  } else if (collection === 'purchase_orders' && operation === 'update' && doc.status === 'received') {
    n = { id, icon: '🚚', text: `Delivery received from ${vendorName(doc.vendor_id)}` }
  } else if (collection === 'promotion_recommendations' && operation === 'insert') {
    n = { id, icon: '💡', text: `Billing agent proposed “${doc.proposal?.title}” — awaiting your approval` }
  } else if (collection === 'promotions' && operation === 'insert') {
    n = { id, icon: '🎉', text: `Promo live: “${doc.title}” (${doc.discount_value}% off)` }
  } else if (collection === 'agent_events' && operation === 'insert') {
    n = { id, icon: '🤖', agent: doc.agent,
          text: `${doc.agent} agent: ${doc.summary}` }
  } else if (collection === 'campaign_sends' && operation === 'insert') {
    const last = notifications[notifications.length - 1]
    if (last?.kind === 'sends' && last.promo_id === doc.promo_id) {
      const count = last.count + 1
      return [...notifications.slice(0, -1),
        { ...last, count, text: `Outreach notified ${count} customers` }]
    }
    n = { id, kind: 'sends', promo_id: doc.promo_id, count: 1, icon: '📣',
          text: 'Outreach notified 1 customer' }
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
