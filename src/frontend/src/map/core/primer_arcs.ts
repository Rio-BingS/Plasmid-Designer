/**
 * 环形图引物几何：弧带路径 + 外置标签角度。
 *
 * 从 CircularMap 抽出（文件已触到尺寸预算上限），逻辑与视图解耦：
 * 输入是已有几何函数（polar / ang / arcBandPath）与半径，输出可直接进 SVG。
 *
 * 箭头完全由弧带自身收成（arcBandPath 的 taper 端，SnapGene 同款）。
 * 曾在弧带外 6px 处再叠一个独立小三角——密集区相邻车道只差 6px，
 * 三角恰好压在邻道弧带上，14 条正反交替的引物看起来就是一簇乱箭。
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
  /** 弧带 path（3′ 端收成箭头） */
  d: string
}

/** 引物弧带（按正反向分色；3′ 端 = 正向顺时针端 / 反向逆时针端） */
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
    out.push({
      id: p.id, name: p.name, key: `p:${p.id}`,
      pos: `${p.start} .. ${p.end}`,
      mid: (env.ang(u.from) + env.ang(u.to)) / 2,
      r: r1 + 4, rev,
      fill: pal.base, stroke: pal.dark,
      // 3′ 端收成锥形箭头：头端角度按带厚取（≈带厚/半径 rad，约等于 45° 锥面），
      // 与特征弧带的 head 比例一致但略窄，密集重叠区不至于把邻带盖满
      d: env.arcBand(r1, r1 + 5, env.ang(u.from),
        env.ang(Math.max(u.to, u.from + env.L / 1000)), dir, 6 / r1),
    })
  }
  return out
}
