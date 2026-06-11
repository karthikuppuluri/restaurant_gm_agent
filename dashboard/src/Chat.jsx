import { useEffect, useRef, useState } from 'react'
import Md from './Markdown.jsx'

const APP = 'restaurant_gm'
const USER = 'gm'

// Chat panel: talks to Central through ADK's /run_sse. Tool calls and agent
// transfers are surfaced as small activity lines (transparency is the product).
export default function Chat() {
  const [items, setItems] = useState([]) // {kind:'user'|'agent'|'activity', author?, text, done?}
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const sessionRef = useRef(null)
  const scrollRef = useRef(null)

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [items])

  async function ensureSession() {
    if (sessionRef.current) return sessionRef.current
    const r = await fetch(`/apps/${APP}/users/${USER}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    const j = await r.json()
    sessionRef.current = j.id
    return j.id
  }

  function push(item) {
    setItems((xs) => {
      // ADK streaming repeats function-call parts in the final merged event —
      // drop consecutive identical activity lines.
      const last = xs[xs.length - 1]
      if (item.kind === 'activity' && last?.kind === 'activity' && last.text === item.text) {
        return xs
      }
      return [...xs, item]
    })
  }

  function handleEvent(ev) {
    const parts = ev.content?.parts || []
    for (const p of parts) {
      if (p.thought) continue
      const fc = p.functionCall || p.function_call
      if (fc) {
        const args = fc.args && Object.keys(fc.args).length
          ? `(${Object.entries(fc.args).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(', ')})`
          : '()'
        push({ kind: 'activity', text: `${ev.author} → ${fc.name}${args}` })
      }
      if (p.text) {
        setItems((xs) => {
          const last = xs[xs.length - 1]
          if (last && last.kind === 'agent' && last.author === ev.author && !last.done) {
            const updated = ev.partial
              ? { ...last, text: last.text + p.text }
              : { ...last, text: p.text, done: true }
            return [...xs.slice(0, -1), updated]
          }
          return [...xs, { kind: 'agent', author: ev.author, text: p.text, done: !ev.partial }]
        })
      }
    }
  }

  async function send() {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)
    push({ kind: 'user', text })
    try {
      const session = await ensureSession()
      const res = await fetch('/run_sse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          app_name: APP,
          user_id: USER,
          session_id: session,
          new_message: { role: 'user', parts: [{ text }] },
          streaming: true,
        }),
      })
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        let i
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i)
          buf = buf.slice(i + 2)
          for (const line of chunk.split('\n')) {
            if (line.startsWith('data: ')) {
              try {
                handleEvent(JSON.parse(line.slice(6)))
              } catch { /* ignore malformed frames */ }
            }
          }
        }
      }
    } catch (err) {
      push({ kind: 'activity', text: `error: ${err.message}` })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="chat">
      <div className="chat-head">
        <span className="chat-title">Ask the GM agents</span>
        {busy && <span className="chat-busy">thinking…</span>}
      </div>
      <div className="chat-scroll" ref={scrollRef}>
        {items.length === 0 && (
          <div className="chat-empty">
            Try: “what's running low?” · “how are sales right now?” ·
            “should we run a promo?”
          </div>
        )}
        {items.map((it, i) =>
          it.kind === 'activity' ? (
            <div key={i} className="chat-activity">{it.text}</div>
          ) : (
            <div key={i} className={`bubble ${it.kind}`}>
              {it.kind === 'agent' && <div className="bubble-author">{it.author}</div>}
              <div className="bubble-text">
                {it.kind === 'agent' ? <Md text={it.text} /> : it.text}
              </div>
            </div>
          )
        )}
      </div>
      <div className="chat-input">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
          placeholder="Message Central…"
          disabled={busy}
        />
        <button onClick={send} disabled={busy || !input.trim()}>Send</button>
      </div>
    </div>
  )
}
