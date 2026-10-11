/**
 * 图谱文档的基础编辑（纯函数，返回新文档，不改原对象）：
 * 重命名 / 改色 / 删除特征，从选区新增特征。
 *
 * 用户改过的自动注释特征（source=auto）转为 source=file 并去掉 auto 信息：
 * 它已被用户确认，后端 annotate=true 重新注释时不应再被丢弃重算。
 * 契约的 source 没有 "user"，用户新增特征也记为 source=file（=随文档保存的注释）。
 */
import type { FeatureType, MapDocument, MapFeature, Range, Strand } from '../types'

export type FeaturePatch = Partial<Pick<MapFeature, 'name' | 'color' | 'type' | 'strand' | 'notes'>>

function confirmAuto(f: MapFeature): MapFeature {
  if (f.source !== 'auto') return f
  const { auto: _auto, ...rest } = f
  return { ...rest, source: 'file' }
}

export function updateFeature(doc: MapDocument, id: string, patch: FeaturePatch): MapDocument {
  let hit = false
  const features = doc.features.map((f) => {
    if (f.id !== id) return f
    hit = true
    const next: MapFeature = { ...confirmAuto(f), ...patch }
    if (patch.name !== undefined) next.name = patch.name.trim() || f.name
    if (patch.color === '') delete next.color
    return next
  })
  return hit ? { ...doc, features } : doc
}

export function deleteFeature(doc: MapDocument, id: string): MapDocument {
  const features = doc.features.filter((f) => f.id !== id)
  return features.length === doc.features.length ? doc : { ...doc, features }
}

/** 文档内不冲突的新 id：user-1, user-2, … */
export function nextFeatureId(doc: Pick<MapDocument, 'features'>, prefix = 'user'): string {
  const used = new Set(doc.features.map((f) => f.id))
  let n = 1
  while (used.has(`${prefix}-${n}`)) n++
  return `${prefix}-${n}`
}

export interface NewFeature {
  name: string
  type: FeatureType
  strand: Strand
  color?: string
}

/** 校验选区能否成为特征；返回中文错误或 null */
export function validateRange(doc: Pick<MapDocument, 'length' | 'topology'>, r: Range | null): string | null {
  if (!r) return '请先在图谱上拖选一段区域'
  const L = doc.length
  const ok = (x: number) => Number.isInteger(x) && x >= 1 && x <= L
  if (!ok(r.start) || !ok(r.end)) return `坐标须在 1..${L} 之间`
  if (r.start > r.end && doc.topology !== 'circular') return '线性分子的起点不能大于终点'
  return null
}

export function addFeature(doc: MapDocument, r: Range, f: NewFeature): MapDocument {
  const err = validateRange(doc, r)
  if (err) throw new Error(err)
  const name = f.name.trim()
  if (!name) throw new Error('请填写特征名称')
  const feature: MapFeature = {
    id: nextFeatureId(doc), name, type: f.type, start: r.start, end: r.end, strand: f.strand, source: 'file',
  }
  if (f.color) feature.color = f.color
  return { ...doc, features: [...doc.features, feature] }
}

/** 有限步撤销栈 */
export function pushHistory<T>(stack: T[], item: T, limit = 30): T[] {
  const out = [...stack, item]
  return out.length > limit ? out.slice(out.length - limit) : out
}
