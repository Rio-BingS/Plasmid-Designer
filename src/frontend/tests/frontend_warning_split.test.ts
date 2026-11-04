/**
 * 警告文案拆分（ResultView 摘要 + 折叠详情）单测
 */
import { describe, it, expect } from 'vitest'
import { splitWarning } from '@/utils/warning'

describe('splitWarning', () => {
  it('全角冒号拆出摘要与详情', () => {
    const { summary, detail } = splitWarning(
      '已自动去除冗余起始密码子：NdeI 位点自带 ATG 并充当起始密码子，已去掉插入序列开头的 ATG')
    expect(summary).toBe('已自动去除冗余起始密码子')
    expect(detail).toContain('NdeI 位点自带 ATG')
  })

  it('半角冒号+空格同样拆分', () => {
    const { summary, detail } = splitWarning('Warning: something went wrong')
    expect(summary).toBe('Warning')
    expect(detail).toBe('something went wrong')
  })

  it('无冒号时整句作为摘要、详情为空', () => {
    const { summary, detail } = splitWarning('GC 含量超出目标范围')
    expect(summary).toBe('GC 含量超出目标范围')
    expect(detail).toBe('')
  })

  it('详情中的后续冒号保留不动（只按第一个冒号拆）', () => {
    const { summary, detail } = splitWarning('摘要：第一点：第二点')
    expect(summary).toBe('摘要')
    expect(detail).toBe('第一点：第二点')
  })
})
