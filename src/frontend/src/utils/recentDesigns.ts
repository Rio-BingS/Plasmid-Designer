/**
 * 最近设计记录（localStorage）
 * ResultView 在设计完成时写入，供测序分析等页面快速选择参考序列。
 * 纯本地记录，不涉及后端存储。
 */

export interface RecentDesign {
  design_id: string
  /** 载体骨架名（无载体设计为空串） */
  vector_name: string
  /** 构建体长度（bp） */
  length: number
  /** 设计创建时间（ISO） */
  time: string
}

const KEY = 'recent_designs_v1'
const MAX = 8

export function listRecentDesigns(): RecentDesign[] {
  try {
    const raw = localStorage.getItem(KEY)
    const list = raw ? JSON.parse(raw) : []
    return Array.isArray(list) ? list : []
  } catch {
    return []
  }
}

export function recordRecentDesign(d: RecentDesign): void {
  try {
    const list = listRecentDesigns().filter((x) => x.design_id !== d.design_id)
    list.unshift(d)
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)))
  } catch {
    // localStorage 不可用（隐私模式等）时静默跳过
  }
}
