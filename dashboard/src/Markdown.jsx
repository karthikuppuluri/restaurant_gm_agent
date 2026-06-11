// Dependency-free mini-markdown for agent responses: headings, bullets,
// **bold**, `code`. Covers what Gemini actually emits without pulling in a lib.

function inline(text, keyBase) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) {
      return <b key={`${keyBase}-${i}`}>{p.slice(2, -2)}</b>
    }
    if (p.startsWith('`') && p.endsWith('`')) {
      return <code key={`${keyBase}-${i}`} className="md-code">{p.slice(1, -1)}</code>
    }
    return p
  })
}

export default function Md({ text }) {
  if (!text) return null
  return (
    <>
      {text.split('\n').map((line, i) => {
        const h = line.match(/^#{1,4}\s+(.*)/)
        if (h) return <div key={i} className="md-h">{inline(h[1], i)}</div>
        const li = line.match(/^\s*[*•-]\s+(.*)/)
        if (li) return <div key={i} className="md-li">• {inline(li[1], i)}</div>
        if (!line.trim()) return <div key={i} className="md-gap" />
        return <div key={i}>{inline(line, i)}</div>
      })}
    </>
  )
}
