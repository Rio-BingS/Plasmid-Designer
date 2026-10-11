import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import BatchSequencingView from '@/views/BatchSequencingView.vue'
import type { SequencingBatchResult } from '@/api'
import { UploadCancelledError, type ChunkedProgress } from '@/utils/chunkedUpload'
import { useAuthStore } from '@/stores/auth'

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock })
}))

vi.mock('@/api', async (importOriginal) => ({
  analyzeSequencingBatchChunked: vi.fn(),
  downloadBatchSequencingReport: vi.fn(),
  // 错误文案走真实实现：界面展示的就是后端 detail 原文
  formatApiError: ((await importOriginal()) as typeof import('@/api')).formatApiError
}))

import { analyzeSequencingBatchChunked, downloadBatchSequencingReport } from '@/api'

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
    setActivePinia(createPinia()) // 页面读取 auth store 里的本账号 read 上限
    vi.mocked(analyzeSequencingBatchChunked).mockReset()
    vi.mocked(downloadBatchSequencingReport).mockReset()
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
    vi.mocked(analyzeSequencingBatchChunked).mockResolvedValue(mockResult)
    const w = mountView()
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [makeFile('T1.ab1'), makeFile('MX.fasta')] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    expect(analyzeSequencingBatchChunked).toHaveBeenCalledTimes(1)
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
    vi.mocked(analyzeSequencingBatchChunked).mockResolvedValue(mockResult)
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
    vi.mocked(analyzeSequencingBatchChunked).mockResolvedValue(mockResult)
    const w = mountView()
    const filesInput = w.find('input[type="file"][multiple]')
    Object.defineProperty(filesInput.element, 'files', { value: [makeFile('T1.ab1')] })
    await filesInput.trigger('change')
    const excelInput = w.find('input[accept=".xlsx"]')
    Object.defineProperty(excelInput.element, 'files', { value: [makeFile('测序.xlsx')] })
    await excelInput.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    const form = vi.mocked(analyzeSequencingBatchChunked).mock.calls[0]
    expect(form[0].map((f: File) => f.name)).toEqual(['T1.ab1'])
    expect(form[1]?.name).toBe('测序.xlsx')
  })

  it('report_ready 时显示整理包下载按钮并调下载接口，缺省不显示', async () => {
    vi.mocked(analyzeSequencingBatchChunked).mockResolvedValue(mockResult)
    const w = mountView()
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [makeFile('T1.ab1')] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    // 旧响应没有 report_ready → 不显示下载入口
    expect(w.find('button.report-btn').exists()).toBe(false)

    vi.mocked(analyzeSequencingBatchChunked).mockResolvedValue({ ...mockResult, report_ready: true })
    const w2 = mountView()
    const input2 = w2.find('input[type="file"][multiple]')
    Object.defineProperty(input2.element, 'files', { value: [makeFile('T1.ab1')] })
    await input2.trigger('change')
    await w2.find('button.analyze-btn').trigger('click')
    await flushPromises()

    expect(w2.find('button.report-btn').exists()).toBe(true)
    await w2.find('button.report-btn').trigger('click')
    await flushPromises()
    expect(downloadBatchSequencingReport).toHaveBeenCalledWith('seqbatch_test1')
  })

  it('选择文件夹时 ~$ Excel 锁文件被跳过，正式信息表被选中', async () => {
    const w = mountView()
    const input = w.find('input[type="file"][multiple]')
    // webkitdirectory 的 FileList 按名称排序，~$ 锁文件（~ = 0x7E > 汉字码位）
    // 恰好比测序.xlsx 先出现——旧逻辑会错把锁文件当信息表
    Object.defineProperty(input.element, 'files', {
      value: [makeFile('~$测序.xlsx'), makeFile('测序.xlsx'), makeFile('T1.ab1')]
    })
    await input.trigger('change')

    expect(w.find('.staged-line').text()).toContain('测序.xlsx')
    expect(w.find('.staged-line').text()).not.toContain('~$测序.xlsx')
    expect(w.find('.staged-line').text()).toContain('测序/图谱文件 × 1')
    expect(w.text()).toContain('已自动跳过临时/系统文件：~$测序.xlsx')
  })

  it('被占用的文件在上传前被拦截并给出明确提示，不调接口', async () => {
    // Excel 打开时锁住的文件读不出来，XHR 在发送阶段整体失败 → 界面只剩
    // "Network Error"；组件应在发送前试读并指出具体文件（模拟 slice 读失败）
    const locked = makeFile('T1.ab1')
    Object.defineProperty(locked, 'slice', {
      value: () => ({ text: () => Promise.reject(new TypeError('read failed')) })
    })
    const w = mountView()
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [locked] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    expect(analyzeSequencingBatchChunked).not.toHaveBeenCalled()
    expect(w.find('.error-msg').text()).toContain('T1.ab1')
    expect(w.find('.error-msg').text()).toContain('无法读取')
  })

  it('超过 100MB 不再拦截：交给分块上传', async () => {
    vi.mocked(analyzeSequencingBatchChunked).mockResolvedValue(mockResult)
    const w = mountView()
    const big = makeFile('big.ab1')
    Object.defineProperty(big, 'size', { value: 300 * 1024 * 1024 })
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [big] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    expect(analyzeSequencingBatchChunked).toHaveBeenCalledTimes(1)
    expect(w.find('.error-msg').exists()).toBe(false)
    expect(w.findAll('tbody tr')).toHaveLength(2)
  })

  it('超过本账号单批总体积上限时不发起请求', async () => {
    const store = useAuthStore()
    store.siteConfig = {
      registration_open: true, email_verification_required: false, tier: 'user',
      features: { anonymous: [], user: [] }, effective_features: [],
      batch_read_limits: {
        max_reads_per_group: 24, max_total_reads: 240, max_files: 400, max_upload_mb: 500,
        chunk_max_files: 200, chunk_max_mb: 90, session_max_mb: 100
      }
    }
    const w = mountView()
    const big = makeFile('big.ab1')
    Object.defineProperty(big, 'size', { value: 101 * 1024 * 1024 })
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [big] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    expect(analyzeSequencingBatchChunked).not.toHaveBeenCalled()
    expect(w.find('.error-msg').text()).toContain('单批上限 100 MB')
  })

  it('上传中显示分块进度「上传中 3/7」，可取消并提示已删除暂存', async () => {
    let captured: { signal?: AbortSignal; onProgress?: (p: ChunkedProgress) => void } = {}
    vi.mocked(analyzeSequencingBatchChunked).mockImplementation((_f, _e, _q, opts) => {
      captured = opts || {}
      return new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => reject(new UploadCancelledError()))
      })
    })
    const w = mountView()
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [makeFile('T1.ab1')] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    captured.onProgress?.({ phase: 'uploading', done: 3, total: 7 })
    await flushPromises()
    const bar = w.find('[data-testid="upload-progress"]')
    expect(bar.exists()).toBe(true)
    expect(bar.text()).toContain('上传中 3/7')
    expect(w.find('button.analyze-btn').text()).toContain('上传中 3/7')
    expect((w.find('.progress-bar').element as HTMLElement).style.width).toBe('43%')

    await w.find('button.cancel-btn').trigger('click')
    await flushPromises()
    expect(captured.signal?.aborted).toBe(true)
    expect(w.find('.error-msg').text()).toContain('已取消上传')
    expect(w.find('[data-testid="upload-progress"]').exists()).toBe(false)
  })

  it('分析阶段显示分析中且不再提供取消', async () => {
    let captured: { onProgress?: (p: ChunkedProgress) => void } = {}
    vi.mocked(analyzeSequencingBatchChunked).mockImplementation((_f, _e, _q, opts) => {
      captured = opts || {}
      return new Promise(() => undefined)
    })
    const w = mountView()
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [makeFile('T1.ab1')] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    captured.onProgress?.({ phase: 'analyzing', done: 2, total: 2 })
    await flushPromises()
    expect(w.find('[data-testid="upload-progress"]').text()).toContain('分析中')
    expect(w.find('button.cancel-btn').exists()).toBe(false)
  })

  it('批量 read 数超限（413）时原样展示后端中文提示', async () => {
    // 审查 02 M6：后端在比对前按每组/整批 read 数上限拒绝，提示用户拆分批次
    const detail = '本批次共有 300 条测序 read 需要比对，超过单次上限（240 条）：请拆分为多个批次提交'
    vi.mocked(analyzeSequencingBatchChunked).mockRejectedValue({
      message: 'Request failed with status code 413',
      response: { status: 413, data: { detail } }
    })
    const w = mountView()
    const input = w.find('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', { value: [makeFile('T1.ab1')] })
    await input.trigger('change')
    await w.find('button.analyze-btn').trigger('click')
    await flushPromises()

    expect(analyzeSequencingBatchChunked).toHaveBeenCalledTimes(1)
    expect(w.find('.error-msg').text()).toBe(detail)
  })
})
