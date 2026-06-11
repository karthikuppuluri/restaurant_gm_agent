// Small shared helpers — money is integer cents everywhere in the schema.

export function fmtMoney(money, opts = {}) {
  if (money == null) return '—'
  const cents = typeof money === 'object' ? money.amount : money
  if (cents == null) return '—'
  const dollars = cents / 100
  return dollars.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: opts.compact ? 0 : 2,
    minimumFractionDigits: opts.compact ? 0 : 2,
  })
}

// "cat_item_beef_burrito_01" -> "Beef Burrito"
export function itemName(itemId) {
  return itemId
    .replace(/^cat_item_/, '')
    .replace(/_\d+$/, '')
    .split('_')
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')
}

// "ing_beef_patty" -> "Beef Patty"
export function ingName(ingredientId) {
  return ingredientId
    .replace(/^ing_/, '')
    .split('_')
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')
}

// "ven_metro_meats" -> "Metro Meats"
export function vendorName(vendorId) {
  if (!vendorId) return 'vendor'
  return vendorId
    .replace(/^ven_/, '')
    .split('_')
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')
}

// "2026-06-11T22:08:38Z" -> "Jun 11, 10:08 PM"
export function fmtSimTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    timeZone: 'UTC',
  })
}

// "2026-06-11" -> "6/11"
export function fmtDay(day) {
  const [, m, d] = day.split('-')
  return `${Number(m)}/${Number(d)}`
}

// Defensive prettifier for agent-written evidence labels: swap raw ids
// (ing_*, cat_item_*) for display names so a lazy metric like
// "ing_guacamole_excess_value_cents" reads "Guacamole excess value".
const METRIC_WORDS = new Set(['excess', 'value', 'cents', 'margin', 'after',
  'pct', 'qty', 'cost', 'stock', 'cover', 'hours', 'revenue', 'units',
  'total', 'sales', 'uptake'])

function titleWords(str) {
  return str.split('_').filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1)).join(' ')
}

export function prettyMetric(s) {
  if (!s) return s
  let out = s.replace(/cat_item_[a-z0-9_]+/g, (raw) => {
    const parts = raw.replace(/^cat_item_/, '').split('_')
    const i = parts.findIndex((p) => /^\d+$/.test(p)) // item ids end in _01 etc.
    if (i === -1) return titleWords(parts.join('_'))
    const rest = parts.slice(i + 1).filter((p) => p !== 'cents').join(' ')
    return rest ? `${titleWords(parts.slice(0, i).join('_'))} ${rest}`
      : titleWords(parts.slice(0, i).join('_'))
  })
  out = out.replace(/ing_[a-z0-9_]+/g, (raw) => {
    const parts = raw.replace(/^ing_/, '').split('_')
    let end = parts.length
    while (end > 1 && (METRIC_WORDS.has(parts[end - 1]) || /^\d+$/.test(parts[end - 1]))) end--
    const rest = parts.slice(end).filter((p) => p !== 'cents').join(' ')
    return rest ? `${titleWords(parts.slice(0, end).join('_'))} ${rest}`
      : titleWords(parts.slice(0, end).join('_'))
  })
  return out.replace(/_/g, ' ')
}

// Evidence values: a cents-flavored metric with a bare number reads as money.
export function prettyValue(metric, value) {
  if (/cents|value|revenue|cost/i.test(metric || '') && /^\d+$/.test(String(value).trim())) {
    return fmtMoney(Number(value))
  }
  return value
}

export function pct(v, digits = 1) {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${Number(v).toFixed(digits)}%`
}
