/**
 * /map 页的文档暂存：放 sessionStorage，刷新页面不丢（关闭标签页即清除）。
 * 超出配额（大序列）时放弃暂存并删掉旧键，避免刷新后静默恢复上一份文档；返回 false
 * 由页面提示"刷新后需重新打开"。
 */
import type { MapDocument } from './types'

export const MAP_SESSION_KEY = 'pd.map.document.v1'

export function saveMapSession(doc: MapDocument | null, storage: Storage | undefined = globalThis.sessionStorage): boolean {
  if (!storage) return false
  try {
    if (!doc) storage.removeItem(MAP_SESSION_KEY)
    else storage.setItem(MAP_SESSION_KEY, JSON.stringify(doc))
    return true
  } catch {
    // 写入失败（配额等）：旧键里是上一份文档，必须删掉，否则刷新会把它当成当前文档恢复
    try { storage.removeItem(MAP_SESSION_KEY) } catch { /* 存储整体不可用：无可恢复内容 */ }
    return false
  }
}

export function loadMapSession(storage: Storage | undefined = globalThis.sessionStorage): MapDocument | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(MAP_SESSION_KEY)
    if (!raw) return null
    const d = JSON.parse(raw) as MapDocument
    if (d?.schema !== 'plasmid-map/1' || typeof d.sequence !== 'string' || !Array.isArray(d.features)) return null
    return d
  } catch {
    return null
  }
}
