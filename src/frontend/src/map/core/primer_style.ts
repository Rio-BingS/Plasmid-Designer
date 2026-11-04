/**
 * 引物视觉 token（跨视图统一）：正/反向分色、方向符号、名牌与高亮块配色。
 *
 * 依据：
 *   - 正反向分色（模块 B）：正向 #2563EB（蓝）、反向 #7C3AED（紫），色相差 ≥ 60°，
 *     色盲友好（蓝-紫在 protan/deutan 下仍可分，且配套 → / ← 形状差异作为冗余编码）；
 *   - 对比度（模块 D）：白字落在两色上实测 5.17:1 / 5.70:1，满足 WCAG AA
 *     正文阈值 4.5:1（tests/primer_render_check.test.ts 有断言守住）。
 */
import type { MapPrimer } from '../types'

/** 引物配色（正向/反向） */
export interface PrimerPalette {
  base: string
  dark: string
  tint: string
  ink: string
}

/** 正向（sense / + 链）引物主色 */
export const PRIMER_FWD: PrimerPalette = {
  /** 高亮块 / 名牌底 */
  base: '#2563EB',
  /** 描边与深色文字 */
  dark: '#1D4ED8',
  /** 浅底（名牌浅色变体） */
  tint: '#DBEAFE',
  /** 浅底上的文字 */
  ink: '#1E3A8A',
}

/** 反向（antisense / − 链）引物主色 */
export const PRIMER_REV: PrimerPalette = {
  base: '#7C3AED',
  dark: '#6D28D9',
  tint: '#EDE9FE',
  ink: '#4C1D95',
}

/**
 * 引物标签优先级：高于双切点酶、与单切点酶同档，保证设计里的引物不会被
 * 拥挤规则先藏掉（22 条 oligo 全部标出的实测要求）。
 */
export const PRIMER_LABEL_PRIORITY = 5e7

/**
 * 是否反义引物：strand=-1，或名字带 AS / R / REV。
 *
 * GenBank 的 primer_bind 链标注常不一致（同一批 AS 引物只有一部分标了
 * complement），而 SnapGene 按引物对象的方向属性画在对应链旁——因此按名字
 * 补判，与本项目序列视图、导出语义保持一致。
 */
export function isRevPrimer(p: Pick<MapPrimer, 'name' | 'strand'>): boolean {
  if (p.strand === -1) return true
  return /AS\d/i.test(p.name) || /(^|_)[Rr]\d/.test(p.name) || /REV/i.test(p.name)
}

/** 该引物使用的配色 */
export function primerPalette(p: Pick<MapPrimer, 'name' | 'strand'>) {
  return isRevPrimer(p) ? PRIMER_REV : PRIMER_FWD
}

/**
 * WCAG 相对亮度（sRGB，0..1）
 */
export function relLuminance(hex: string): number {
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  const n = parseInt(h.slice(0, 6), 16)
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
}

/** WCAG 对比度（1..21） */
export function contrastRatio(a: string, b: string): number {
  const la = relLuminance(a)
  const lb = relLuminance(b)
  const [hi, lo] = la > lb ? [la, lb] : [lb, la]
  return (hi + 0.05) / (lo + 0.05)
}
