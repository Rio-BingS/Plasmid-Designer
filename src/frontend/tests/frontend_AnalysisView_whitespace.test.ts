import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import AnalysisView from '@/views/AnalysisView.vue'
import { cleanSequence } from '@/utils/sequence'

vi.mock('@/api', () => ({
  analyzeSequence: vi.fn(),
  findRestrictionSites: vi.fn(),
  predictORFs: vi.fn(),
  analyzeGC: vi.fn(),
  exportSequence: vi.fn(),
  getExportFormats: vi.fn(),
  checkCompatibility: vi.fn(),
  getEnzymes: vi.fn(),
  simulateDigest: vi.fn(),
}))

import {
  analyzeSequence, analyzeGC, checkCompatibility, exportSequence, findRestrictionSites,
  getEnzymes, getExportFormats, predictORFs,
} from '@/api'

// 审查 06 M2：此前只有酶切消化去空白，限制性位点 / ORF / GC 等仍把粘贴的
// 换行原样提交，位点跨行漏检、ORF 坐标偏移、GC 被稀释
const FLAT = 'AAAAGAATTC' + 'T'.repeat(20) + 'GGATCC'
const PASTED = '  AAAAGAA\r\nTTC' + 'T'.repeat(20) + '\n\tGGATCC \n'

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/analysis', component: AnalysisView }],
})
const mounted: VueWrapper[] = []

async function mountWith(seq: string) {
  const wrapper = mount(AnalysisView, { global: { plugins: [router] }, attachTo: document.body })
  mounted.push(wrapper)
  await flushPromises()
  await wrapper.find('textarea').setValue(seq)
  return wrapper
}

async function click(wrapper: VueWrapper, label: string) {
  const btn = wrapper.findAll('button').find(b => b.text().includes(label))
  expect(btn, `应有按钮 ${label}`).toBeTruthy()
  await btn!.trigger('click')
  await flushPromises()
}

describe('cleanSequence', () => {
  it('去掉空格、换行、回车、制表符', () => {
    expect(cleanSequence(PASTED)).toBe(FLAT)
    expect(cleanSequence('')).toBe('')
  })
})

describe('AnalysisView 各分析按钮统一去空白后提交', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    vi.mocked(getExportFormats).mockResolvedValue([])
    vi.mocked(getEnzymes).mockResolvedValue({ enzymes: {} })
    vi.mocked(findRestrictionSites).mockResolvedValue({ total: 0, sites: [], unique_sites: [] })
    vi.mocked(predictORFs).mockResolvedValue({ total: 0, orfs: [] })
    vi.mocked(analyzeGC).mockResolvedValue({
      total_gc_content: 50, total_regions: 0, extreme_regions: 0, distribution: [], extremes: [],
    })
    vi.mocked(analyzeSequence).mockResolvedValue({
      sequence_length: FLAT.length, gc_content: 50, coding_potential: 0,
      restriction_sites: [], orfs: [], gc_distribution: [], warnings: [],
    })
    vi.mocked(checkCompatibility).mockResolvedValue({ compatible: true, issues: [], recommendations: [] })
    vi.mocked(exportSequence).mockResolvedValue(undefined)
    await router.push('/analysis')
    await router.isReady()
  })

  afterEach(() => {
    while (mounted.length) mounted.pop()?.unmount()
  })

  it('限制性位点', async () => {
    const w = await mountWith(PASTED)
    await click(w, '限制性位点')
    expect(findRestrictionSites).toHaveBeenCalledWith(FLAT, undefined)
  })

  it('ORF 预测', async () => {
    const w = await mountWith(PASTED)
    await click(w, 'ORF 预测')
    expect(vi.mocked(predictORFs).mock.calls[0][0]).toBe(FLAT)
  })

  it('GC 分析', async () => {
    const w = await mountWith(PASTED)
    await click(w, 'GC 分析')
    expect(vi.mocked(analyzeGC).mock.calls[0][0]).toBe(FLAT)
  })

  it('综合分析', async () => {
    const w = await mountWith(PASTED)
    await click(w, '综合分析')
    expect(vi.mocked(analyzeSequence).mock.calls[0][0]).toBe(FLAT)
  })

  it('兼容性检查两条序列与导出', async () => {
    const w = await mountWith(PASTED)
    const areas = w.findAll('.compatibility-section textarea')
    expect(areas).toHaveLength(2)
    await areas[0].setValue('GAA\nTTC ')
    await areas[1].setValue(' AAG\r\nCTT')
    await click(w, '检查兼容性')
    const call = vi.mocked(checkCompatibility).mock.calls[0]
    expect([call[0], call[1]]).toEqual(['GAATTC', 'AAGCTT'])

    await click(w, '导出')
    expect(vi.mocked(exportSequence).mock.calls[0][0]).toBe(FLAT)
  })

  it('长度提示与提交口径一致', async () => {
    const w = await mountWith(PASTED)
    expect(w.find('.seq-length').text()).toBe(`${FLAT.length} bp`)
  })
})
