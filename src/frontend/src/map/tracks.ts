/** LinearMap 额外轨道（如测序 read、覆盖度、变异），由调用方从自身数据构造 */
import type { Range } from './types'

export interface TrackItem {
  id: string
  start: number
  end: number
  /** 跨原点等需要分段画的项（箭头头部画在 3' 端所在段） */
  segments?: Range[]
  strand?: 1 | -1 | 0
  label?: string
  color?: string
  stroke?: string
  /** 箭头上的标记位点（如差异碱基），画成空心圆 */
  marks?: number[]
  /** 箭头上带样式的标记位点（如双峰/未决位点）：color 描边，filled 实心 */
  dots?: { pos: number; color: string; filled?: boolean }[]
  /** marks 轨道：刻块上方的文字标注（与特征标签同一套避让布局，引线连回刻块） */
  markLabel?: string
  /** marks 轨道：标注优先级（空间不足时先保高优先级） */
  priority?: number
  tooltip?: { title: string; lines: string[] }
  className?: string
}

export interface MapTrack {
  id: string
  /** arrows = 分道箭头；axis = 叠在主轴上的色段；marks = 主轴上方的小刻块 */
  kind: 'arrows' | 'axis' | 'marks'
  position?: 'above' | 'below'
  /** 箭头高度（px） */
  height?: number
  items: TrackItem[]
}
