import { describe, expect, it } from 'vitest'

import {
  AMINO_ACID_CHARS,
  findUnsupportedChars,
  describeUnsupportedChars
} from './sequence'

describe('findUnsupportedChars', () => {
  it('DNA 大小写都接受（后端提交时会统一转大写）', () => {
    expect(findUnsupportedChars('atgcATGC', 'ATGC')).toEqual([])
    expect(findUnsupportedChars('gattaca', 'ATGC')).toEqual([])
  })

  it('氨基酸大小写不敏感地匹配单字母代码', () => {
    expect(findUnsupportedChars('mktayia', AMINO_ACID_CHARS)).toEqual([])
  })

  it('真正的非法字符仍被报出，且位置按原文计算', () => {
    expect(findUnsupportedChars('ATGX', 'ATGC')).toEqual([
      { char: 'X', index: 4 }
    ])
    // 小写不豁免非法字符（x 不是任何碱基）
    expect(findUnsupportedChars('atgx', 'ATGC')).toEqual([
      { char: 'x', index: 4 }
    ])
  })

  it('空白与零宽字符不算非法', () => {
    expect(findUnsupportedChars('AT GC\nTA\u200bCG\ufeff', 'ATGC')).toEqual([])
  })
})

describe('describeUnsupportedChars', () => {
  it('DNA 提示文案包含合法字母表', () => {
    const msg = describeUnsupportedChars(
      findUnsupportedChars('atgB', 'ATGC'),
      'dna'
    )
    expect(msg).toContain('B')
    expect(msg).toContain('A/T/G/C')
  })
})
