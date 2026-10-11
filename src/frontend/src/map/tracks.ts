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
