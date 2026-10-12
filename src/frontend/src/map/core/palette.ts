/** 特征类型配色（文件/用户给了 color 时优先用其值） */
import type { FeatureType, MapFeature } from '../types'

/**
 * 柔和配色：沿用各类型原有色相（用户已熟悉），降低饱和度、略提亮，
 * 加上加深 28% 的描边后在白底上层次清楚、彼此协调（参考 SnapGene / BioRender 质粒图）。
 */
export const FEATURE_COLORS: Record<FeatureType, string> = {
  cds: '#5b8bd9', marker: '#efb84c', promoter: '#e9736f', terminator: '#47b096',
  rep_origin: '#94c47a', rbs: '#f3ab68', tag: '#b48ddf', signal_peptide: '#e19dbf',
  polyA_signal: '#4fb0bd', enhancer: '#cf6f9b', ltr: '#a5a27a', primer_bind: '#68ace4',
  protein_bind: '#a497db', misc_rna: '#76c6c6', misc_feature: '#a7b1bc', insert: '#f39a48',
  mcs: '#ee9f55',
}

export const FEATURE_TYPE_LABELS: Record<FeatureType, string> = {
  cds: 'CDS', marker: '抗性/筛选', promoter: '启动子', terminator: '终止子', rep_origin: '复制起点',
  rbs: 'RBS', tag: '标签', signal_peptide: '信号肽', polyA_signal: 'polyA', enhancer: '增强子',
  ltr: 'LTR', primer_bind: '引物结合', protein_bind: '蛋白结合', misc_rna: 'RNA', misc_feature: '其他',
  insert: '插入片段', mcs: 'MCS',
}

export function featureColor(f: Pick<MapFeature, 'type' | 'color'>): string {
  if (f.color && /^#[0-9a-f]{3,8}$/i.test(f.color)) return f.color
  return FEATURE_COLORS[f.type] ?? FEATURE_COLORS.misc_feature
}

/** 颜色加深（描边用） */
export function darken(hex: string, amount = 0.28): string {
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  const n = parseInt(h.slice(0, 6), 16)
  const k = 1 - amount
  const r = Math.round(((n >> 16) & 255) * k)
  const g = Math.round(((n >> 8) & 255) * k)
  const b = Math.round((n & 255) * k)
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`
}

/** 浅底上文字用黑或白 */
export function textOn(hex: string): string {
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  const n = parseInt(h.slice(0, 6), 16)
  const lum = 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)
  return lum > 150 ? '#10131a' : '#ffffff'
}

/** 与白色混合（amount = 白色占比），环外特征标签的淡底用 */
export function tint(hex: string, amount = 0.8): string {
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  const n = parseInt(h.slice(0, 6), 16)
  const mix = (v: number) => Math.round(v + (255 - v) * amount)
  return `#${((1 << 24) | (mix((n >> 16) & 255) << 16) | (mix((n >> 8) & 255) << 8) | mix(n & 255)).toString(16).slice(1)}`
}
