import { describe, it, expect } from 'vitest'
import { parseBatchInput, validateParsed } from '@/utils/fastaParse'

describe('parseBatchInput', () => {
  it('解析多记录 FASTA', () => {
    const text = '>A01-1 CDS\nATGGGCTTAAAC\nCCGGTTTAAGG\n>A01-2\nATGAAACCC'
    const out = parseBatchInput(text)
    expect(out).toHaveLength(2)
    expect(out[0]).toEqual({ name: 'A01-1', sequence: 'ATGGGCTTAAACCCGGTTTAAGG', named: true })
    expect(out[1]).toEqual({ name: 'A01-2', sequence: 'ATGAAACCC', named: true })
  })

  it('裸序列按空行与 --- 分隔，名称按序推断', () => {
    const text = 'ATGAAA\nTTT\n---\nGGGCCC\n\nATGTAG'
    const out = parseBatchInput(text)
    expect(out).toHaveLength(3)
    expect(out[0].name).toBe('sequence_1')
    expect(out[1].name).toBe('sequence_2')
    expect(out[2].name).toBe('sequence_3')
    expect(out[0].sequence).toBe('ATGAAATTT')
    expect(out[0].named).toBe(false)
  })

  it('FASTA 与裸序列混合', () => {
    const text = '>geneX\nATGAAA\n\nATGCCC\nGGG'
    const out = parseBatchInput(text)
    expect(out).toHaveLength(2)
    expect(out[0]).toEqual({ name: 'geneX', sequence: 'ATGAAA', named: true })
    expect(out[1].name).toBe('sequence_2')
  })

  it('无名 FASTA 头行（> 后为空）回退推断名', () => {
    const out = parseBatchInput('>\nATGAAA')
    expect(out[0].name).toBe('sequence_1')
    expect(out[0].named).toBe(false)
  })

  it('头行取第一个词为名', () => {
    const out = parseBatchInput('>sp|P123|PROT_HUMAN RecName\nATGAAA')
    expect(out[0].name).toBe('sp|P123|PROT_HUMAN')
  })

  it('清洗序列行内空白与数字', () => {
    const out = parseBatchInput('>g\n1 ATGGG\n2 CTTAA\n')
    expect(out[0].sequence).toBe('ATGGGCTTAA')
  })

  it('空输入与纯分隔符返回空数组', () => {
    expect(parseBatchInput('')).toEqual([])
    expect(parseBatchInput('   \n---\n\n')).toEqual([])
  })

  it('CRLF 行尾', () => {
    const out = parseBatchInput('>a\r\nATGAAA\r\n>b\r\nTTTCCC')
    expect(out).toHaveLength(2)
    expect(out[0].sequence).toBe('ATGAAA')
  })
})

describe('validateParsed', () => {
  it('DNA 只认 ACGT', () => {
    expect(validateParsed('ATGCatgc', 'dna')).toBe(true)
    expect(validateParsed('ATGU', 'dna')).toBe(false)
    expect(validateParsed('ATG*', 'dna')).toBe(false)
    expect(validateParsed('', 'dna')).toBe(false)
  })

  it('氨基酸放宽到字母与终止符', () => {
    expect(validateParsed('MGEKT*', 'amino_acid')).toBe(true)
    expect(validateParsed('MGE1', 'amino_acid')).toBe(false)
    expect(validateParsed('', 'amino_acid')).toBe(false)
  })
})
