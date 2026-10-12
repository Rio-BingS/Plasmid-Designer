/** 独立图谱页 /map：上传/粘贴/示例、错误提示、sessionStorage 暂存 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'

const mapApi = vi.hoisted(() => ({
  parseMapFile: vi.fn(), parseMapText: vi.fn(), getVectorMap: vi.fn(), getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn(),
}))
vi.mock('@/map/api', async (orig) => ({ ...(await orig<typeof import('@/map/api')>()), ...mapApi }))

import MapView from '@/views/MapView.vue'
import { MAP_SESSION_KEY, loadMapSession, saveMapSession } from '@/map/session'
import { checkMapFile, describeMapError } from '@/map/api'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

function pickFile(w: ReturnType<typeof mount>, file: File) {
  const input = w.find('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  return input.trigger('change')
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  mapApi.getEnzymeSets.mockResolvedValue([
    { id: 'common', name: '常用酶', description: '', enzymes: [] },
    { id: 'golden_gate', name: 'Golden Gate', description: '', enzymes: [] },
  ])
})

describe('/map 页面', () => {
  it('标题、上限提示；选择 GenBank 文件 → parseMapFile（带酶组/注释选项）→ 可编辑查看器，并暂存', async () => {
    mapApi.parseMapFile.mockResolvedValue(puc())
    const w = mount(MapView)
    await flushPromises()
    expect(document.title).toContain('质粒图谱')
    expect(w.find('.map-drop').text()).toContain('5 MB')
    await w.find('select[aria-label="酶组"]').setValue('golden_gate')
    await w.find('select[aria-label="自动注释"]').setValue('on')
    await pickFile(w, new File(['LOCUS x'], 'pUC19.gb'))
    await flushPromises()
    expect(mapApi.parseMapFile).toHaveBeenCalledWith(expect.any(File), { enzyme_set: 'golden_gate', annotate: true })
    expect(w.find('.mv').exists()).toBe(true)
    expect(w.find('.mv-edit-tools').exists()).toBe(true)
    expect(loadMapSession()?.name).toBe('pUC19')
  })

  it('拖放文件同样打开', async () => {
    mapApi.parseMapFile.mockResolvedValue(puc())
    const w = mount(MapView)
    await flushPromises()
    await w.find('.map-drop').trigger('drop', { dataTransfer: { files: [new File(['>a\nACGT'], 'a.fasta')] } })
    await flushPromises()
    expect(mapApi.parseMapFile).toHaveBeenCalled()
  })

  it('.txt 文件读出文本按粘贴解析', async () => {
    mapApi.parseMapText.mockResolvedValue(puc())
    const w = mount(MapView)
    await flushPromises()
    await pickFile(w, new File(['ACGTACGTAC'], 'my seq.txt'))
    await flushPromises()
    expect(mapApi.parseMapFile).not.toHaveBeenCalled()
    expect(mapApi.parseMapText).toHaveBeenCalledWith('ACGTACGTAC', { name: 'my seq' }, { enzyme_set: 'common' })
  })

  it('本地预检：不支持的扩展名、超过上限', async () => {
    const w = mount(MapView)
    await flushPromises()
    await pickFile(w, new File(['x'], 'a.docx'))
    expect(w.find('.map-error').text()).toContain('不支持的文件格式')
    const big = new File(['x'], 'big.gb')
    Object.defineProperty(big, 'size', { value: 6 * 1024 * 1024 })
    await pickFile(w, big)
    expect(w.find('.map-error').text()).toContain('文件过大（上限 5 MB）')
    expect(mapApi.parseMapFile).not.toHaveBeenCalled()
  })

  it.each([
    [413, undefined, '文件过大'],
    [415, 'Unsupported', '不支持的文件格式'],
    [422, '序列含非法字符：X', '序列无法解析：序列含非法字符：X'],
  ])('后端 %i → 中文错误', async (status, detail, text) => {
    mapApi.parseMapFile.mockRejectedValue({ response: { status, data: { detail } } })
    const w = mount(MapView)
    await flushPromises()
    await pickFile(w, new File(['x'], 'a.gb'))
    await flushPromises()
    expect(w.find('.map-error').text()).toContain(text)
    expect(w.find('.mv').exists()).toBe(false)
  })

  it('粘贴序列：名称 + 拓扑；GenBank 文本不覆盖拓扑', async () => {
    mapApi.parseMapText.mockResolvedValue(puc())
    const w = mount(MapView)
    await flushPromises()
    await w.findAll('.map-tab')[1].trigger('click')
    await w.find('input[aria-label="序列名称"]').setValue('frag')
    await w.find('input[value="linear"]').setValue(true)
    await w.find('textarea').setValue('ACGTACGT')
    await w.find('.map-open').trigger('click')
    await flushPromises()
    expect(mapApi.parseMapText).toHaveBeenCalledWith('ACGTACGT', { name: 'frag', topology: 'linear' }, { enzyme_set: 'common' })

    await w.find('.map-head-actions button').trigger('click') // 打开其他序列
    await w.findAll('.map-tab')[1].trigger('click')
    await w.find('textarea').setValue('LOCUS  x  10 bp DNA circular\nORIGIN\n//')
    await w.find('.map-open').trigger('click')
    await flushPromises()
    expect(mapApi.parseMapText.mock.calls[1][1]).toEqual({ name: 'frag' })
  })

  it('示例按钮载入 pUC19', async () => {
    mapApi.getVectorMap.mockResolvedValue(puc())
    const w = mount(MapView)
    await flushPromises()
    await w.find('.map-example').trigger('click')
    await flushPromises()
    expect(mapApi.getVectorMap).toHaveBeenCalledWith('pUC19', { enzyme_set: 'common' })
    expect(w.find('.cmap-svg').exists()).toBe(true)
  })

  it('刷新后从 sessionStorage 恢复；编辑写回暂存；关闭清除', async () => {
    saveMapSession(puc())
    const w = mount(MapView)
    await flushPromises()
    expect(w.find('.mv').exists()).toBe(true)
    expect(w.find('.map-input').exists()).toBe(false)
    await w.findAll('.mv-feature-list .mv-row')[0].trigger('click')
    await w.find('.mv-delete').trigger('click')
    await flushPromises()
    expect(loadMapSession()!.features).toHaveLength(puc().features.length - 1)
    await w.find('.map-close').trigger('click')
    expect(sessionStorage.getItem(MAP_SESSION_KEY)).toBeNull()
    expect(w.find('.map-input').exists()).toBe(true)
  })
})

describe('辅助函数', () => {
  it('checkMapFile / describeMapError', () => {
    expect(checkMapFile({ name: 'a.GBK', size: 10 })).toBeNull()
    expect(checkMapFile({ name: 'a.dna', size: 0 })).toBe('文件为空')
    expect(describeMapError({ response: { status: 413, data: { detail: '文件过大（上限 5 MB），请截取后再打开' } } })).toContain('上限 5 MB')
    expect(describeMapError({ response: { status: 422, data: { detail: [{ msg: 'x' }] } } })).toContain('序列无法解析')
    expect(describeMapError({ response: { status: 500, data: { detail: '服务器错误' } } })).toBe('服务器错误')
  })

  it('session：坏数据与非契约文档不恢复', () => {
    sessionStorage.setItem(MAP_SESSION_KEY, '{bad')
    expect(loadMapSession()).toBeNull()
    sessionStorage.setItem(MAP_SESSION_KEY, JSON.stringify({ schema: 'other' }))
    expect(loadMapSession()).toBeNull()
    const full = { setItem: () => { throw new Error('QuotaExceeded') } } as unknown as Storage
    expect(saveMapSession(puc(), full)).toBe(false)
  })
})
