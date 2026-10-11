/**
 * /map 页的文档暂存：放 sessionStorage，刷新页面不丢（关闭标签页即清除）。
 * 超出配额（大序列）时静默放弃，不影响使用。
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
