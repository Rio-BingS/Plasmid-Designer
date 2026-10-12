/**
 * 载体列表的 map_preview → MapThumbnail 可用的最小文档。
 * 预览缺省、长度非法或坐标越界的特征一律跳过，列表照常显示（无缩略图）。
 */
import type { FeatureType, MapDocument, MapFeature, MapPreview } from './types'
import { FEATURE_COLORS } from './core/palette'

export type ThumbDoc = Pick<MapDocument, 'length' | 'topology' | 'features' | 'name'>

export function previewToThumb(p: MapPreview | null | undefined, name: string): ThumbDoc | null {
  if (!p || !Number.isFinite(p.length) || p.length <= 0) return null
  const L = Math.floor(p.length)
  const topology = p.topology === 'linear' ? 'linear' : 'circular'
  const features: MapFeature[] = (Array.isArray(p.features) ? p.features : [])
    .filter((f) => Number.isInteger(f.start) && Number.isInteger(f.end) && f.start >= 1 && f.end >= 1 && f.start <= L && f.end <= L)
    .filter((f) => topology === 'circular' || f.start <= f.end)
    .map((f, i) => ({
      id: `pv-${i}`, name: f.name, start: f.start, end: f.end,
      strand: f.strand === 1 || f.strand === -1 ? f.strand : 0,
      type: (f.type in FEATURE_COLORS ? f.type : 'misc_feature') as FeatureType,
      source: 'vector',
    }))
  return { name, length: L, topology, features }
}
