import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ConclusionCards from '@/components/sequencing/ConclusionCards.vue'
import type { SequencingAnalysis } from '@/api'

// M13 前端半边（goal T2）：徽标读 verdict.status、阻断项按 blocking 结构化
// 列出、裁决行优先 resolved_regions 结构化渲染；旧记录（无 verdict）退回旧逻辑

const baseAnalysis: SequencingAnalysis = {
  analysis_id: 'seq_t2',
  sample_name: 'mx',
  created_at: '2026-11-07T00:00:00',
  engine: 'internal+biopython',
  conclusion: '△ 结论：1 处未决差异，需核对峰图或补测\n正文第一行',
  reads: [{
    index: 0, filename: 'r1.ab1', raw_length: 500, trimmed_length: 480,
    mean_q: 38, direction: '+', ref_start: 1, ref_end: 480,
    identity: 0.99, mixed_positions: []
  }],
  variants: [],
  consensus: { sequence: 'ACGT', covered_ranges: [[1, 100]], coverage_percent: 92 },
  coverage_ranges: [[1, 480]],
  mixed_detected: {},
  errors: [],
  reference_length: 1000,
  features: [],
  verdict: {
    status: 'pending',
    blocking: [
      { kind: 'unresolved_variant', ref_start: 500, ref_end: 500, element: 'GeneX',
        detail: '500 substitution（read 可信区外）', action: '核对峰图或补测' },
      { kind: 'n_in_cds', ref_start: 301, ref_end: 600, element: 'GeneX',
        detail: 'CDS 内 1 处 N/模糊调用，蛋白判定待定', action: '核对峰图或换引物补测' },
    ],
    info: [],
    roi: { start: 1, end: 1000, source: 'whole' },
    reads: { total: 1, analyzed: 1, failed: [] },
    algo_version: 'v2',
  },
  resolved_regions: [{
    ref_range: '120-120', source_read: 'r2.ab1', direction: '+',
    primer_range: '30-40', variant_count: 1,
    verifier_reads: ['r1.ab1'], verifier_quality: '验证质量充分', positions: [120],
  }],
  unresolved_sites: [{ ref_pos: 500, kind: 'n_call', reads: ['r1.ab1'], detail: 'read 调用 N（未解析）' }],
  algo_version: 'v2',
}

describe('ConclusionCards verdict 渲染（T2/M13）', () => {
  it('徽标读 verdict.status（pending → 待定）', () => {
    const w = mount(ConclusionCards, { props: { analysis: baseAnalysis } })
    const badge = w.find('.verdict-badge')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('待定')
    expect(badge.classes()).toContain('pending')
  })

  it('阻断项按 verdict.blocking 结构化列出（种类/位置/元件/动作）', () => {
    const w = mount(ConclusionCards, { props: { analysis: baseAnalysis } })
    const items = w.findAll('.verdict-blocking-item')
    expect(items.length).toBe(2)
    expect(items[0].text()).toContain('未决差异')
    expect(items[0].text()).toContain('500')
    expect(items[0].text()).toContain('GeneX')
    expect(items[0].text()).toContain('核对峰图或补测')
    expect(items[1].text()).toContain('CDS 内 N/模糊调用')
  })

  it('裁决行优先 resolved_regions 结构化渲染（不依赖文案匹配）', () => {
    const w = mount(ConclusionCards, { props: { analysis: baseAnalysis } })
    const lines = w.findAll('.resolved-region-line')
    expect(lines.length).toBe(1)
    expect(lines[0].text()).toContain('差异区间 120-120')
    expect(lines[0].text()).toContain('r2.ab1')
    expect(lines[0].text()).toContain('无需核对')
  })

  it('旧记录（无 verdict/resolved_regions/conclusion_groups）正文整段常驻', () => {
    // W12（RV 遗留清除）：分组不再按文案匹配——旧记录无结构化分组时
    // 正文整段常驻（裁决行文本不丢，只是没有结构化高亮样式）
    const legacy: SequencingAnalysis = {
      ...baseAnalysis,
      conclusion: '✓ 结论：构建与设计一致\n差异区间 120-120：r2.ab1（正向）报出 1 处差异 → 已被 r1.ab1 高质量覆盖排除，无需核对',
      verdict: undefined,
      resolved_regions: undefined,
    }
    const w = mount(ConclusionCards, { props: { analysis: legacy } })
    expect(w.find('.verdict-badge').exists()).toBe(false)
    expect(w.find('.verdict-blocking').exists()).toBe(false)
    expect(w.findAll('.resolved-region-line').length).toBe(0)
    expect(w.text()).toContain('无需核对')
    // ✓ 头行退回绿色强调
    expect(w.find('.conclusion-head').classes()).toContain('ok')
  })
})


// 2026-11-10（cf77c3a 复审 P0-1 / P0-4）：结论卡不再铺开全部 blocking；
// 验证范围按钮必须有可见的选中样式
describe('ConclusionCards 阻断项截断（P0-1）', () => {
  const many = Array.from({ length: 12 }, (_, i) => ({
    kind: 'unresolved_variant', ref_start: 100 + i, ref_end: 100 + i,
    detail: `${100 + i} substitution`, action: '核对峰图或补测',
  }))
  const withFail = [...many, { kind: 'confirmed_variant', ref_start: 4081, ref_end: 4081,
    detail: '4081 deletion（置信 high）', action: '核对', fail_impact: true }]
  const a: SequencingAnalysis = {
    ...baseAnalysis,
    verdict: { ...baseAnalysis.verdict!, status: 'fail', blocking: withFail },
    review_queue: [{ level: 'confirmed' } as never, { level: 'read' } as never],
  }

  it('最多列 5 项，确证差异排第一，其余给出队列合并数', () => {
    const w = mount(ConclusionCards, { props: { analysis: a } })
    const items = w.findAll('.verdict-blocking-item')
    expect(items.length).toBe(5)
    expect(items[0].classes()).toContain('confirmed_variant')
    const more = w.find('.verdict-blocking-more')
    expect(more.exists()).toBe(true)
    expect(more.text()).toContain('另 8 项未展开')
    expect(more.text()).toContain('复核队列已合并为 2 项')
  })

  it('不超过 5 项时不出截断行', () => {
    const w = mount(ConclusionCards, { props: { analysis: baseAnalysis } })
    expect(w.find('.verdict-blocking-more').exists()).toBe(false)
  })
})

describe('验证范围按钮样式（P0-4）', () => {
  it('全局样式定义 .scope-btn 与选中态 .scope-btn.active', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const css = fs.readFileSync(path.resolve(__dirname, '../src/style.css'), 'utf-8')
    expect(css).toMatch(/\.scope-btn\s*\{/)
    expect(css).toMatch(/\.scope-btn\.active\s*\{[^}]*background/)
  })
})
