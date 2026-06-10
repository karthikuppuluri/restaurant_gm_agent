import { useEffect, useState } from 'react'
import Chat from './Chat.jsx'
import { useFeed } from './useFeed.js'
import { fmtMoney, fmtSimTime, fmtDay, itemName, ingName, vendorName, pct } from './lib.js'

export default function App() {
  const feed = useFeed()
  return (
    <div className="shell">
      <Toasts notifications={feed.notifications} onDismiss={feed.dismiss} />
      <header className="topbar">
        <div className="brand">🌮 Restaurant GM</div>
        <div className="topbar-right">
          <span className="sim-clock">sim {fmtSimTime(feed.sim_now)}</span>
          <span className={`dot ${feed.connected ? 'on' : 'off'}`} />
        </div>
      </header>
      <main className="layout">
        <section className="left">
          <StatRow lm={feed.live_metrics} />
          <PromoSection recommendations={feed.recommendations} promotions={feed.promotions}
            sends={feed.sends_seen} />
          <div className="two-col">
            <FinancialsChart financials={feed.financials} />
            <StockPanel lm={feed.live_metrics} ingredients={feed.raw_ingredients}
              pos={feed.purchase_orders} />
          </div>
          <POPanel pos={feed.purchase_orders} />
          <AgentActivity events={feed.agent_events} />
        </section>
        <aside className="right">
          <Chat />
        </aside>
      </main>
    </div>
  )
}

function Toasts({ notifications, onDismiss }) {
  // auto-dismiss each toast ~7s after it appears
  useEffect(() => {
    const timers = notifications.map((n) => setTimeout(() => onDismiss(n.id), 7000))
    return () => timers.forEach(clearTimeout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notifications.map((n) => n.id).join(',')])

  if (notifications.length === 0) return null
  return (
    <div className="toasts">
      {notifications.slice(-4).map((n) => (
        <div key={n.id} className="toast" onClick={() => onDismiss(n.id)}>
          <span className="toast-icon">{n.icon}</span>
          <span>{n.text}</span>
        </div>
      ))}
    </div>
  )
}

const AGENT_ICONS = {
  inventory: '📦', billing: '💳', outreach: '📣', order_mgmt: '📈', central: '🧠',
}

function AgentActivity({ events }) {
  if (events.length === 0) return null
  return (
    <div className="panel">
      <div className="panel-title">Agent activity <span className="muted">(autonomous actions, in the agent's own words)</span></div>
      {events.slice(0, 6).map((e) => (
        <div key={e.event_id} className="activity-row">
          <span className="activity-icon">{AGENT_ICONS[e.agent] || '🤖'}</span>
          <div className="activity-body">
            <div className="activity-head">
              <b>{e.agent} agent</b>
              <span className="pill">{e.action?.replaceAll('_', ' ')}</span>
              <span className="muted activity-time">{fmtSimTime(e.created_at)}</span>
            </div>
            <div className="activity-text">{e.reasoning || e.summary}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

function StatRow({ lm }) {
  const pace = lm.sales_pace_vs_baseline_pct
  return (
    <div className="stats">
      <Stat label="Shift revenue" value={fmtMoney(lm.shift_revenue_money)} />
      <Stat label="Covers" value={lm.covers ?? '—'} />
      <Stat label="Operating cash" value={fmtMoney(lm.cash_on_hand_money, { compact: true })} />
      <Stat label="Vendor spend" value={fmtMoney(lm.total_vendor_spend_money, { compact: true })} />
      <Stat label="Pace vs baseline" value={pct(pace)}
        tone={pace > 0 ? 'good' : pace < 0 ? 'bad' : ''} />
    </div>
  )
}

function Stat({ label, value, tone = '' }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone}`}>{value}</div>
    </div>
  )
}

function PromoSection({ recommendations, promotions, sends }) {
  const [expandedId, setExpandedId] = useState(null)
  const pending = recommendations.filter((r) => r.status === 'pending')
  const decided = recommendations.filter((r) => r.status !== 'pending').slice(0, 5)
  const live = promotions.filter((p) => p.status === 'live')
  const past = promotions.filter((p) => p.status !== 'live').slice(0, 4)
  const expanded = decided.find((r) => r.recommendation_id === expandedId)
  return (
    <div className="panel">
      <div className="panel-title">
        Promotions
        {sends > 0 && <span className="pill">{sends} customers notified</span>}
      </div>
      {pending.length === 0 && live.length === 0 && (
        <div className="muted">No pending recommendations. Ask Central “should we run a promo?”</div>
      )}
      {pending.map((r) => <RecommendationCard key={r.recommendation_id} rec={r} />)}
      {live.map((p) => <LivePromo key={p.promo_id} promo={p} />)}
      {decided.length > 0 && (
        <div className="decided-row">
          {decided.map((r) => (
            <button
              key={r.recommendation_id}
              className={`pill clickable ${r.status} ${expandedId === r.recommendation_id ? 'open' : ''}`}
              onClick={() => setExpandedId(
                expandedId === r.recommendation_id ? null : r.recommendation_id)}
            >
              {r.proposal?.title} — {r.status} {expandedId === r.recommendation_id ? '▾' : '▸'}
            </button>
          ))}
        </div>
      )}
      {expanded && (
        <PromoDetail
          rec={expanded}
          promo={promotions.find((p) => p.recommendation_id === expanded.recommendation_id)}
        />
      )}
      {past.length > 0 && (
        <div className="past-promos">
          <div className="stock-head">Past promos</div>
          {past.map((p) => (
            <div key={p.promo_id} className="past-promo-row"
              title={`Targets: ${JSON.stringify(p.target_criteria)}`}>
              <span>{p.title}</span>
              <span className="pill">{p.discount_value}% off</span>
              <ItemPills ids={(p.applies_to_item_ids || []).slice(0, 2)} />
              <span className="muted">{fmtSimTime(p.valid_from)} – {fmtSimTime(p.valid_until)}</span>
              <span className="redemptions">
                {p.redemption_count ?? 0} redeemed
                {p.predicted_uptake != null && ` · predicted ${Math.round(p.predicted_uptake * 100)}%`}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// target_criteria -> human-readable pills ("silver/gold", "price sens ≤ 0.6", …)
function CriteriaPills({ criteria }) {
  if (!criteria) return null
  const pills = []
  if (criteria.loyalty_tier?.length) pills.push(`${criteria.loyalty_tier.join(' / ')} loyalty`)
  if (criteria.max_price_sensitivity != null) pills.push(`price sensitivity ≤ ${criteria.max_price_sensitivity}`)
  if (criteria.city) pills.push(`📍 ${criteria.city}`)
  if (criteria.dietary_flags?.length) pills.push(criteria.dietary_flags.join(' / '))
  pills.push('opted-in only')
  return (
    <span className="pill-row inline">
      {pills.map((p) => <span key={p} className="pill target">{p}</span>)}
    </span>
  )
}

function ItemPills({ ids }) {
  return (ids || []).map((id) => <span key={id} className="pill item">{itemName(id)}</span>)
}

function RecommendationCard({ rec }) {
  const [busy, setBusy] = useState(false)
  const p = rec.proposal || {}
  const just = rec.justification || {}
  const pred = just.predictive || {}

  async function decide(decision) {
    setBusy(true)
    try {
      await fetch(`/api/recommendations/${rec.recommendation_id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      })
      // the SSE feed delivers the status change; no local state needed
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rec-card">
      <div className="rec-head">
        <div>
          <div className="rec-title">{p.title}</div>
          <div className="rec-desc">{p.description}</div>
        </div>
        <div className="rec-right">
          <div className="rec-discount">{p.discount_value}% off</div>
          {p.duration_hours && <div className="rec-duration">runs {p.duration_hours}h</div>}
        </div>
      </div>
      <div className="rec-meta">
        <span className="meta-label">On:</span> <ItemPills ids={p.applies_to_item_ids} />
      </div>
      <div className="rec-meta">
        <span className="meta-label">Targets:</span> <CriteriaPills criteria={p.target_criteria} />
      </div>
      <ul className="rec-evidence">
        {(just.analytical || []).map((a, i) => (
          <li key={i}>
            <b>{a.metric}:</b> {a.value}
            <span className="source"> · {a.source_table}</span>
            {a.note ? <span className="note"> — {a.note}</span> : null}
          </li>
        ))}
      </ul>
      <div className="rec-pred">
        predicted uptake <b>{pred.predicted_uptake != null ? `${Math.round(pred.predicted_uptake * 100)}%` : '—'}</b>
        {' · '}incremental revenue <b>{fmtMoney(pred.predicted_incremental_revenue_money)}</b>
        {' · '}margin after <b>{pred.predicted_margin_after_pct != null ? `${pred.predicted_margin_after_pct}%` : '—'}</b>
        {' · '}confidence <b>{pred.confidence != null ? `${Math.round(pred.confidence * 100)}%` : '—'}</b>
      </div>
      <div className="rec-actions">
        <button className="approve" disabled={busy} onClick={() => decide('approved')}>
          ✓ Approve
        </button>
        <button className="reject" disabled={busy} onClick={() => decide('rejected')}>
          ✕ Reject
        </button>
      </div>
    </div>
  )
}

// Read-only expanded view of a decided recommendation + its resulting promo.
function PromoDetail({ rec, promo }) {
  const p = rec.proposal || {}
  const just = rec.justification || {}
  const pred = just.predictive || {}
  return (
    <div className="rec-card detail">
      <div className="rec-head">
        <div>
          <div className="rec-title">{p.title}</div>
          <div className="rec-desc">{p.description}</div>
        </div>
        <div className="rec-right">
          <div className="rec-discount">{p.discount_value}% off</div>
          {p.duration_hours && <div className="rec-duration">runs {p.duration_hours}h</div>}
        </div>
      </div>
      <div className="rec-meta">
        <span className="meta-label">On:</span> <ItemPills ids={p.applies_to_item_ids} />
      </div>
      <div className="rec-meta">
        <span className="meta-label">Targets:</span> <CriteriaPills criteria={p.target_criteria} />
      </div>
      <ul className="rec-evidence">
        {(just.analytical || []).map((a, i) => (
          <li key={i}>
            <b>{a.metric}:</b> {a.value}
            <span className="source"> · {a.source_table}</span>
            {a.note ? <span className="note"> — {a.note}</span> : null}
          </li>
        ))}
      </ul>
      <div className="rec-pred">
        {rec.status} by <b>{rec.decided_by || '—'}</b> at {fmtSimTime(rec.decided_at)}
        {promo ? <>
          {' · '}promo <b>{promo.status}</b> {fmtSimTime(promo.valid_from)} → {fmtSimTime(promo.valid_until)}
          {' · '}redemptions <b>{promo.redemption_count ?? 0}</b>
          {pred.predicted_uptake != null && <>
            {' · '}predicted uptake <b>{Math.round(pred.predicted_uptake * 100)}%</b>
          </>}
        </> : rec.status === 'approved' ? ' · promo configuring…' : null}
      </div>
    </div>
  )
}

function LivePromo({ promo }) {
  return (
    <div className="live-card">
      <div className="rec-head">
        <div>
          <div className="rec-title">
            <span className="live-dot" /> {promo.title}
          </div>
          <div className="rec-desc">{promo.description}</div>
        </div>
        <div className="rec-right">
          <div className="rec-discount live">{promo.discount_value}% off</div>
          <div className="rec-duration">until {fmtSimTime(promo.valid_until)}</div>
        </div>
      </div>
      <div className="rec-meta">
        <span className="meta-label">On:</span> <ItemPills ids={promo.applies_to_item_ids} />
      </div>
      <div className="rec-meta">
        <span className="meta-label">Targets:</span> <CriteriaPills criteria={promo.target_criteria} />
      </div>
      <div className="rec-pred">
        live {fmtSimTime(promo.valid_from)} → {fmtSimTime(promo.valid_until)}
        {' · '}redemptions <b>{promo.redemption_count ?? 0}</b>
        {promo.predicted_uptake != null && <>
          {' · '}predicted uptake <b>{Math.round(promo.predicted_uptake * 100)}%</b>
        </>}
        {' · '}approved by <b>{promo.approved_by || '—'}</b>
      </div>
    </div>
  )
}

function FinancialsChart({ financials }) {
  const days = financials.slice(-14)
  if (days.length === 0) return <div className="panel"><div className="panel-title">Daily P&L</div><div className="muted">No financials yet.</div></div>
  const max = Math.max(...days.map((d) => d.net_revenue_money?.amount || 0))
  return (
    <div className="panel">
      <div className="panel-title">Daily P&L <span className="muted">(net revenue · margin)</span></div>
      <div className="chart">
        {days.map((d) => {
          const net = d.net_revenue_money?.amount || 0
          const h = max ? Math.max((net / max) * 100, 3) : 3
          return (
            <div key={d.period_id} className="bar-col" title={`${d.period_id}: ${fmtMoney(net)} · ${d.gross_margin_pct}% margin`}>
              <div className="bar-margin">{Math.round(d.gross_margin_pct)}%</div>
              <div className="bar-wrap"><div className="bar" style={{ height: `${h}%` }} /></div>
              <div className="bar-label">{fmtDay(d.period_id)}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function StockPanel({ lm, ingredients = [], pos = [] }) {
  const low = lm.low_stock || []
  const dead = lm.eighty_sixed_item_ids || []
  // ingredients covered by an open PO get an "on order" badge
  const onOrder = new Set(
    pos.filter((p) => ['draft', 'placed'].includes(p.status))
      .flatMap((p) => (p.line_items || []).map((li) => li.ingredient_id)))
  // worst first: out of stock, then low, then healthy
  const sorted = [...ingredients].sort((a, b) =>
    stockRank(a) - stockRank(b) || (a.name || '').localeCompare(b.name || ''))
  return (
    <div className="panel">
      <div className="panel-title">Stock & supply</div>
      {dead.length > 0 && (
        <div className="stock-block">
          <div className="stock-head bad">86'd ({dead.length})</div>
          <div className="pill-row">
            {dead.map((id) => <span key={id} className="pill bad">{itemName(id)}</span>)}
          </div>
        </div>
      )}
      <div className="stock-block">
        <div className="stock-head warn">Low stock ({low.length})</div>
        {low.length === 0 && <div className="muted">Nothing below reorder point.</div>}
        <div className="pill-row">
          {low.slice(0, 12).map((i) => (
            <span key={i.ingredient_id} className="pill warn" title={`${i.on_hand_qty} on hand / reorder at ${i.reorder_point}`}>
              {i.name || i.ingredient_id}
            </span>
          ))}
          {low.length > 12 && <span className="pill">+{low.length - 12} more</span>}
        </div>
      </div>
      {sorted.length > 0 && (
        <details className="inv-details">
          <summary>Full inventory ({sorted.length} ingredients)</summary>
          <div className="inv-scroll">
            <table className="po-table">
              <thead>
                <tr><th>Ingredient</th><th>On hand</th><th>Reorder at</th><th>Par</th><th>Level</th><th></th></tr>
              </thead>
              <tbody>
                {sorted.map((r) => {
                  const fill = r.par_level ? Math.min(r.on_hand_qty / r.par_level, 1) : 0
                  const rank = stockRank(r)
                  return (
                    <tr key={r.ingredient_id}>
                      <td>{r.name}</td>
                      <td className={rank === 0 ? 'bad-text' : rank === 1 ? 'warn-text' : ''}>
                        <b>{Number(r.on_hand_qty.toFixed(2))}</b> <span className="muted">{r.unit}</span>
                      </td>
                      <td className="muted">{r.reorder_point}</td>
                      <td className="muted">{r.par_level}</td>
                      <td className="inv-bar-cell">
                        <div className="inv-bar"><div
                          className={`inv-fill ${rank === 0 ? 'bad' : rank === 1 ? 'warn' : ''}`}
                          style={{ width: `${fill * 100}%` }} /></div>
                      </td>
                      <td>
                        {rank === 0 && <span className="pill bad">out</span>}
                        {rank === 1 && <span className="pill warn">low</span>}
                        {onOrder.has(r.ingredient_id) && <span className="pill">on order</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  )
}

function stockRank(r) {
  if (r.on_hand_qty <= 0) return 0
  if (r.on_hand_qty < r.reorder_point) return 1
  return 2
}

function POPanel({ pos }) {
  if (pos.length === 0) return null
  const open = pos.filter((p) => ['draft', 'placed'].includes(p.status))
  return (
    <div className="panel">
      <div className="panel-title">
        Purchase orders
        {open.length > 0 && <span className="pill warn">{open.length} open</span>}
      </div>
      <table className="po-table">
        <thead>
          <tr><th>Status</th><th>Vendor</th><th>Items</th><th>Placed</th><th>ETA / received</th><th>Total</th></tr>
        </thead>
        <tbody>
          {pos.map((p) => (
            <tr key={p.po_id}>
              <td><span className={`pill ${p.status === 'received' ? 'approved' : 'warn'}`}>{p.status}</span></td>
              <td>{vendorName(p.vendor_id)}</td>
              <td className="po-items">
                {(p.line_items || []).slice(0, 3).map((li) => (
                  <span key={li.ingredient_id} className="pill" title={`qty ${li.qty}`}>
                    {ingName(li.ingredient_id)} ×{li.qty}
                  </span>
                ))}
                {(p.line_items || []).length > 3 && <span className="pill">+{p.line_items.length - 3}</span>}
              </td>
              <td className="muted">{fmtSimTime(p.placed_at)}</td>
              <td className="muted">{fmtSimTime(p.status === 'received' ? p.received_at : p.expected_delivery)}</td>
              <td><b>{fmtMoney(p.total_money)}</b></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
