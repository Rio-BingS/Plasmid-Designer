/**
 * 环形图引物几何：弧带路径 + 3′ 端延伸箭头 + 外置标签角度。
 *
 * 从 CircularMap 抽出（文件已触到尺寸预算上限），逻辑与视图解耦：
 * 输入是已有几何函数（polar / ang / arcBandPath）与半径，输出可直接进 SVG。
 */
import { PRIMER_FWD, PRIMER_REV, isRevPrimer } from './primer_style'

export interface PrimerArcGeom {
  id: string
  name: string
  key: string
  /** `"start .. end"`，标签用 */
  pos: string
  /** 弧带中点角度（弧度），外置标签锚点 */
  mid: number
  /** 标签引线起点半径 */
  r: number
  rev: boolean
  fill: string
  stroke: string
  /** 弧带 path */
  d: string
  /** 3′ 端小三角 path */
  arrow: string
}

/** 保留两位小数（SVG path 用；避免浮点长尾让 DOM 膨胀） */
const r2 = (v: number) => Math.round(v * 100) / 100

/**
 * 弧带 3′ 端的小箭头：在半径 r、角度 a 处沿圆弧切向画等腰三角。
 *
 * 角度约定（与 core/coords.polar 一致）：0 在顶部、顺时针增大，
 * 故顺时针切向量 (cos a, sin a)、径向法向 (sin a, −cos a)。
 */
export function arcArrowPath(
  polar: (r: number, a: number) => [number, number],
  r: number,
  a: number,
  dir: 1 | -1,
  size: number,
): string {
  const [tipX, tipY] = polar(r, a)
  const tx = Math.cos(a) * dir, ty = Math.sin(a) * dir
  const nx = Math.sin(a), ny = -Math.cos(a)
  const back = size * 1.8
  const b1x = tipX - tx * back + nx * size, b1y = tipY - ty * back + ny * size
  const b2x = tipX - tx * back - nx * size, b2y = tipY - ty * back - ny * size
  return `M${r2(tipX)} ${r2(tipY)}L${r2(b1x)} ${r2(b1y)}L${r2(b2x)} ${r2(b2y)}Z`
}

/** 引物弧带 + 箭头（按正反向分色；3′ 端 = 正向顺时针端 / 反向逆时针端） */
export function buildPrimerArcs(
  primers: Array<{ id: string; name: string; start: number; end: number; strand: 1 | -1 }>,
  env: {
    lanes: Map<string, number>
    L: number
    /** 弧带内半径基准（第 0 道） */
    baseR: number
    /** 两道之间的半径间距 */
    laneGap: number
    ang: (x: number) => number
    unroll: (r: { start: number; end: number }) => { from: number; to: number }
    arcBand: (r1: number, r2: number, a1: number, a2: number, dir: 1 | -1, head: number) => string
    polar: (r: number, a: number) => [number, number]
  },
): PrimerArcGeom[] {
  const out: PrimerArcGeom[] = []
  for (const p of primers) {
    const lane = env.lanes.get(p.id)
    if (lane === undefined) continue
    const r1 = env.baseR + 14 + lane * env.laneGap
    const u = env.unroll(p)
    const rev = isRevPrimer(p)
    const pal = rev ? PRIMER_REV : PRIMER_FWD
    const dir: 1 | -1 = rev ? -1 : 1
    const tipA = rev ? env.ang(u.from) : env.ang(u.to)
    out.push({
      id: p.id, name: p.name, key: `p:${p.id}`,
      pos: `${p.start} .. ${p.end}`,
      mid: (env.ang(u.from) + env.ang(u.to)) / 2,
      r: r1 + 4, rev,
      fill: pal.base, stroke: pal.dark,
      d: env.arcBand(r1, r1 + 4, env.ang(u.from),
        env.ang(Math.max(u.to, u.from + env.L / 1000)), dir, 5 / r1),
      // 3′ 端小三角画在弧带外侧 6px 处，避免被弧带盖住
      arrow: arcArrowPath(env.polar, r1 + 10, tipA, dir, 3.4),
    })
  }
  return out
}
