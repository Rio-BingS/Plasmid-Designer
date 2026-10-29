// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import { createMapStore } from '../src/map/store'
import { resolveHit } from '../src/map/hits'
import type { MapDocument } from '../src/map/types'

const seq = 'ATGAAACGTCTGGAAATCGCTGCTGAACTGCGTAAAGGCGCTGCTACCGTTCTGAAAGAACTGGCTAAA'
const doc: MapDocument = {
  schema: 'plasmid-map/1', name: 'T', topology: 'linear', length: seq.length, sequence: seq,
  features: [], enzymes: [], orfs: [],
  primers: [
    { id: 'p1', name: 'C01_S01', start: 1, end: 30, strand: 1, sequence: seq.slice(0, 30) },
    { id: 'p2', name: 'C01_AS02', start: 31, end: 60, strand: -1, sequence: seq.slice(30, 60) },
  ],
  meta: { source_format: 'raw', enzyme_set: 'common', warnings: [] },
} as unknown as MapDocument

describe('引物 Tooltip 内容', () => {
  it('含 Tm、GC%、链向与坐标', () => {
    const store = createMapStore(ref(doc))
    const hit = resolveHit(store, 'p:p1')!
    expect(hit.kind).toBe('primer')
    const text = hit.lines.join('\n')
    expect(text).toMatch(/正向/)
    expect(text).toMatch(/Tm\s+[\d.]+\s+°C/)
    expect(text).toMatch(/GC\s+[\d.]+\s*%/)
    expect(text).toMatch(/1\.\.30/)
  })

  it('反向引物显示 ← 与反向标记', () => {
    const store = createMapStore(ref(doc))
    const hit = resolveHit(store, 'p:p2')!
    expect(hit.lines.join('\n')).toMatch(/反向/)
  })
})
