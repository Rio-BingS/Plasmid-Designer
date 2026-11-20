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
  /** 箭头内部的分段着色（如逐碱基质量）：裁剪在箭头形状内；hatch = 斜线灰（不可信区） */
  bands?: { start: number; end: number; color: string; opacity?: number; hatch?: boolean }[]
  /** 箭头上的竖线标记（错配/插入缺失/双峰等），贯穿箭头高度 */
  ticks?: { pos: number; color: string; width?: number; title?: string }[]
  /** 箭头外侧的小字摘要（如等级/Q20/一致性） */
  badge?: string
  /** marks 轨道：刻块上方的文字标注（与特征标签同一套避让布局，引线连回刻块） */
  markLabel?: string
  /** marks 轨道：标注优先级（空间不足时先保高优先级） */
  priority?: number
  tooltip?: { title: string; lines: string[] }
  className?: string
}

export interface MapTrack {
  id: string
  /** arrows = 分道箭头；axis = 叠在主轴上的色段；marks = 主轴上方的小刻块；
   *  strip = 箭头轨道下方的一条细色带（如共识/双向确认状态），label 写在左端；
   *  regions = 贯穿 read 层到参考层的底色区段（如 CDS），实线边界，label 写在 read 层顶部；
   *  pins = 顶部编号圆标（markLabel = 编号），竖向引导线贯穿到参考元件层；区段画淡色竖条 */
  kind: 'arrows' | 'axis' | 'marks' | 'strip' | 'regions' | 'pins'
  /** strip：左端说明文字 */
  label?: string
  position?: 'above' | 'below'
  /** 箭头高度（px） */
  height?: number
  items: TrackItem[]
}
