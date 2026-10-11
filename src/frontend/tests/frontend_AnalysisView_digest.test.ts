import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import AnalysisView from '@/views/AnalysisView.vue'

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

import { findRestrictionSites, getEnzymes, getExportFormats, simulateDigest } from '@/api'

// EcoRI(GAATTC) @5-10 与 BamHI(GGATCC) @31-36，共 46 bp
const SEQ = 'AAAA' + 'GAATTC' + 'T'.repeat(20) + 'GGATCC' + 'C'.repeat(10)

// 与后端 POST /analysis/digest 对该序列的真实返回一致
const DOUBLE_DIGEST = {
  total_fragments: 3,
  fragments: [
    { start: 1, end: 5, size: 5, cut_by: ['EcoRI'] },
    { start: 6, end: 31, size: 26, cut_by: ['BamHI'] },
    { start: 32, end: 46, size: 15, cut_by: [] },
  ],
  enzymes_with_sites: ['EcoRI', 'BamHI'],
}

const ENZYMES = {
  EcoRI: { recognition_sequence: 'GAATTC' },
  BamHI: { recognition_sequence: 'GGATCC' },
  HindIII: { recognition_sequence: 'AAGCTT' },
  NotI: { recognition_sequence: 'GCGGCCGC' },
  XhoI: { recognition_sequence: 'CTCGAG' },
  NdeI: { recognition_sequence: 'CATATG' },
  SalI: { recognition_sequence: 'GTCGAC' },
}

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/analysis', component: AnalysisView }],
})

const mounted: VueWrapper[] = []

async function mountWithRestriction(seq = SEQ) {
  const wrapper = mount(AnalysisView, { global: { plugins: [router] }, attachTo: document.body })
  mounted.push(wrapper)
  await flushPromises()
  await wrapper.find('textarea').setValue(seq)
  // 消化模拟区位于限制性位点结果卡片内，先跑一次位点分析
  const btn = wrapper.findAll('button').find(b => b.text().includes('限制性位点'))!
  await btn.trigger('click')
  await flushPromises()
  return wrapper
}

function digestSection(wrapper: VueWrapper) {
  return wrapper.find('.digest-sim')
}

async function pickEnzyme(wrapper: VueWrapper, query: string, name: string) {
  const input = digestSection(wrapper).find('input.es-input')
  await input.trigger('focus')
  await input.setValue(query)
  const option = digestSection(wrapper).findAll('li.es-option').find(li => li.text().startsWith(name))
  expect(option, `下拉中应有 ${name}`).toBeTruthy()
  await option!.trigger('mousedown')
}

function digestButton(wrapper: VueWrapper) {
  return digestSection(wrapper).findAll('button').find(b => b.text().includes('模拟'))!
}

describe('AnalysisView 双酶切模拟', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    vi.mocked(getExportFormats).mockResolvedValue([])
    vi.mocked(getEnzymes).mockResolvedValue({ enzymes: ENZYMES })
    vi.mocked(findRestrictionSites).mockResolvedValue({ sites: [], unique_sites: [] })
    await router.push('/analysis')
    await router.isReady()
  })

  afterEach(() => {
    while (mounted.length) mounted.pop()?.unmount()
  })

  it('选两种酶 → 提交 → 渲染三条片段（电泳条带）', async () => {
    vi.mocked(simulateDigest).mockResolvedValue(DOUBLE_DIGEST)
    const wrapper = await mountWithRestriction()

    expect(digestButton(wrapper).attributes('disabled')).toBeDefined()
    await pickEnzyme(wrapper, 'eco', 'EcoRI')
    await pickEnzyme(wrapper, 'GGATCC', 'BamHI') // 按识别序列也能搜到

    const chips = digestSection(wrapper).findAll('.es-chip').map(c => c.text().replace('×', '').trim())
    expect(chips).toEqual(['EcoRI', 'BamHI'])
    expect(digestButton(wrapper).attributes('disabled')).toBeUndefined()

    await digestButton(wrapper).trigger('click')
    await flushPromises()

    expect(simulateDigest).toHaveBeenCalledTimes(1)
    expect(simulateDigest).toHaveBeenCalledWith(SEQ, ['EcoRI', 'BamHI'], false)

    const rows = digestSection(wrapper).findAll('tbody tr')
    expect(rows).toHaveLength(3)
    const cells = rows.map(r => r.findAll('td').map(td => td.text()))
    expect(cells[0]).toEqual(['#1', '1', '5', '5', 'EcoRI'])
    expect(cells[1]).toEqual(['#2', '6', '31', '26', 'BamHI'])
    expect(cells[2]).toEqual(['#3', '32', '46', '15', '—'])
    // 片段大小之和 = 序列长度
    const total = cells.reduce((n, c) => n + Number(c[3]), 0)
    expect(total).toBe(SEQ.length)
    expect(wrapper.find('.error-msg').exists()).toBe(false)
  })

  it('粘贴的多行序列去掉空白后再提交，避免空白计入片段长度、跨行位点漏切', async () => {
    vi.mocked(simulateDigest).mockResolvedValue(DOUBLE_DIGEST)
    // EcoRI 位点被换行拆开（GAA\nTTC），另带首尾空格
    const pasted = '  AAAAGAA\nTTC' + 'T'.repeat(20) + '\nGGATCC' + 'C'.repeat(10) + ' \n'
    const wrapper = await mountWithRestriction(pasted)
    await pickEnzyme(wrapper, 'Eco', 'EcoRI')
    await pickEnzyme(wrapper, 'Bam', 'BamHI')
    await digestButton(wrapper).trigger('click')
    await flushPromises()

    expect(simulateDigest).toHaveBeenCalledWith(SEQ, ['EcoRI', 'BamHI'], false)
  })

  it('勾选环状 → 以 circular=true 提交，跨原点片段带标注', async () => {
    vi.mocked(simulateDigest).mockResolvedValue({
      total_fragments: 2,
      fragments: [
        { start: 6, end: 31, size: 26, cut_by: ['BamHI'] },
        { start: 32, end: 5, size: 20, cut_by: ['EcoRI'], wraps_origin: true },
      ],
      enzymes_with_sites: ['EcoRI', 'BamHI'],
      circular: true,
    })
    const wrapper = await mountWithRestriction()
    const box = digestSection(wrapper).find('input.digest-circular-input')
    expect((box.element as HTMLInputElement).checked).toBe(false) // 默认线性
    await box.setValue(true)
    await pickEnzyme(wrapper, 'Eco', 'EcoRI')
    await pickEnzyme(wrapper, 'Bam', 'BamHI')
    await digestButton(wrapper).trigger('click')
    await flushPromises()

    expect(simulateDigest).toHaveBeenCalledWith(SEQ, ['EcoRI', 'BamHI'], true)
    const rows = digestSection(wrapper).findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[1].text()).toContain('跨原点')
    expect(rows[0].text()).not.toContain('跨原点')
    expect(digestSection(wrapper).text()).toContain('环状完全消化')
  })

  it('所选酶在序列中无位点时显示后端提示', async () => {
    vi.mocked(simulateDigest).mockResolvedValue({
      total_fragments: 0, fragments: [], enzymes_with_sites: [],
      message: '所选酶在序列中均无切割位点',
    })
    const wrapper = await mountWithRestriction()
    await pickEnzyme(wrapper, 'Hind', 'HindIII')
    await pickEnzyme(wrapper, 'Not', 'NotI')
    await digestButton(wrapper).trigger('click')
    await flushPromises()

    expect(digestSection(wrapper).find('table').exists()).toBe(false)
    expect(digestSection(wrapper).text()).toContain('所选酶在序列中均无切割位点')
  })

  it('超过 6 个酶时禁用提交并提示，不发请求', async () => {
    const wrapper = await mountWithRestriction()
    for (const name of Object.keys(ENZYMES)) await pickEnzyme(wrapper, name, name)

    expect(digestSection(wrapper).findAll('.es-chip')).toHaveLength(7)
    expect(digestButton(wrapper).attributes('disabled')).toBeDefined()
    expect(digestSection(wrapper).text()).toContain('最多选择 6 个酶')
    await digestButton(wrapper).trigger('click')
    expect(simulateDigest).not.toHaveBeenCalled()
  })

  it('请求失败：422 校验错误显示可读文字，且不残留上一次的片段表', async () => {
    vi.mocked(simulateDigest).mockResolvedValueOnce(DOUBLE_DIGEST)
    const wrapper = await mountWithRestriction()
    await pickEnzyme(wrapper, 'Eco', 'EcoRI')
    await pickEnzyme(wrapper, 'Bam', 'BamHI')
    await digestButton(wrapper).trigger('click')
    await flushPromises()
    expect(digestSection(wrapper).findAll('tbody tr')).toHaveLength(3)

    vi.mocked(simulateDigest).mockRejectedValueOnce({
      response: { status: 422, data: { detail: [{ msg: 'List should have at most 6 items' }] } },
    })
    await digestButton(wrapper).trigger('click')
    await flushPromises()

    const err = wrapper.find('.error-msg')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('List should have at most 6 items')
    expect(err.text()).not.toContain('[object Object]')
    expect(digestSection(wrapper).find('table').exists()).toBe(false)
  })
})
