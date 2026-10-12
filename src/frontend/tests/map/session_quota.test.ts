/** /map 页暂存写入失败（审查 M1）：不能留下旧文档让刷新"恢复"出上一份序列 */
import { describe, it, expect } from 'vitest'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import { MAP_SESSION_KEY, loadMapSession, saveMapSession } from '@/map/session'

/** 内存 Storage：值超过 limit 字符时 setItem 抛 QuotaExceededError */
function quotaStorage(limit: number): Storage {
  const m = new Map<string, string>()
  return {
    get length() { return m.size },
    clear: () => m.clear(),
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    key: (i: number) => [...m.keys()][i] ?? null,
    removeItem: (k: string) => { m.delete(k) },
    setItem: (k: string, v: string) => {
      if (v.length > limit) throw new DOMException('quota', 'QuotaExceededError')
      m.set(k, v)
    },
  }
}

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

describe('saveMapSession 配额失败', () => {
  it('新文档写入失败时删掉旧键，刷新后不恢复旧文档', () => {
    const old = puc()
    const st = quotaStorage(JSON.stringify(old).length + 10)
    expect(saveMapSession(old, st)).toBe(true)
    expect(loadMapSession(st)?.name).toBe(old.name)

    const big = { ...puc(), name: 'BIG', sequence: 'A'.repeat(200_000), length: 200_000 }
    expect(saveMapSession(big, st)).toBe(false)
    expect(st.getItem(MAP_SESSION_KEY)).toBeNull()
    expect(loadMapSession(st)).toBeNull()
  })

  it('removeItem 也抛错时不向外抛出，仍返回 false', () => {
    const st = quotaStorage(0)
    st.removeItem = () => { throw new Error('denied') }
    expect(saveMapSession(puc(), st)).toBe(false)
  })
})
