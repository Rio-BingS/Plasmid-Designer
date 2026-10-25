/**
 * 设计页表单草稿：切到别的页面（SPA 路由）或刷新后不丢。
 *
 * 放 sessionStorage，与 /map 页的文档暂存同一策略：同一标签页内有效、关闭
 * 标签页即清除。序列与载体属于用户数据，不做跨会话长期驻留；也不走
 * localStorage，避免多人共用一台机器时把上一个人的序列留给下一个人。
 *
 * 读取一律做形状校验：旧版本/手改过的草稿不能把页面带进非法状态，
 * 未知字段直接忽略（由调用方按白名单取值）。
 */

export const DESIGN_DRAFT_KEY = 'pd.design.draft.v1'

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | undefined

export interface DesignDraft {
  sequenceType?: string
  sequence?: string
  sequenceName?: string
  vectorId?: string
  vectorMode?: string
  adhocVector?: unknown
  cloningMethod?: string
  insertSource?: string
  protocolLanguage?: string
  optimizeCodons?: boolean
  targetSpecies?: string
  gcMin?: number
  gcMax?: number
  homologyArm?: number
  enzyme?: string
  enzyme5?: string
  enzyme3?: string
  gibsonSite?: string
  gibsonSingleSite?: boolean
  excludeEnzymes?: string[]
  oligoLengthMin?: number
  oligoLengthMax?: number
  overlapLength?: number
  savedAt?: number
}

export function saveDesignDraft(
  draft: DesignDraft,
  storage: StorageLike = globalThis.sessionStorage
): boolean {
  if (!storage) return false
  try {
    storage.setItem(DESIGN_DRAFT_KEY, JSON.stringify({ ...draft, savedAt: Date.now() }))
    return true
  } catch {
    // 配额超了（超长序列/大载体）：删掉旧草稿，避免恢复出一份过期的半截表单
    try { storage.removeItem(DESIGN_DRAFT_KEY) } catch { /* 存储整体不可用 */ }
    return false
  }
}

export function loadDesignDraft(
  storage: StorageLike = globalThis.sessionStorage
): DesignDraft | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(DESIGN_DRAFT_KEY)
    if (!raw) return null
    const data = JSON.parse(raw) as DesignDraft
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null
    return data
  } catch {
    return null
  }
}

export function clearDesignDraft(storage: StorageLike = globalThis.sessionStorage): void {
  if (!storage) return
  try { storage.removeItem(DESIGN_DRAFT_KEY) } catch { /* 存储不可用：无内容可清 */ }
}
