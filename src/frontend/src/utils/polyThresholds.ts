/**
 * poly「大量缺失」报警阈值（二期 10d，用户 2026-11-17 拍板：UI 可改）。
 *
 * 两条是「或」关系：确证缺失 ≥count 个，或占该 poly 参考长度 ≥pct%，任一
 * 达到即报警；取消勾选的那条不参与，两条都取消则不报警。与后端
 * core/sanger/params.resolve_poly_thresholds 同口径。设置存在浏览器本地，
 * 分析请求（单样品 / 批量 / 分块批量）自动带上；与默认相同时不发送。
 */
export interface PolyThresholds {
  count: number
  pct: number
  count_enabled: boolean
  pct_enabled: boolean
}

export const DEFAULT_POLY_THRESHOLDS: Readonly<PolyThresholds> = Object.freeze({
  count: 5, pct: 15, count_enabled: true, pct_enabled: true,
})

const KEY = 'pd_seq_poly_thresholds'

export function validatePolyThresholds(t: PolyThresholds): string {
  if (!Number.isInteger(t.count) || t.count < 1) return '缺失个数须为 ≥1 的整数'
  if (!Number.isFinite(t.pct) || t.pct <= 0 || t.pct > 100) return '缺失比例须在 0–100% 之间'
  return ''
}

export function loadPolyThresholds(): PolyThresholds {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) {
      const v = { ...DEFAULT_POLY_THRESHOLDS, ...JSON.parse(raw) } as PolyThresholds
      if (!validatePolyThresholds(v)) return v
    }
  } catch { /* 本地存储不可用时用默认 */ }
  return { ...DEFAULT_POLY_THRESHOLDS }
}

export function savePolyThresholds(t: PolyThresholds): void {
  try {
    if (isDefaultPolyThresholds(t)) localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, JSON.stringify(t))
  } catch { /* 忽略 */ }
}

export function isDefaultPolyThresholds(t: PolyThresholds): boolean {
  const d = DEFAULT_POLY_THRESHOLDS
  return t.count === d.count && t.pct === d.pct
    && t.count_enabled === d.count_enabled && t.pct_enabled === d.pct_enabled
}

/** 表单值：与默认相同（或不合法）时发空串，后端走默认 */
export function polyThresholdsFormValue(t: PolyThresholds = loadPolyThresholds()): string {
  if (validatePolyThresholds(t) || isDefaultPolyThresholds(t)) return ''
  return JSON.stringify(t)
}

/** 给 FormData 追加 poly_thresholds（非默认时） */
export function appendPolyThresholds(form: FormData): void {
  const v = polyThresholdsFormValue()
  if (v) form.append('poly_thresholds', v)
}
