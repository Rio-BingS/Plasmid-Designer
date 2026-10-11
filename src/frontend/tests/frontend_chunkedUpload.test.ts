import { describe, it, expect, vi } from 'vitest'
import {
  splitIntoChunks,
  runChunkedBatch,
  isRetryable,
  UploadCancelledError,
  type ChunkedTransport,
  type ChunkedProgress
} from '@/utils/chunkedUpload'

const MB = 1024 * 1024

function file(name: string, size: number): File {
  const f = new File([new Uint8Array(1)], name)
  Object.defineProperty(f, 'size', { value: size })
  return f
}

type R = { batch_id: string }

function transport(over: Partial<ChunkedTransport<R>> = {}) {
  const t = {
    create: vi.fn(async () => ({ session_id: 's1', access_token: 'tok', limits: { chunk_max_files: 2, chunk_max_mb: 10 } })),
    upload: vi.fn(async () => ({})),
    finalize: vi.fn(async () => ({ status: 200, data: { batch_id: 'b1' } as R })),
    status: vi.fn(async () => ({ state: 'done' as const, result: { batch_id: 'b1' } })),
    abort: vi.fn(async () => undefined),
    ...over
  }
  return t
}

const fast = { retryDelayMs: 1, pollIntervalMs: 1 }

describe('splitIntoChunks', () => {
  it('按个数切分并保持原顺序', () => {
    const fs = Array.from({ length: 5 }, (_, i) => file(`f${i}.ab1`, 1))
    const chunks = splitIntoChunks(fs, { maxFiles: 2, maxBytes: 100 * MB })
    expect(chunks.map((c) => c.map((f) => f.name))).toEqual([['f0.ab1', 'f1.ab1'], ['f2.ab1', 'f3.ab1'], ['f4.ab1']])
  })

  it('按体积切分：不超过 90MB，超大的单个文件独占一块', () => {
    const fs = [file('a', 50 * MB), file('b', 30 * MB), file('c', 20 * MB), file('huge', 120 * MB), file('d', 1 * MB)]
    const chunks = splitIntoChunks(fs, { maxFiles: 200, maxBytes: 90 * MB })
    expect(chunks.map((c) => c.map((f) => f.name))).toEqual([['a', 'b'], ['c'], ['huge'], ['d']])
    for (const c of chunks.filter((c) => c.length > 1)) {
      expect(c.reduce((s, f) => s + f.size, 0)).toBeLessThanOrEqual(90 * MB)
    }
  })

  it('默认上限 200 个 / 90MB；空列表无分块', () => {
    const fs = Array.from({ length: 450 }, (_, i) => file(`r${i}.ab1`, 300 * 1024))
    const chunks = splitIntoChunks(fs)
    expect(chunks.map((c) => c.length)).toEqual([200, 200, 50])
    expect(splitIntoChunks([])).toEqual([])
  })
})

describe('runChunkedBatch', () => {
  it('建会话时带文件清单与信息表，逐块上传并报告进度，finalize 返回结果', async () => {
    const t = transport()
    const excel = file('表.xlsx', 10)
    const fs = [file('a.ab1', 1), file('b.ab1', 1), file('c.ab1', 1)]
    const progress: ChunkedProgress[] = []
    const res = await runChunkedBatch(fs, excel, 25, t, { ...fast, onProgress: (p) => progress.push({ ...p }) })

    expect(res).toEqual({ batch_id: 'b1' })
    expect(t.create).toHaveBeenCalledWith(
      [{ name: 'a.ab1', size: 1 }, { name: 'b.ab1', size: 1 }, { name: 'c.ab1', size: 1 }], excel)
    // 服务器下发的分块上限（2 个）生效
    expect(t.upload.mock.calls.map((c: any[]) => [c[0], c[1].map((f: File) => f.name), c[2], c[3]])).toEqual([
      ['s1', ['a.ab1', 'b.ab1'], 0, 'tok'],
      ['s1', ['c.ab1'], 1, 'tok']
    ])
    expect(t.finalize).toHaveBeenCalledWith('s1', 25, 'tok')
    expect(progress).toEqual([
      { phase: 'uploading', done: 0, total: 2 },
      { phase: 'uploading', done: 1, total: 2 },
      { phase: 'uploading', done: 2, total: 2 },
      { phase: 'analyzing', done: 2, total: 2 }
    ])
    expect(t.abort).not.toHaveBeenCalled()
  })

  it('分块失败（网络中断/5xx）按同一序号重试后成功', async () => {
    let n = 0
    const t = transport({
      upload: vi.fn(async () => {
        n++
        if (n === 1) throw new Error('Network Error')
        if (n === 2) throw { response: { status: 502 } }
        return {}
      })
    })
    await runChunkedBatch([file('a.ab1', 1)], null, 20, t, fast)
    expect(t.upload).toHaveBeenCalledTimes(3)
    expect(t.upload.mock.calls.map((c: any[]) => c[2])).toEqual([0, 0, 0])
  })

  it('重试用尽后放弃，并删除服务器会话', async () => {
    const t = transport({ upload: vi.fn(async () => { throw new Error('Network Error') }) })
    await expect(runChunkedBatch([file('a.ab1', 1)], null, 20, t, { ...fast, retries: 2 })).rejects.toThrow('Network Error')
    expect(t.upload).toHaveBeenCalledTimes(3)
    expect(t.abort).toHaveBeenCalledWith('s1', 'tok')
    expect(t.finalize).not.toHaveBeenCalled()
  })

  it('确定性拒绝（4xx）不重试，直接报错并删除会话', async () => {
    const err = { response: { status: 413, data: { detail: '本批次总体积超过上限（2048 MB）' } } }
    const t = transport({ upload: vi.fn(async () => { throw err }) })
    await expect(runChunkedBatch([file('a.ab1', 1)], null, 20, t, fast)).rejects.toBe(err)
    expect(t.upload).toHaveBeenCalledTimes(1)
    expect(t.abort).toHaveBeenCalledTimes(1)
  })

  it('建会话即被拒（read 超限预检 413）时不上传、无会话可删', async () => {
    const err = { response: { status: 413, data: { detail: '共有 300 条' } } }
    const t = transport({ create: vi.fn(async () => { throw err }) })
    await expect(runChunkedBatch([file('a.ab1', 1)], null, 20, t, fast)).rejects.toBe(err)
    expect(t.upload).not.toHaveBeenCalled()
    expect(t.abort).not.toHaveBeenCalled()
  })

  it('取消：中止上传并删除会话，抛出 UploadCancelledError', async () => {
    const ctrl = new AbortController()
    const t = transport({
      upload: vi.fn(async (_s: string, _f: File[], index: number) => {
        if (index === 1) ctrl.abort()
        return {}
      })
    })
    const fs = [file('a', 1), file('b', 1), file('c', 1), file('d', 1), file('e', 1)]
    await expect(runChunkedBatch(fs, null, 20, t, { ...fast, signal: ctrl.signal })).rejects.toBeInstanceOf(UploadCancelledError)
    expect(t.upload).toHaveBeenCalledTimes(2)
    expect(t.abort).toHaveBeenCalledWith('s1', 'tok')
    expect(t.finalize).not.toHaveBeenCalled()
  })

  it('上传请求被取消（axios CanceledError）不重试', async () => {
    const ctrl = new AbortController()
    const t = transport({
      upload: vi.fn(async () => {
        ctrl.abort()
        throw { name: 'CanceledError', code: 'ERR_CANCELED' }
      })
    })
    await expect(runChunkedBatch([file('a', 1)], null, 20, t, { ...fast, signal: ctrl.signal })).rejects.toBeInstanceOf(UploadCancelledError)
    expect(t.upload).toHaveBeenCalledTimes(1)
    expect(t.abort).toHaveBeenCalledTimes(1)
  })

  it('finalize 返回 202 时轮询状态直到完成', async () => {
    const states = [{ state: 'running' as const }, { state: 'running' as const }, { state: 'done' as const, result: { batch_id: 'late' } }]
    const t = transport({
      finalize: vi.fn(async () => ({ status: 202, data: { state: 'running' as const } })),
      status: vi.fn(async () => states.shift()!)
    })
    const res = await runChunkedBatch([file('a', 1)], null, 20, t, fast)
    expect(res).toEqual({ batch_id: 'late' })
    expect(t.status).toHaveBeenCalledTimes(3)
  })

  it('后台分析失败时以后端 detail 报错（与单请求入口同形）', async () => {
    const t = transport({
      finalize: vi.fn(async () => ({ status: 202, data: { state: 'running' as const } })),
      status: vi.fn(async () => ({ state: 'failed' as const, error: { status_code: 413, detail: '本批次共有 300 条测序 read' } }))
    })
    const p = runChunkedBatch([file('a', 1)], null, 20, t, fast)
    await expect(p).rejects.toMatchObject({ response: { status: 413, data: { detail: '本批次共有 300 条测序 read' } } })
    expect(t.abort).not.toHaveBeenCalled()       // 已提交：不再删会话（结果保留供查询）
  })

  it('finalize 网络中断：先查状态，未提交则重新提交一次，不重复分析', async () => {
    const t = transport({
      finalize: vi.fn()
        .mockRejectedValueOnce(new Error('Network Error'))
        .mockResolvedValueOnce({ status: 200, data: { batch_id: 'b2' } }),
      status: vi.fn(async () => ({ state: 'open' as const }))
    })
    expect(await runChunkedBatch([file('a', 1)], null, 20, t, fast)).toEqual({ batch_id: 'b2' })
    expect(t.finalize).toHaveBeenCalledTimes(2)

    const t2 = transport({
      finalize: vi.fn().mockRejectedValueOnce(new Error('Network Error')),
      status: vi.fn()
        .mockResolvedValueOnce({ state: 'running' })
        .mockResolvedValueOnce({ state: 'done', result: { batch_id: 'b3' } })
    })
    expect(await runChunkedBatch([file('a', 1)], null, 20, t2, fast)).toEqual({ batch_id: 'b3' })
    expect(t2.finalize).toHaveBeenCalledTimes(1)
  })

  it('finalize 确定性拒绝（413 read 超限）直接抛出', async () => {
    const err = { response: { status: 413, data: { detail: '超过单次上限' } } }
    const t = transport({ finalize: vi.fn(async () => { throw err }) })
    await expect(runChunkedBatch([file('a', 1)], null, 20, t, fast)).rejects.toBe(err)
    expect(t.status).not.toHaveBeenCalled()
  })
})

describe('isRetryable', () => {
  it('网络错误/408/429/5xx 可重试，其余 4xx 不重试', () => {
    expect(isRetryable(new Error('Network Error'))).toBe(true)
    expect(isRetryable({ response: { status: 503 } })).toBe(true)
    expect(isRetryable({ response: { status: 429 } })).toBe(true)
    expect(isRetryable({ response: { status: 408 } })).toBe(true)
    expect(isRetryable({ response: { status: 400 } })).toBe(false)
    expect(isRetryable({ response: { status: 404 } })).toBe(false)
    expect(isRetryable(new UploadCancelledError())).toBe(false)
  })
})
