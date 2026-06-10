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

export function pct(v, digits = 1) {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${Number(v).toFixed(digits)}%`
}
