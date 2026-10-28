/**
 * 环外引物标签（SnapGene 图谱视图口径）。
 *
 * 官方行为（Show or Hide Restriction Site or Primer Coordinates on a Map）：
 *   - 引物结合区在环上是细弧段，名字是环外的**洋红纯文字**，靠细引线连到弧段；
 *   - 坐标（`(97 .. 114)`）不是名字的一部分，由「位置」开关统一控制，
 *     关掉就只剩名字；
 *   - 拥挤时只隐藏放不下的那几条，**标出来的名字与坐标一定完整**——
 *     截断成 `(1434 .. 14…` 比不写更糟。
 *
 * 这个模块只负责「一条引物 → 一个标签输入」，排布仍交给 core/labels。
 */
import { textWidth } from './text'
import { featurePriority } from './labels'
import type { CircularLabelInput } from './labels'

/** 引物标签颜色（SnapGene 洋红） */
export const PRIMER_LABEL_COLOR = '#a21caf'

/**
 * 引物标签优先级（SnapGene：引物识别位点与普通特征同档，拥挤时按序隐藏但
 * 绝不整体吞掉——只要有一条放得下就标一条）。略高于 misc_feature，
 * 免得被同长度的杂项特征挤掉。
 */
export const PRIMER_LABEL_PRIORITY = featurePriority('misc_feature', 9_999_999)

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
  /** 渲染用的坐标片段（含前导空格）；放不下或关了「位置」时为空串 */
  pos: string
}

/**
 * 构造引物外标签。
 * @param maxW  标签栏可用宽度（px）
 * @param font  名字字号
 * @param posFont 坐标字号（小一号）
 * @param showPos 是否显示坐标（「位置」开关 && 非窄屏）
 * @param rowH  行高
 * @param priority 标签优先级
 */
export function buildPrimerLabel(
  p: PrimerLabelSource,
  opts: { maxW: number; font: number; posFont: number; showPos: boolean; rowH: number; priority: number },
): PrimerLabel | null {
  const { maxW, font, posFont, showPos, rowH, priority } = opts
  const nameW = textWidth(p.name, font)
  if (nameW > maxW) return null // 名字都放不下：不截名字，整条交给布局隐藏
  const posText = showPos ? ` (${p.pos})` : ''
  const posW = posText ? textWidth(posText, posFont) : 0
  const fitsPos = !!posText && nameW + posW <= maxW
  return {
    input: {
      id: p.key,
      text: fitsPos ? p.name + posText : p.name,
      width: nameW + (fitsPos ? posW : 0),
      height: rowH,
      angle: p.mid,
      radius: p.r + 6,
      priority,
    },
    name: p.name,
    pos: fitsPos ? posText : '',
  }
}
