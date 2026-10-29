/**
 * 环外引物标签（SnapGene 图谱视图口径，含正反向差异化）。
 *
 * 形态：圆角名牌（正/反向分色）+ 名牌内方向符号 + 旁侧坐标。
 * 名牌宽度按「方向符号 + 名字」实测，坐标是否显示取决于剩余宽度。
 */
import { textWidth } from './text'
import { PRIMER_LABEL_PRIORITY, isRevPrimer } from './primer_style'
import type { CircularLabelInput } from './labels'

/** 名牌左右内边距（与 PrimerLabel.vue 保持一致） */
export const PRIMER_CHIP_PAD = 4
/** 名牌与坐标之间的间隙 */
export const PRIMER_POS_GAP = 4

export { PRIMER_LABEL_PRIORITY }

export interface PrimerLabelSource {
  key: string
  name: string
  /** 已是 `"start .. end"` 形式 */
  pos: string
  /** 弧段中点角度（弧度） */
  mid: number
  /** 弧段外沿半径（引线起点） */
  r: number
  /** 反向引物（紫色 + ←）；缺省按名字推断 */
  rev?: boolean
}

export interface PrimerLabel {
  input: CircularLabelInput
  name: string
  /** 坐标文本（含括号与前导空格）；关闭「位置」或放不下时为空串 */
  pos: string
  /** 名牌文字宽（含方向符号，渲染时据此定 rect 宽度） */
  nameW: number
  /** 是否反向引物 */
  rev: boolean
}

/**
 * 引物标签所需的最大宽度（标签栏留白用）：取最长「名牌文字 + 坐标」。
 * 单个引物可能放不下时会被降级为紧凑形式，这里按全名估算即可。
 */
export function primerLabelWidth(
  primers: ReadonlyArray<{ name: string; start: number; end: number }>,
  opts: { font: number; posFont: number; showPos: boolean },
): number {
  let w = 0
  for (const p of primers) {
    const rev = isRevPrimer({ name: p.name, strand: 1 })
    const text = primerLabelText(p.name, rev)
    const base = textWidth(text, opts.font, true) + 2 * PRIMER_CHIP_PAD
    const pos = opts.showPos ? PRIMER_POS_GAP + textWidth(` (${p.start} .. ${p.end})`, opts.posFont) : 0
    w = Math.max(w, base + pos)
  }
  return w
}

/** 环形图外置标签的渲染描述（CircularMap 的 LabelInfo.primer 分支） */
export interface PrimerLabelInfo {
  kind: 'primer'
  name: string
  pos: string
  nameW: number
  rev: boolean
  members: string[]
}

/** 由标签结果生成渲染描述，视图层只做渲染 */
export function primerLabelInfo(lab: PrimerLabel, key: string): PrimerLabelInfo {
  return { kind: 'primer', name: lab.name, pos: lab.pos, nameW: lab.nameW, rev: lab.rev, members: [key] }
}

/** 名牌显示文本：`名字 →`（正向）/ `← 名字`（反向） */
export function primerLabelText(name: string, rev: boolean): string {
  return rev ? `← ${name}` : `${name} →`
}

/** 名牌内三个元素在用户空间的排布（渲染与校验共用同一份几何） */
export interface PrimerChipGeometry {
  /** 名牌矩形左沿 */
  chipX: number
  /** 名牌矩形宽度（含左右内边距） */
  chipW: number
  /** 坐标文字：左列 start 锚点 / 右列 start 锚点，null 表示不显示 */
  posX: number | null
}

/**
 * 计算名牌与坐标的位置。
 *
 * 布局给一列标签预留的框是 `[box.x, box.x + w]`，其中
 * `w = chipW + PRIMER_POS_GAP + posW`（见 buildPrimerLabel）。因此两个方向的
 * 元素都必须完整落在**框内**：
 *   - 右列：名牌贴框左沿（靠环），坐标在名牌右侧；
 *   - 左列：坐标贴框左沿（远离环），名牌贴框右沿（靠环）。
 *
 * 早前把左列坐标画在 `box.x` 左侧、名牌又多移一个内边距，坐标整段落在预留框
 * 外并溢出画布被裁掉（40 条密集引物里 9 条只剩后半截）——布局层查不出这类
 * 缺陷，因为框本身是合规的。
 */
export function primerChipGeometry(
  box: { x: number; w: number },
  side: 1 | -1,
  chipW: number,
  hasPos: boolean,
): PrimerChipGeometry {
  if (!hasPos) return { chipX: side === 1 ? box.x : box.x + box.w - chipW, chipW, posX: null }
  if (side === 1) return { chipX: box.x, chipW, posX: box.x + chipW + PRIMER_POS_GAP }
  return { chipX: box.x + box.w - chipW, chipW, posX: box.x }
}

/**
 * 紧凑标签（模块 C 渐进式暴露）：名字太长放不下时退到「序号 + 方向」，
 * 例如 `S09 →` / `← AS12`，把最后一段编号保留下来——同批引物靠编号就能
 * 区分，完整名字交给 hover 提示。连「序号 + 方向」都放不下时返回空串
 * （交给布局隐藏），不再降级到只剩方向符号——密集正反交替时一排孤箭头
 * 没有辨识度，看起来就是一团乱码（c01 密集区实测教训）。
 */
export function compactPrimerText(name: string, rev: boolean, maxW: number, font: number): string {
  // 取名字末尾的「字母前缀 + 序号」段（C01_AS12 → AS12，D_03_AS → AS）
  const tail = name.match(/([A-Za-z]+_?[A-Za-z]*\d*)$/)?.[1] || name
  const text = rev ? `← ${tail}` : `${tail} →`
  return textWidth(text, font, true) <= maxW ? text : ''
}

/**
 * 构造引物外标签。
 * @param maxW  标签栏可用宽度（px）
 * @param font  名字字号
 * @param posFont 坐标字号（小一号）
 * @param showPos 是否显示坐标（「位置」开关 && 非窄屏）
 * @param rowH  行高
 */
export function buildPrimerLabel(
  p: PrimerLabelSource,
  opts: { maxW: number; font: number; posFont: number; showPos: boolean; rowH: number; priority?: number },
): PrimerLabel | null {
  const { maxW, font, posFont, showPos, rowH } = opts
  const rev = p.rev ?? isRevPrimer({ name: p.name, strand: 1 })
  const full = primerLabelText(p.name, rev)
  const fullW = textWidth(full, font, true)
  // 名牌放不下全名 → 退到紧凑形式（保留编号），仍放不下才交给布局隐藏
  let text = full
  let nameW = fullW
  if (fullW + 2 * PRIMER_CHIP_PAD > maxW) {
    text = compactPrimerText(p.name, rev, maxW - 2 * PRIMER_CHIP_PAD, font)
    if (!text) return null
    nameW = textWidth(text, font, true)
  }
  const chipW = nameW + 2 * PRIMER_CHIP_PAD
  const posText = showPos ? ` (${p.pos})` : ''
  const posW = posText ? textWidth(posText, posFont) : 0
  // 坐标放不下时整段省略（截断半个数字比不写更糟），名牌本身照样显示
  const fitsPos = !!posText && chipW + PRIMER_POS_GAP + posW <= maxW
  return {
    input: {
      id: p.key,
      text: text + (fitsPos ? posText : ''),
      width: chipW + (fitsPos ? PRIMER_POS_GAP + posW : 0),
      height: rowH,
      angle: p.mid,
      radius: p.r + 6,
      priority: opts.priority ?? PRIMER_LABEL_PRIORITY,
    },
    name: p.name,
    pos: fitsPos ? posText : '',
    nameW,
    rev,
  }
}
