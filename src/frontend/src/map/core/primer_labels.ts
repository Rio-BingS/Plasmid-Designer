/**
 * 环外引物标签（按用户提供的 SnapGene 截图复刻）。
 *
 * 形态：**蓝底圆角名牌 + 白色引物名**，名字旁边跟一段普通文字的坐标
 * `(280 .. 325)`；22 条 oligo 全部标出，沿环外堆成列、引线呈扇形展开。
 *
 * 与酶标签的差别：酶是裸文字，引物是色块名牌——用户明确要求按截图复刻，
 * 因此不再套用「名字 + 小号灰坐标」的酶样式。
 */
import { textWidth } from './text'
import type { CircularLabelInput } from './labels'

/** 名牌配色（SnapGene 引物蓝） */
export const PRIMER_CHIP_FILL = '#4a90d9'
export const PRIMER_CHIP_STROKE = '#2f6aa8'
export const PRIMER_CHIP_INK = '#ffffff'
/** 名牌左右内边距 */
export const PRIMER_CHIP_PAD = 4
/** 名牌与坐标之间的间隙 */
export const PRIMER_POS_GAP = 4

/**
 * 引物标签优先级：高于双切点酶、与单切点酶同档，保证设计里的引物不会被
 * 拥挤规则先藏掉（用户要求 22 条全部标出）。
 */
export const PRIMER_LABEL_PRIORITY = 5e7

export interface PrimerLabelSource {
  key: string
  name: string
  /** 已是 `"start .. end"` 形式 */
  pos: string
  /** 弧段中点角度（弧度） */
  mid: number
  /** 弧段外沿半径（引线起点） */
  r: number
}

export interface PrimerLabel {
  input: CircularLabelInput
  name: string
  /** 坐标文本（含括号与前导空格）；关闭「位置」时为空串 */
  pos: string
  /** 名牌文字宽（渲染时定 rect 宽度） */
  nameW: number
}

/**
 * 构造引物外标签：名牌宽度只按名字算，坐标是名牌外的附加文字。
 * 名字放不下（标签栏太窄）时整条返回 null，交给布局隐藏——绝不截断名字。
 */
export function buildPrimerLabel(
  p: PrimerLabelSource,
  opts: { maxW: number; font: number; posFont: number; showPos: boolean; rowH: number; priority?: number },
): PrimerLabel | null {
  const { maxW, font, posFont, showPos, rowH } = opts
  const nameW = textWidth(p.name, font, true)
  const chipW = nameW + 2 * PRIMER_CHIP_PAD
  if (chipW > maxW) return null
  const posText = showPos ? ` (${p.pos})` : ''
  const posW = posText ? textWidth(posText, posFont) : 0
  // 坐标放不下时整段省略（截断半个数字比不写更糟），名牌本身照样显示
  const fitsPos = !!posText && chipW + PRIMER_POS_GAP + posW <= maxW
  return {
    input: {
      id: p.key,
      text: p.name + (fitsPos ? posText : ''),
      width: chipW + (fitsPos ? PRIMER_POS_GAP + posW : 0),
      height: rowH,
      angle: p.mid,
      radius: p.r + 6,
      priority: opts.priority ?? PRIMER_LABEL_PRIORITY,
    },
    name: p.name,
    pos: fitsPos ? posText : '',
    nameW,
  }
}
