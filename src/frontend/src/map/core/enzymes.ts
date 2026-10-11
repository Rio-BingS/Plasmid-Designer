/**
 * 酶切显示规则（纯函数）：
 * - 单切点（cut_count = 1）加粗；平端酶用独立颜色；5'/3' 突出端各一色；
 * - 过滤：切点数（1 / ≤2 / 任意）、名称或识别序列搜索、逐个隐藏、
 *   限定名单（如 Golden Gate 酶组）。酶组本身（common/unique6/...）由后端
 *   在 analyze 时按 enzyme_set 计算，前端只做显示层过滤。
 */
import type { MapEnzyme, MethylationKind } from '../types'

export const GOLDEN_GATE_ENZYMES = ['BsaI', 'BsmBI', 'BbsI', 'SapI', 'Esp3I', 'PaqCI']

export type CutFilter = 1 | 2 | null

export interface EnzymeFilter {
  /** 最多切几次：1 = 仅单切点，2 = 1~2 次，null = 不限 */
  maxCuts: CutFilter
  /** 名称/识别序列子串（不区分大小写） */
  query: string
  /** 被用户单独隐藏的酶名 */
  hidden: string[]
  /** 只显示名单内的酶（null = 不限），如 GOLDEN_GATE_ENZYMES */
  only: string[] | null
}

export function defaultEnzymeFilter(): EnzymeFilter {
  return { maxCuts: null, query: '', hidden: [], only: null }
}

export function matchesEnzymeQuery(e: MapEnzyme, query: string): boolean {
  const q = query.trim().toUpperCase()
  if (!q) return true
  if (e.name.toUpperCase().includes(q)) return true
  return e.recognition.replace(/[\^_]/g, '').toUpperCase().includes(q.replace(/[\^_]/g, ''))
}

/** 满足除"逐个隐藏"外全部条件的酶（列表面板用：被隐藏的仍列出以便恢复） */
export function listEnzymes(enzymes: MapEnzyme[], f: EnzymeFilter): MapEnzyme[] {
  const only = f.only ? new Set(f.only.map((n) => n.toLowerCase())) : null
  return enzymes
    .filter((e) => e.cut_count >= 1)
    .filter((e) => f.maxCuts === null || e.cut_count <= f.maxCuts)
    .filter((e) => !only || only.has(e.name.toLowerCase()))
    .filter((e) => matchesEnzymeQuery(e, f.query))
    .sort((a, b) => a.cut_count - b.cut_count || a.name.localeCompare(b.name))
}

/** 图上实际绘制的酶 */
export function visibleEnzymes(enzymes: MapEnzyme[], f: EnzymeFilter): MapEnzyme[] {
  const hidden = new Set(f.hidden)
  return listEnzymes(enzymes, f).filter((e) => !hidden.has(e.name))
}

export const ENZYME_COLORS = { fivePrime: '#1f4e9c', threePrime: '#7b3fa0', blunt: '#c0392b' }

export interface EnzymeStyle { unique: boolean; blunt: boolean; color: string; weight: number }

export function enzymeStyle(e: MapEnzyme): EnzymeStyle {
  const blunt = e.ends === 'blunt'
  const color = blunt ? ENZYME_COLORS.blunt : e.ends === "3'" ? ENZYME_COLORS.threePrime : ENZYME_COLORS.fivePrime
  const unique = e.cut_count === 1
  return { unique, blunt, color, weight: unique ? 700 : 400 }
}

export interface CutMark {
  /** 唯一键：酶名#序号 */
  id: string
  name: string
  /** 顶链切点（边界坐标 0..L） */
  cut: number
  cutBottom: number
  /** 识别位点首碱基（1-based） */
  position: number
  siteLength: number
  enzyme: MapEnzyme
  /** 该位点被 dam/dcm 甲基化阻断（空 = 可切） */
  blocked: MethylationKind[]
  /** 该位点的突出端（位点级优先，否则酶级） */
  overhang: string | null
}

/** 酶切位点展开成逐个切点，按位置排序；showBlocked=false 时略去甲基化阻断位点 */
export function cutMarks(enzymes: MapEnzyme[], showBlocked = true): CutMark[] {
  const out: CutMark[] = []
  for (const e of enzymes) {
    const siteLength = recognitionLength(e.recognition)
    e.sites.forEach((s, i) => {
      const blocked = s.blocked_by ?? []
      if (blocked.length && !showBlocked) return
      out.push({
        id: `${e.name}#${i}`, name: e.name, cut: s.cut_top, cutBottom: s.cut_bottom,
        position: s.position, siteLength, enzyme: e, blocked: [...blocked],
        overhang: s.overhang !== undefined ? s.overhang : e.overhang,
      })
    })
  }
  return out.sort((a, b) => a.cut - b.cut || a.name.localeCompare(b.name))
}

/** 识别序列长度（去掉 ^ _ 标记；含 N 的 IIS 酶取到最后一个非 N 为止） */
export function recognitionLength(recognition: string): number {
  const core = recognition.replace(/[\^_]/g, '').replace(/[()0-9/,-]/g, '')
  const trimmed = core.replace(/N+$/i, '')
  return Math.max(1, trimmed.length || core.length)
}

/** 甲基化阻断位点的显示色（灰、虚线 = "空心"） */
export const BLOCKED_COLOR = '#9ca3af'

export interface CutStyle extends EnzymeStyle { blocked: boolean; dash: string | undefined }

/** 单个切点的样式：阻断位点灰色虚线、不加粗，其余同酶样式 */
export function cutStyle(c: Pick<CutMark, 'enzyme' | 'blocked'>): CutStyle {
  const st = enzymeStyle(c.enzyme)
  if (c.blocked.length) return { ...st, color: BLOCKED_COLOR, weight: 400, unique: false, blocked: true, dash: '3 2' }
  return { ...st, blocked: false, dash: undefined }
}

/** 该酶有多少位点被阻断 */
export function blockedSiteCount(e: MapEnzyme): number {
  return e.sites.filter((s) => s.blocked_by?.length).length
}

export function blockedText(kinds: string[]): string {
  return `被 ${kinds.join('/')} 甲基化阻断`
}

/**
 * 概览图（环形/线性）上的切点上限：位点总数超过 maxMarks 时，按切点数从少到多
 * 逐个酶纳入，直到再加一个酶就超限为止（单/双切点酶总会纳入）。被略去的酶
 * 整体不画——每个位点都画只会糊成一圈色带（SnapGene 默认也只显示少切点酶）；
 * 序列视图与酶切列表仍显示全部位点。
 */
export function overviewCuts(cuts: CutMark[], maxMarks: number): { shown: CutMark[]; dropped: { name: string; count: number }[] } {
  if (cuts.length <= maxMarks) return { shown: cuts, dropped: [] }
  const byEnzyme = new Map<string, number>()
  for (const c of cuts) byEnzyme.set(c.name, (byEnzyme.get(c.name) ?? 0) + 1)
  const order = [...byEnzyme.entries()].sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0]))
  const keep = new Set<string>()
  const dropped: { name: string; count: number }[] = []
  let total = 0
  for (const [name, n] of order) {
    if (n <= 2 || (dropped.length === 0 && total + n <= maxMarks)) {
      keep.add(name)
      total += n
    } else {
      dropped.push({ name, count: n })
    }
  }
  return { shown: cuts.filter((c) => keep.has(c.name)), dropped }
}
