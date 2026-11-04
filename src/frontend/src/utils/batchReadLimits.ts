/**
 * 批量测序 read 上限：管理面板输入解析
 * 空白 = 跟随上一级（分组 → 服务器默认），否则须为 1..安全上限 的整数；
 * 与后端 app/batch_read_limits.validate_limit 同一规则，前端先拦一道给出就地提示
 */

export type LimitInput = number | string | null | undefined

export type ParsedLimit = { ok: true; value: number | null } | { ok: false; error: string }

export function parseLimitInput(raw: LimitInput, hardMax: number, label: string): ParsedLimit {
  if (raw === null || raw === undefined || String(raw).trim() === '') return { ok: true, value: null }
  const text = String(raw).trim()
  if (!/^\d+$/.test(text)) return { ok: false, error: `${label}须为正整数，留空表示跟随上一级设置` }
  const n = Number(text)
  if (n < 1) return { ok: false, error: `${label}须为正整数（≥1），留空表示跟随上一级设置` }
  if (n > hardMax) return { ok: false, error: `${label}不能超过服务器安全上限 ${hardMax} 条` }
  return { ok: true, value: n }
}

/** 输入框初值：null → 空白 */
export function limitToInput(v: number | null | undefined): string {
  return v === null || v === undefined ? '' : String(v)
}
