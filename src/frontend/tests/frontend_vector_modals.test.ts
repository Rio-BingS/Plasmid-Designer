/**
 * 载体库三个 modal 组件的最小行为测试（从 VectorsView 拆出后的契约锁）：
 *
 * - VectorUploadModal：defineModel 开关 + 上传成功/失败消息 + uploaded 事件
 * - VectorBatchImportModal：imported 事件 + 结果消息（成功/失败）
 * - VectorEditModal：watch(vector) 回填表单（数组字段拼接、保存拆分）+ saved 事件
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

vi.mock('@/api', () => ({
  uploadVectorFile: vi.fn(),
  batchImportVectors: vi.fn(),
  updateVector: vi.fn(),
}))

import { uploadVectorFile, batchImportVectors, updateVector } from '@/api'
import VectorUploadModal from '@/components/VectorUploadModal.vue'
import VectorBatchImportModal from '@/components/VectorBatchImportModal.vue'
import VectorEditModal from '@/components/VectorEditModal.vue'

const mockedUpload = vi.mocked(uploadVectorFile)
const mockedBatch = vi.mocked(batchImportVectors)
const mockedUpdate = vi.mocked(updateVector)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('VectorUploadModal', () => {
  it('show 为 defineModel：遮罩点击关闭（父级 v-model 语义）', async () => {
    const w = mount(VectorUploadModal, { props: { modelValue: true } })
    expect(w.find('.modal').exists()).toBe(true)
    await w.find('.modal-overlay').trigger('click')
    expect(w.emitted('update:modelValue')?.[0]).toEqual([false])
  })

  it('上传成功：显示导入结果并发出 uploaded', async () => {
    mockedUpload.mockResolvedValue({
      vector: { name: 'pET-28a', length: 5368, features: 12 },
    } as any)
    const w = mount(VectorUploadModal, { props: { modelValue: true } })
    const file = new File(['LOCUS'], 'pet.gb', { type: 'text/plain' })
    Object.defineProperty(w.find('input[type="file"]').element, 'files', { value: [file] })
    await w.find('input[type="file"]').trigger('change')
    await w.find('.modal-footer .btn-primary').trigger('click')
    await flushPromises()
    expect(mockedUpload).toHaveBeenCalledWith(file)
    expect(w.text()).toContain('导入成功: pET-28a (5368 bp, 12 个特征)')
    expect(w.emitted('uploaded')).toHaveLength(1)
  })

  it('上传失败：消息带原因，不发 uploaded', async () => {
    mockedUpload.mockRejectedValue({ response: { data: { detail: '格式错误' } } })
    const w = mount(VectorUploadModal, { props: { modelValue: true } })
    const file = new File(['x'], 'x.gb', { type: 'text/plain' })
    Object.defineProperty(w.find('input[type="file"]').element, 'files', { value: [file] })
    await w.find('input[type="file"]').trigger('change')
    await w.find('.modal-footer .btn-primary').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('导入失败: 格式错误')
    expect(w.emitted('uploaded')).toBeUndefined()
  })
})

describe('VectorBatchImportModal', () => {
  it('批量导入成功：消息含数量并发出 imported', async () => {
    mockedBatch.mockResolvedValue({ ncbi: [{}, {}], errors: [] } as any)
    const w = mount(VectorBatchImportModal, { props: { modelValue: true } })
    await w.find('textarea').setValue('JX185437.1\nHQ670549.1')
    await w.find('.modal-footer .btn-primary').trigger('click')
    await flushPromises()
    expect(mockedBatch).toHaveBeenCalledWith(['JX185437.1', 'HQ670549.1'])
    expect(w.text()).toContain('成功导入 2 个载体')
    expect(w.emitted('imported')).toHaveLength(1)
  })
})

describe('VectorEditModal', () => {
  const baseVector = {
    id: 'v1', name: 'pET-28a', description: '表达载体', vector_type: 'expression',
    host: ['E. coli', 'B. subtilis'], antibiotic_resistance: ['Kan'], copy_number: 'high',
  } as any

  it('watch(vector) 回填表单：数组字段拼接为逗号串', async () => {
    const w = mount(VectorEditModal, {
      props: { modelValue: true, vector: null },
      global: { stubs: { transition: false } },
    })
    await w.setProps({ vector: baseVector })
    await nextTick()
    const inputs = w.findAll('input, select')
    const values = inputs.map(i => (i.element as HTMLInputElement).value)
    expect(values).toContain('pET-28a')
    expect(values).toContain('E. coli, B. subtilis')
    expect(values).toContain('Kan')
  })

  it('保存成功：数组字段按逗号拆分回传，关闭弹窗并发 saved', async () => {
    mockedUpdate.mockResolvedValue({} as any)
    const w = mount(VectorEditModal, { props: { modelValue: true, vector: baseVector } })
    await nextTick()
    await w.find('.modal-footer .btn-primary').trigger('click')
    await flushPromises()
    expect(mockedUpdate).toHaveBeenCalledWith('v1', expect.objectContaining({
      host: ['E. coli', 'B. subtilis'],
      antibiotic_resistance: ['Kan'],
      copy_number: 'high',
    }))
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([false])
    expect(w.emitted('saved')).toHaveLength(1)
  })
})
