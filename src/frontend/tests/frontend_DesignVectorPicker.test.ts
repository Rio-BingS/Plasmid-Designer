/**
 * 设计页一次性载体选择器（本地上传 / 在线导入）
 *
 * 缺陷背景：设计页只能从载体库选载体，而载体库写入口只对管理员开放，
 * 普通用户手里的 .dna/.gb 或在线来源的质粒完全用不上。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import DesignVectorPicker from '@/components/DesignVectorPicker.vue'

// 登录/管理员状态可控（真实实现依赖 pinia，这里只关心「登录后才出现 SnapGene」）
let loggedIn = false
let admin = false
vi.mock('@/composables/useIsAdmin', async () => {
  const { computed } = await import('vue')
  return {
    useIsAdmin: () => computed(() => admin),
    useIsAuthenticated: () => computed(() => loggedIn)
  }
})

vi.mock('@/api', async (importOriginal) => ({
  uploadDesignVector: vi.fn(),
  importDesignVectorOnline: vi.fn(),
  searchDesignVectorSnapgene: vi.fn(),
  getDesignVectorSources: vi.fn(),
  formatApiError: ((await importOriginal()) as typeof import('@/api')).formatApiError
}))

import {
  uploadDesignVector, importDesignVectorOnline, searchDesignVectorSnapgene,
  getDesignVectorSources
} from '@/api'

const PAYLOAD = {
  id: 'adhoc_upload_pTEST', name: 'pTEST', source: 'genbank', url: '', license: '',
  sequence: 'ATGC'.repeat(150), topology: 'circular' as const, length: 600,
  features: [{ name: 'bla', type: 'CDS', start: 10, end: 90, strand: '+' as const }],
  mcs: { start: 158, end: 158, origin: 'inferred' as const, enzymes: ['BamHI'] },
  unique_enzymes: ['BamHI', 'EcoRI'],
  warnings: ['载体未标注 MCS：已按唯一位点最密集区（158 bp 附近，1 个唯一位点）取插入点']
}

function mountPicker(mode: 'upload' | 'online' = 'upload', modelValue: any = null) {
  return mount(DesignVectorPicker, { props: { mode, modelValue } })
}

describe('DesignVectorPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    loggedIn = false
    admin = false
    vi.mocked(getDesignVectorSources).mockResolvedValue({
      enabled: true, snapgene_online_enabled: true, disclaimer: '',
      sources: [{ id: 'snapgene', name: 'SnapGene 在线质粒库', kind: 'online_library', url: '', license: 'L', importable: true, note: '' }]
    } as any)
  })

  it('选择文件后把解析结果交给父组件', async () => {
    vi.mocked(uploadDesignVector).mockResolvedValue(PAYLOAD as any)
    const wrapper = mountPicker('upload')
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [new File(['x'], 'pTEST.gb')], configurable: true
    })
    await input.trigger('change')
    await flushPromises()
    expect(uploadDesignVector).toHaveBeenCalledTimes(1)
    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted && emitted[0][0]).toEqual(PAYLOAD)
  })

  it('上传失败时展示后端文案且不提交载体', async () => {
    vi.mocked(uploadDesignVector).mockRejectedValue({
      response: { status: 415, data: { detail: '不支持的文件格式：请上传 GenBank' } }
    })
    const wrapper = mountPicker('upload')
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [new File(['x'], 'a.txt')], configurable: true
    })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('不支持的文件格式')
    expect(wrapper.emitted('update:modelValue')).toBeFalsy()
  })

  it('在线导出的 NCBI accession 会去掉首尾空白后提交', async () => {
    vi.mocked(importDesignVectorOnline).mockResolvedValue(PAYLOAD as any)
    const wrapper = mountPicker('online')
    await flushPromises()
    const vm = wrapper.vm as any
    vm.ncbiAccession = '  L09137 '
    await vm.importOnline()
    await flushPromises()
    expect(importDesignVectorOnline).toHaveBeenCalledWith({ source: 'ncbi', accession: 'L09137' })
  })

  it('未登录时不显示 SnapGene 页签（后端仍会 401）', async () => {
    const wrapper = mountPicker('online')
    await flushPromises()
    expect(wrapper.text()).not.toContain('SnapGene 在线库')
    expect(wrapper.text()).toContain('NCBI')
    expect(wrapper.text()).toContain('Ensembl')
  })

  it('登录后出现 SnapGene 页签，搜索结果按文本渲染（不解析 HTML）', async () => {
    loggedIn = true
    vi.mocked(searchDesignVectorSnapgene).mockResolvedValue({
      query: 'pET', count: 1,
      plasmids: [{ plasmid_id: 'x<1>', name: '<img src=x>', set_name: 'set' }]
    } as any)
    const wrapper = mountPicker('online')
    await flushPromises()
    const vm = wrapper.vm as any
    expect(wrapper.text()).toContain('SnapGene 在线库')
    vm.onlineSource = 'snapgene'
    await flushPromises()
    vm.sgQuery = 'pET'
    await vm.searchSnapgene()
    await flushPromises()
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('<img src=x>')
  })

  it('已选载体时展示摘要并可清除', async () => {
    const wrapper = mountPicker('upload', PAYLOAD)
    expect(wrapper.text()).toContain('pTEST')
    expect(wrapper.text()).toContain('600 bp')
    expect(wrapper.text()).toContain('推测插入点')
    await wrapper.find('.dvp-link').trigger('click')
    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted && emitted[0][0]).toBeNull()
  })
})
