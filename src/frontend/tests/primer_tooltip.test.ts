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

describe('hover 同名引物多处结合', () => {
  it('同名的多个结合位点全部纳入高亮', async () => {
    const { hoverHighlightRanges } = await import('../src/map/core/primer_hover')
    const primers = [
      { id: 'a', name: 'M13F', start: 10, end: 30, strand: 1 as const, sequence: '' },
      { id: 'b', name: 'M13F', start: 100, end: 120, strand: 1 as const, sequence: '' },
      { id: 'c', name: 'M13R', start: 50, end: 70, strand: -1 as const, sequence: '' },
    ]
    // 悬停其中一条 M13F → 两处 M13F 都高亮，M13R 不受影响
    const rs = hoverHighlightRanges('p:a', { start: 10, end: 30 }, primers as never)
    expect(rs).toHaveLength(2)
    expect(rs.map((r) => r.start).sort((x, y) => x - y)).toEqual([10, 100])
    // 非引物命中只高亮自身区间
    const f = hoverHighlightRanges('f:x', { start: 5, end: 9 }, primers as never)
    expect(f).toEqual([{ start: 5, end: 9 }])
  })
})

describe('文档下发 tm/gc 优先于前端计算', () => {
  it('带 tm/gc 字段时直接使用（不重复计算）', () => {
    const p = { id: 'p1', name: 'X', start: 1, end: 20, strand: 1 as const, sequence: doc.primers[0].sequence, tm: 61.2, gc: 55.5 }
    const d2 = { ...doc, primers: [p] } as unknown as MapDocument
    const store = createMapStore(ref(d2))
    const hit = resolveHit(store, 'p:p1')!
    const text = hit.lines.join('\n')
    expect(text).toContain('Tm 61.2 °C')
    expect(text).toContain('GC 55.5%')
  })
})

describe('Tooltip 渲染性能（模块 C「响应迅速」）', () => {
  it('hover 状态变更 → Tooltip DOM 更新在 1 帧内（<16.7ms）', async () => {
    const { mount } = await import('@vue/test-utils')
    const { nextTick } = await import('vue')
    const MapTooltip = (await import('../src/map/components/MapTooltip.vue')).default
    const store = createMapStore(ref(JSON.parse(JSON.stringify(doc)) as MapDocument))
    const w = mount(MapTooltip, { props: { store: store as never }, attachTo: document.body })
    // 模拟 30 次悬停（不同引物交替），测量每次「设置 hover → DOM 出现内容」的耗时
    const times: number[] = []
    for (let i = 0; i < 30; i++) {
      const t0 = performance.now()
      store.hover.value = {
        kind: 'primer', id: i % 2 ? 'p:p1' : 'p:p2', title: i % 2 ? 'C01_S01' : 'C01_AS02',
        lines: ['引物 · 正向 →', '1..30（30 bp）', 'Tm 62.0 °C · GC 50.0%', doc.primers[0].sequence],
        range: { start: 1, end: 30 }, x: 100, y: 100,
      }
      await nextTick()
      // DOM 真有文本才算渲染完成
      if (!w.text().includes('C01')) throw new Error('tooltip 未渲染')
      times.push(performance.now() - t0)
    }
    const avg = times.reduce((a, b) => a + b, 0) / times.length
    const worst = Math.max(...times)
    // 渲染成本必须远低于一帧预算（16.7ms），否则悬停会掉帧
    expect(avg).toBeLessThan(16.7)
    expect(worst).toBeLessThan(50)
    w.unmount()
  })
})
