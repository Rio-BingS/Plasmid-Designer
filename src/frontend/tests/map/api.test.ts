import { describe, it, expect, vi, beforeEach } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      ...mocks,
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    })),
  },
}))

import {
  analyzeMap, exportGenbank, getDesignMap, getEnzymeSets, getVectorMap, parseMapFile, parseMapText,
} from '@/map/api'

describe('map/api', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue({ data: { ok: 1 } })
    mocks.post.mockResolvedValue({ data: { ok: 2 } })
  })

  it('GET 端点编码路径参数', async () => {
    await getVectorMap('a/b?c')
    await getDesignMap('x#y')
    await getEnzymeSets()
    expect(mocks.get.mock.calls.map((c) => c[0])).toEqual([
      `/vectors/${encodeURIComponent('a/b?c')}/map`,
      `/design/${encodeURIComponent('x#y')}/map`,
      '/map/enzyme-sets',
    ])
  })

  it('parse：文件走 multipart，文本走 JSON，选项走查询参数', async () => {
    const f = new File(['>x\nACGT'], 'x.fa')
    expect(await parseMapFile(f, { annotate: false, enzyme_set: 'unique6' })).toEqual({ ok: 2 })
    const [url, body, cfg] = mocks.post.mock.calls[0]
    expect(url).toBe('/map/parse')
    expect(body).toBeInstanceOf(FormData)
    expect((body as FormData).get('file')).toBeTruthy()
    expect(cfg.params).toEqual({ annotate: false, enzyme_set: 'unique6' })

    await parseMapText('ACGT', { name: 'n', topology: 'linear' }, { orf_min_aa: 50 })
    expect(mocks.post.mock.calls[1]).toEqual(['/map/parse', { text: 'ACGT', name: 'n', topology: 'linear' }, { params: { orf_min_aa: 50 } }])
  })

  it('analyze / export', async () => {
    await analyzeMap({ sequence: 'ACGT', topology: 'circular', enzyme_set: 'golden_gate' })
    expect(mocks.post.mock.calls[0][0]).toBe('/map/analyze')
    await exportGenbank({ name: 'n' } as any)
    expect(mocks.post.mock.calls[1][0]).toBe('/map/export/genbank')
    expect(mocks.post.mock.calls[1][2]).toEqual({ responseType: 'blob' })
  })
})
