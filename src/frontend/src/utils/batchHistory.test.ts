import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  loadBatchHistory, recordBatch, removeFromBatchHistory,
  type BatchHistoryEntry,
} from '@/utils/batchHistory'

const entry = (id: string, submittedAt = 1000): BatchHistoryEntry => ({
  batchId: id, submittedAt, total: 2,
})

describe('batchHistory', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('空存储返回空数组', () => {
    expect(loadBatchHistory()).toEqual([])
  })

  it('记录与读取（最新在前）', () => {
    recordBatch(entry('batch_a', 1))
    recordBatch(entry('batch_b', 2))
    const list = loadBatchHistory()
    expect(list.map((e) => e.batchId)).toEqual(['batch_b', 'batch_a'])
  })

  it('重复记录同 batchId 去重并置顶', () => {
    recordBatch(entry('batch_a', 1))
    recordBatch(entry('batch_b', 2))
    recordBatch(entry('batch_a', 3))
    expect(loadBatchHistory().map((e) => e.batchId)).toEqual(['batch_a', 'batch_b'])
  })

  it('最多保留 10 条', () => {
    for (let i = 0; i < 15; i++) recordBatch(entry(`batch_${i}`, i))
    const list = loadBatchHistory()
    expect(list).toHaveLength(10)
    expect(list[0].batchId).toBe('batch_14')
  })

  it('损坏数据回退为空且不抛错', () => {
    localStorage.setItem('plasmid-batch-history', '{not json')
    expect(loadBatchHistory()).toEqual([])
    localStorage.setItem('plasmid-batch-history', '["str", 42, null]')
    expect(loadBatchHistory()).toEqual([])
  })

  it('存储异常静默（隐私模式）', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    expect(() => recordBatch(entry('batch_x'))).not.toThrow()
    expect(() => removeFromBatchHistory('batch_x')).not.toThrow()
  })

  it('删除指定条目', () => {
    recordBatch(entry('batch_a'))
    recordBatch(entry('batch_b'))
    removeFromBatchHistory('batch_a')
    expect(loadBatchHistory().map((e) => e.batchId)).toEqual(['batch_b'])
  })
})
