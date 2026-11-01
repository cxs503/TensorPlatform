// Minimal markdown renderer for console reports. Input is authored by the
// platform's own report pipeline (trusted subset: headings, tables, lists,
// bold, inline code, fenced code, hr). Everything is HTML-escaped first, so
// no raw markup can pass through.
const esc = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const inline = (s: string): string =>
  esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')

function renderTable(lines: string[]): string {
  const cells = (l: string) => l.replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
  const head = cells(lines[0])
  const body = lines.slice(2).map(cells)
  let h = '<table class="tf-md-table"><thead><tr>'
  head.forEach((c) => { h += `<th>${inline(c)}</th>` })
  h += '</tr></thead><tbody>'
  body.forEach((row) => {
    h += '<tr>'
    row.forEach((c) => { h += `<td>${inline(c)}</td>` })
    h += '</tr>'
  })
  return h + '</tbody></table>'
}

export function renderMarkdown(md: string): string {
  const out: string[] = []
  const lines = md.split('\n')
  let i = 0
  let para: string[] = []
  const flush = () => {
    if (para.length) {
      out.push(`<p>${inline(para.join(' '))}</p>`)
      para = []
    }
  }
  while (i < lines.length) {
    const line = lines[i]
    if (line.startsWith('```')) {
      flush()
      const buf: string[] = []
      i += 1
      while (i < lines.length && !lines[i].startsWith('```')) { buf.push(lines[i]); i += 1 }
      out.push(`<pre class="tf-md-pre">${esc(buf.join('\n'))}</pre>`)
      i += 1
      continue
    }
    if (/^\|.*\|/.test(line) && i + 1 < lines.length && /^\|[\s:|-]+\|$/.test(lines[i + 1])) {
      flush()
      const tbl: string[] = []
      while (i < lines.length && /^\|.*\|/.test(lines[i])) { tbl.push(lines[i]); i += 1 }
      out.push(renderTable(tbl))
      continue
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) {
      flush()
      const n = h[1].length
      out.push(`<h${n}>${inline(h[2])}</h${n}>`)
      i += 1
      continue
    }
    if (/^(\*|-)\s+/.test(line)) {
      flush()
      const items: string[] = []
      while (i < lines.length && /^(\*|-)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^(\*|-)\s+/, '')); i += 1
      }
      out.push(`<ul>${items.map((it) => `<li>${inline(it)}</li>`).join('')}</ul>`)
      continue
    }
    if (/^\d+\.\s+/.test(line)) {
      flush()
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s+/, '')); i += 1
      }
      out.push(`<ol>${items.map((it) => `<li>${inline(it)}</li>`).join('')}</ol>`)
      continue
    }
    if (/^---+$/.test(line.trim())) { flush(); out.push('<hr />'); i += 1; continue }
    if (!line.trim()) { flush(); i += 1; continue }
    para.push(line.trim())
    i += 1
  }
  flush()
  return out.join('\n')
}
