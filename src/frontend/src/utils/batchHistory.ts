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

export function loadBatchHistory(): BatchHistoryEntry[] {
  try {
    const raw = localStorage.getItem(KEY)
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
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX_ITEMS)))
  } catch {
    // 存储满/隐私模式：历史是便利功能，静默放弃
  }
}

export function removeFromBatchHistory(batchId: string): void {
  const list = loadBatchHistory().filter((e) => e.batchId !== batchId)
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    // 同上
  }
}
