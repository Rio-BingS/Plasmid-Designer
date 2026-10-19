import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import AnalysisView from '@/views/AnalysisView.vue'
import { cleanSequence, parseNamedSequence } from '@/utils/sequence'

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

import { analyzeSequence, exportSequence, getEnzymes, getExportFormats } from '@/api'

const CDS = 'ATGGCTGCTAAA' + 'T'.repeat(20) + 'GGATCC'
const NAMED = '>PNPM5 小质粒目标基因\n' + 'ATGGCTGCT\r\nAAA' + 'T'.repeat(20) + '\nGGATCC\n'

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

function button(wrapper: VueWrapper, label: string) {
  const btn = wrapper.findAll('button').find(b => b.text().includes(label))
  expect(btn, `应有按钮 ${label}`).toBeTruthy()
  return btn!
}

describe('parseNamedSequence', () => {
  it('取 FASTA 头的第一个词作名称，碱基去空白', () => {
    expect(parseNamedSequence(NAMED)).toEqual({ name: 'PNPM5', sequence: CDS })
  })

  it('没有 FASTA 头时名称为空，序列与 cleanSequence 一致', () => {
    expect(parseNamedSequence('  ATG\nGCT  ')).toEqual({ name: '', sequence: 'ATGGCT' })
    expect(parseNamedSequence('').name).toBe('')
  })

  it('cleanSequence 丢掉头行（头行文本不能被当碱基提交）', () => {
    expect(cleanSequence(NAMED)).toBe(CDS)
    expect(cleanSequence('>only header\nATG')).toBe('ATG')
  })
})

describe('AnalysisView 识别带名称的 CDS', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getExportFormats).mockResolvedValue([])
    vi.mocked(getEnzymes).mockResolvedValue([])
    vi.mocked(analyzeSequence).mockResolvedValue({
      sequence_length: CDS.length, gc_content: 40, orfs: [], enzymes: [],
    } as any)
  })

  it('头行名称显示在输入区，碱基长度按去头后的序列算', async () => {
    const wrapper = await mountWith(NAMED)
    expect(wrapper.find('.seq-name').text()).toContain('PNPM5')
    expect(wrapper.find('.seq-length').text()).toBe(`${CDS.length} bp`)
    wrapper.unmount()
  })

  it('分析仍提交纯碱基（头行不参与）', async () => {
    const wrapper = await mountWith(NAMED)
    await button(wrapper, '综合分析').trigger('click')
    await flushPromises()
    expect(vi.mocked(analyzeSequence)).toHaveBeenCalledWith(CDS, 'dna')
    wrapper.unmount()
  })

  it('导出用 CDS 名称作文件名，没有名称时退回 analysis_result', async () => {
    vi.mocked(exportSequence).mockResolvedValue(undefined as any)
    const named = await mountWith(NAMED)
    await button(named, '导出').trigger('click')
    await flushPromises()
    expect(vi.mocked(exportSequence)).toHaveBeenCalledWith(CDS, [], 'genbank', 'PNPM5')
    named.unmount()

    const plain = await mountWith(CDS)
    await button(plain, '导出').trigger('click')
    await flushPromises()
    expect(vi.mocked(exportSequence)).toHaveBeenLastCalledWith(CDS, [], 'genbank', 'analysis_result')
    plain.unmount()
  })
})
