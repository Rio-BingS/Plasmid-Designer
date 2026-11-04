/**
 * 图元命中键：各视图在可交互元素上写 data-hit="<键>"，悬停/点击统一在这里
 * 解析成提示文本与选择动作，保证同一元件在任一视图里的提示与行为一致。
 *   f:<featureId>  e:<enzyme>#<i>  o:<orfIndex>  p:<primerId>  s:<searchHitIndex>
 */
import type { Range } from './types'
import type { MapStore } from './store'
import { normPos, rangeLength } from './core/coords'
import { FEATURE_TYPE_LABELS } from './core/palette'
import { blockedText } from './core/enzymes'
import { cachedEnzymeDetail, enzymeDetailLines } from './enzymeMeta'
import { gcPercent, meltingTemp } from './core/seq'
import { isRevPrimer } from './core/primer_style'

export interface HitInfo {
  kind: 'feature' | 'enzyme' | 'orf' | 'primer' | 'search'
  title: string
  lines: string[]
  range: Range | null
  select: () => void
}

function strandText(s: number): string {
  return s === 1 ? '正链 →' : s === -1 ? '反链 ←' : '无方向'
}

export function resolveHit(store: MapStore, key: string | null | undefined): HitInfo | null {
  if (!key) return null
  const i = key.indexOf(':')
  const kind = key.slice(0, i)
  const id = key.slice(i + 1)
  const doc = store.doc.value
  const L = doc.length
  const circ = store.circular.value
  const span = (r: Range) => `${r.start.toLocaleString()}..${r.end.toLocaleString()}（${rangeLength(r, L, circ).toLocaleString()} bp）`
  if (kind === 'f') {
    const f = doc.features.find((x) => x.id === id)
    if (!f) return null
    const lines = [`${FEATURE_TYPE_LABELS[f.type] ?? f.type} · ${strandText(f.strand)}`, span(f)]
    if (f.segments && f.segments.length > 1) lines.push(`${f.segments.length} 段：${f.segments.map((s) => `${s.start}..${s.end}`).join(', ')}`)
    if (f.notes) lines.push(f.notes)
    if (f.auto) {
      lines.push(`自动注释 · ${f.auto.library} · 一致性 ${(f.auto.identity * 100).toFixed(1)}% · 覆盖 ${(f.auto.coverage * 100).toFixed(0)}%${f.auto.fragment ? '（片段）' : ''}`)
    }
    return { kind: 'feature', title: f.name, lines, range: { start: f.start, end: f.end }, select: () => store.selectFeature(f.id) }
  }
  if (kind === 'e') {
    const [name, idx] = id.split('#')
    const e = doc.enzymes.find((x) => x.name === name)
    const site = e?.sites[Number(idx)]
    if (!e || !site) return null
    const len = e.recognition.replace(/[\^_]/g, '').replace(/N+$/i, '').length || 1
    const range = { start: site.position, end: circ ? normPos(site.position + len - 1, L) : Math.min(L, site.position + len - 1) }
    const ends = e.ends === 'blunt' ? '平端' : `${e.ends} 突出${e.overhang ? ` ${e.overhang}` : ''}`
    const lines = [`${e.recognition} · ${ends}`, `切点 ${site.cut_top}/${site.cut_bottom} · 共切 ${e.cut_count} 次${e.cut_count === 1 ? '（单切点）' : ''}`]
    // 位点级突出端（IIS 型酶每个位点的 4 nt 不同）
    if (site.overhang && site.overhang !== e.overhang) lines.push(`本位点突出端 ${site.overhang}`)
    if (site.blocked_by?.length) lines.push(`⚠ ${blockedText(site.blocked_by)}（dam+/dcm+ 菌株中不切）`)
    if (e.methylation?.length) lines.push(`甲基化敏感：${e.methylation.join(', ')}`)
    // 酶详情（厂商、Type IIS）只在用户在酶切面板里打开过该酶后才有
    const meta = cachedEnzymeDetail(e.name)
    if (meta) lines.push(...enzymeDetailLines(meta))
    return {
      kind: 'enzyme', title: e.name, lines, range,
      select: () => { store.select(range, { navigate: true }); store.caret.value = site.cut_top },
    }
  }
  if (kind === 'o') {
    const o = doc.orfs[Number(id)]
    if (!o) return null
    const r = { start: o.start, end: o.end }
    return {
      kind: 'orf', title: `ORF ${o.strand === 1 ? '+' : '−'}${o.frame + 1}`,
      lines: [`${o.aa_length} aa · ${strandText(o.strand)}`, span(r)], range: r,
      select: () => store.select(r, { navigate: true }),
    }
  }
  if (kind === 'p') {
    const p = doc.primers.find((x) => x.id === id)
    if (!p) return null
    const r = { start: p.start, end: p.end }
    const rev = isRevPrimer(p)
    const seq = (p.full_sequence || p.sequence || '').toUpperCase()
    // Tm/GC 优先用文档自带字段（后端 core.primer_base 最近邻算法算出）；
    // 旧缓存文档没有这两个字段时回退到前端同算法实现
    const tm = p.tm ?? (seq ? meltingTemp(seq) : undefined)
    const gc = p.gc ?? (seq ? gcPercent(seq) : undefined)
    const stats = tm !== undefined && gc !== undefined ? `Tm ${tm.toFixed(1)} °C · GC ${gc.toFixed(1)}%` : ''
    // 同一引物多处结合（同名其它位点）时提示，与高亮联动（SnapGene 同款行为：
    // 悬停即告知引物在序列上还有别的结合位置）
    const others = doc.primers.filter((x) => x.name === p.name && x.id !== p.id)
    const multiNote = others.length
      ? `⚠ 另有 ${others.length} 处结合位点：${others.slice(0, 3).map((x) => `${x.start}..${x.end}`).join('、')}${others.length > 3 ? ' 等' : ''}`
      : ''
    const lines = [
      `引物 · ${rev ? '反向 ←' : '正向 →'}`,
      span(r),
      ...(stats ? [stats] : []),
      ...(multiNote ? [multiNote] : []),
      p.full_sequence && p.full_sequence !== p.sequence
        ? `全长 ${p.full_sequence.length} nt（退火 ${p.sequence.length} nt）`
        : (seq ? `${seq.length} nt` : ''),
      seq,
    ].filter(Boolean) as string[]
    return {
      kind: 'primer', title: p.name, lines, range: r,
      select: () => store.select(r, { navigate: true }),
    }
  }
  if (kind === 's') {
    const n = Number(id)
    const h = store.search.hits[n]
    if (!h) return null
    return {
      kind: 'search', title: `搜索命中 ${n + 1}/${store.search.hits.length}`,
      lines: [`${strandText(h.strand)} · ${h.mismatches} 个错配`, span(h)], range: h,
      select: () => store.focusHit(n),
    }
  }
  return null
}

/** 事件目标向上找 data-hit */
export function hitKeyOf(target: EventTarget | null): string | null {
  const el = target as Element | null
  if (!el || typeof el.closest !== 'function') return null
  return el.closest('[data-hit]')?.getAttribute('data-hit') ?? null
}

/** 悬停：写入共享 hover（提示框与各视图高亮读取） */
export function setHoverFromEvent(store: MapStore, e: PointerEvent | MouseEvent): void {
  const key = hitKeyOf(e.target)
  if (!key) {
    if (store.hover.value) store.hover.value = null
    return
  }
  const prev = store.hover.value
  if (prev && prev.id === key) {
    // 同一元件：只更新提示框坐标，不重新解析（高亮读 hoverTarget，不会因此重算）
    if (prev.x !== e.clientX || prev.y !== e.clientY) store.hover.value = { ...prev, x: e.clientX, y: e.clientY }
    return
  }
  const info = resolveHit(store, key)
  if (!info) return
  store.hover.value = {
    kind: info.kind, id: key, title: info.title, lines: info.lines, range: info.range, x: e.clientX, y: e.clientY,
  }
}
