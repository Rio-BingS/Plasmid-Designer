/**
 * 批量任务本地历史：localStorage 记录最近提交的批量任务。
 *
 * 刷新/重开页面后仍可回到进度页继续看结果与下载；换浏览器/设备不可见
 * （服务端历史列表是二期）。
 */

export interface BatchHistoryEntry {
  batchId: string
  submittedAt: number
  total: number
}

const KEY = 'plasmid-batch-history'
const MAX_ITEMS = 10

/** localStorage 访问兜底：隐私模式/被禁用/存储满时直接读写该属性即抛（未兜住的调用方整页白屏）。
 * 读失败返回 null，写失败静默放弃——记忆/历史类便利功能可接受。 */
export function storageGet(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}

export function storageSet(key: string, value: string): void {
  try { localStorage.setItem(key, value) } catch { /* 同上：静默放弃 */ }
}

export function loadBatchHistory(): BatchHistoryEntry[] {
  try {
    const raw = storageGet(KEY)
    if (!raw) return []
    const list = JSON.parse(raw)
    if (!Array.isArray(list)) return []
    return list.filter(
      (e): e is BatchHistoryEntry =>
        e && typeof e.batchId === 'string' && typeof e.submittedAt === 'number'
    )
  } catch {
    return []
  }
}

export function recordBatch(entry: BatchHistoryEntry): void {
  const list = [entry, ...loadBatchHistory().filter((e) => e.batchId !== entry.batchId)]
  storageSet(KEY, JSON.stringify(list.slice(0, MAX_ITEMS)))
}

export function removeFromBatchHistory(batchId: string): void {
  const list = loadBatchHistory().filter((e) => e.batchId !== batchId)
  storageSet(KEY, JSON.stringify(list))
}
