/**
 * 目的基因模式（CDS 对比）的前端配套：从分析结果特征表里定位目标 CDS，
 * 并为每条 read 计算「比对区间 ∩ 目的 CDS」的覆盖关系（匹配引物）。
 *
 * 后端 CDS 模式的参考替换发生在分析时（core.sanger.cds_extract）：伪 CDS
 * 特征的 description 以「目的基因模式：」开头、区间即目标 CDS，5′/3′ 侧翼
 * 以 misc_feature 标注。分析记录持久化了 features 与逐 read 比对区间，
 * 关系匹配是纯展示层推导——不依赖额外字段，历史回看同样可用。
 */
import type { SequencingAnalysis } from '@/api'

type AnalysisFeature = SequencingAnalysis['features'][number]
type AnalysisRead = SequencingAnalysis['reads'][number]

export interface CdsTarget {
  name: string
  start: number
  end: number
}

/** 目的基因模式的目标 CDS 特征；非 CDS 模式 / 旧记录返回 null（不显示该列）。
 *  机器标记 analysis_target（与后端 pipeline 结论资格门同源）为主，
 *  description 前缀兜底（兼容标记上线前的记录） */
export function findCdsTarget(features: AnalysisFeature[] | undefined): CdsTarget | null {
  const feat = (features || []).find(
    (f) => f.type === 'CDS' &&
      (f.analysis_target === true || (f.description || '').startsWith('目的基因模式')))
  if (!feat) return null
  return { name: feat.name, start: feat.start, end: feat.end }
}

export interface ReadCdsRelation {
  /** read 比对区间与目的 CDS 有交集 */
  covers: boolean
  /** 覆盖的 CDS 碱基数与占 CDS 全长的百分比（covers=false 时为 0） */
  coveredBp: number
  percent: number
  /** 交集落在 CDS 内的起止（CDS 内坐标；covers=false 时 null） */
  overlapStart: number | null
  overlapEnd: number | null
}

/** read 比对区间（跨原点用 ref_segments 展开）与目的 CDS 区间求交 */
export function cdsRelation(read: AnalysisRead, target: CdsTarget): ReadCdsRelation {
  const segs: [number, number][] = read.ref_segments?.length
    ? read.ref_segments.map((s) => [s[0], s[1]])
    : [[read.ref_start, read.ref_end]]
  let overlapStart: number | null = null
  let overlapEnd: number | null = null
  for (const [rs, re] of segs) {
    const s = Math.max(rs, target.start)
    const e = Math.min(re, target.end)
    if (s <= e) {
      if (overlapStart === null || s < overlapStart) overlapStart = s
      if (overlapEnd === null || e > overlapEnd) overlapEnd = e
    }
  }
  if (overlapStart === null || overlapEnd === null) {
    return { covers: false, coveredBp: 0, percent: 0, overlapStart: null, overlapEnd: null }
  }
  const coveredBp = overlapEnd - overlapStart + 1
  const total = target.end - target.start + 1
  return { covers: true, coveredBp, percent: (coveredBp / total) * 100,
           overlapStart, overlapEnd }
}

/** 一句话汇总：N/M 条 read 覆盖目的 CDS（read 摘要区标题旁展示） */
export function cdsCoverageSummary(reads: AnalysisRead[], target: CdsTarget): string {
  let covering = 0
  for (const r of reads) if (cdsRelation(r, target).covers) covering++
  return `${covering}/${reads.length} 条 read 覆盖目的 CDS「${target.name}」`
}
