/** 特征类型配色（文件/用户给了 color 时优先用其值） */
import type { FeatureType, MapFeature } from '../types'

export const FEATURE_COLORS: Record<FeatureType, string> = {
  cds: '#4e79c7', marker: '#e8b93e', promoter: '#e8656b', terminator: '#2fa98c',
  rep_origin: '#8fbf6b', rbs: '#f2a65a', tag: '#b07fd8', signal_peptide: '#d98cb3',
  polyA_signal: '#3fa7b5', enhancer: '#c75d8f', ltr: '#8c8c5a', primer_bind: '#5aa0e0',
  protein_bind: '#9b8bd1', misc_rna: '#6fc2c2', misc_feature: '#9aa5b1', insert: '#f08a24',
  mcs: '#e8923d',
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
