/**
 * 文本宽度。浏览器里用 Canvas measureText 按图谱实际字体测量（结果缓存），
 * 字体回退到哪款就按哪款算——估算表在宽字体（如 DejaVu Sans）上会偏窄，
 * 标签因此挤在一起；测不了（jsdom/SSR）时用按字符类别的 em 宽度估算
 * （确定性，单测用），CJK 等全角字符按 1em（OVE 曾因中文标签被挤压修过同类问题）。
 * 两条路径都略偏大，宁可多留空隙也不重叠。
 */

/** 图谱 SVG 的字体栈（各视图 font-family 与此一致） */
export const MAP_FONT = "Inter, -apple-system, 'Segoe UI', Roboto, sans-serif"

export function estimateTextWidth(text: string, fontSize: number, bold = false): number {
  let em = 0
  for (const ch of text) {
    const c = ch.codePointAt(0) ?? 0
    if (c >= 0x2e80) em += 1.0
    else if (/[ilIj.,:;'|!()[\]{} ]/.test(ch)) em += 0.32
    else if (/[mwMW@%]/.test(ch)) em += 0.86
    else if (/[A-Z0-9#&]/.test(ch)) em += 0.66
    else em += 0.56
  }
  return em * fontSize * (bold ? 1.08 : 1)
}

let ctx: CanvasRenderingContext2D | null | undefined
function measureCtx(): CanvasRenderingContext2D | null {
  if (ctx !== undefined) return ctx
  ctx = null
  try {
    const jsdom = typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent)
    if (typeof document !== 'undefined' && !jsdom) ctx = document.createElement('canvas').getContext('2d')
  } catch {
    ctx = null
  }
  return ctx
}

const cache = new Map<string, number>()

export function textWidth(text: string, fontSize: number, bold = false): number {
  if (!text) return 0
  const c = measureCtx()
  if (!c) return estimateTextWidth(text, fontSize, bold)
  const key = `${bold ? 'b' : ''}${fontSize}|${text}`
  let w = cache.get(key)
  if (w === undefined) {
    c.font = `${bold ? '700 ' : ''}${fontSize}px ${MAP_FONT}`
    w = c.measureText(text).width * 1.04 + 0.5
    if (cache.size > 20000) cache.clear()
    cache.set(key, w)
  }
  return w
}

/** 截断到给定宽度（末尾 …），放不下一个字符返回 '' */
export function truncateText(text: string, maxW: number, fontSize: number, bold = false): string {
  if (textWidth(text, fontSize, bold) <= maxW) return text
  let out = ''
  for (const ch of text) {
    if (textWidth(out + ch + '…', fontSize, bold) > maxW) break
    out += ch
  }
  return out ? out + '…' : ''
}
