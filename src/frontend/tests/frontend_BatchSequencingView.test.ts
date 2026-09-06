import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import BatchSequencingView from '@/views/BatchSequencingView.vue'
import type { SequencingBatchResult } from '@/api'

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock })
}))

vi.mock('@/api', () => ({
  analyzeSequencingBatch: vi.fn()
}))

import { analyzeSequencingBatch } from '@/api'

const mockResult: SequencingBatchResult = {
  batch_id: 'seqbatch_test1',
  created_at: '2026-09-07T12:00:00',
  min_q: 20,
  excel_mode: true,
  items: [
    {
      plasmid: 'MX', status: 'analyzed', conclusion: '合格：与设计一致',
      analysis_id: 'seq_abc123', reference_name: 'MX.fasta', reference_length: 7039,
      read_count: 3, variant_count: 1, pending_count: 2, coverage_percent: 27.4
    },
    {
      plasmid: 'P2', status: 'no_reference', conclusion: '已整理 1 个测序文件，但缺少同名参考图谱（.dna/.gb/.fasta），无法自动比对',
      analysis_id: null, reference_name: null, reference_length: null,
      read_count: 1, variant_count: 0, pending_count: 0, coverage_percent: null
    }
  ],
  unmatched: [{ filename: '孤儿.ab1', reason: '未出现在任何质粒的引物列中' }],
  ignored_files: ['说明.txt']
}

function makeFile(name: string, type = 'application/octet-stream'): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type })
}

function mountView() {
  return mount(BatchSequencingView, {
    global: {
      stubs: {
        'router-link': { template: '<a><slot /></a>', props: ['to'] }
      }
    }
  })
}

describe('BatchSequencingView', () => {
  beforeEach(() => {
    vi.mocked(analyzeSequencingBatch).mockReset()
    pushMock.mockReset()
  })

  it('空文件时分析按钮禁用，选文件后可用', async () => {
    const w = mountView()
    const btn = w.find('button.analyze-btn')
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
    // 模拟文件入库：直接调用组件暴露的 addFiles 不可行，走 input 事件
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [makeFile('T1.ab1')] })
    await input.trigger('change')
    expect((w.find('button.analyze-btn').element as HTMLButtonElement).disabled).toBe(false)
  })

  it('分析后渲染结论表：徽章/覆盖/待复核/一句话结论', async () => {
    vi.mocked(analyzeSequencingBatch).mockResolvedValue(mockResult)
    const w = mountView()
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [makeFile('T1.ab1'), makeFile('MX.fasta')] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    expect(analyzeSequencingBatch).toHaveBeenCalledTimes(1)
    const rows = w.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    const badges = rows[0].findAll('.badge').map((b) => b.text())
    expect(badges).toContain('合格')
    expect(rows[0].text()).toContain('合格：与设计一致')
    expect(rows[0].text()).toContain('27.4%')
    expect(rows[0].text()).toContain('+2 待复核')
    expect(rows[1].text()).toContain('缺图谱')
    expect(rows[1].text()).toContain('缺少同名参考图谱')
    expect(w.text()).toContain('未匹配文件（1 个，未参与分析）')
  })

  it('查看详情深链到 /sequencing?history=<analysis_id>，无 id 的行无按钮', async () => {
    vi.mocked(analyzeSequencingBatch).mockResolvedValue(mockResult)
    const w = mountView()
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [makeFile('T1.ab1')] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    const btns = w.findAll('button.mini-btn')
    expect(btns).toHaveLength(1) // 只有已分析的行有详情按钮
    await btns[0].trigger('click')
    expect(pushMock).toHaveBeenCalledWith({ path: '/sequencing', query: { history: 'seq_abc123' } })
  })

  it('上传信息表后随请求一起提交', async () => {
    vi.mocked(analyzeSequencingBatch).mockResolvedValue(mockResult)
    const w = mountView()
    const filesInput = w.find('input[type="file"][multiple]')
    Object.defineProperty(filesInput.element, 'files', { value: [makeFile('T1.ab1')] })
    await filesInput.trigger('change')
    const excelInput = w.find('input[accept=".xlsx"]')
    Object.defineProperty(excelInput.element, 'files', { value: [makeFile('测序.xlsx')] })
    await excelInput.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    const form = vi.mocked(analyzeSequencingBatch).mock.calls[0]
    expect(form[0].map((f: File) => f.name)).toEqual(['T1.ab1'])
    expect(form[1]?.name).toBe('测序.xlsx')
  })
})
