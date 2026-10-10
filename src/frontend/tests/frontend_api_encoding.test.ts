import { describe, it, expect, vi, beforeEach } from 'vitest'

// 路径参数必须经 encodeURIComponent：含 / ? # .. 的 ID 不能改写请求路径
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn()
}))

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      ...mocks,
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() }
      }
    }))
  }
}))

import {
  getDesign,
  getVector,
  getVectorMapData,
  previewNcbi,
  deleteVector,
  updateVector,
  getBatchProgress,
  updateAdminUser,
  deleteAdminUser,
  invalidateDesignCache,
  getReadTrace,
  getSequencingAnalysis,
  deleteSequencingAnalysis
} from '@/api'

const EVIL = '../admin/x?y=1#z'
const ENC = encodeURIComponent(EVIL)

describe('API 路径参数编码', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    for (const m of Object.values(mocks)) m.mockResolvedValue({ data: {} })
  })

  it('设计/载体/批量接口编码 ID', async () => {
    await getDesign(EVIL)
    await getVector(EVIL)
    await getVectorMapData(EVIL)
    await previewNcbi(EVIL)
    await getBatchProgress(EVIL)
    const urls = mocks.get.mock.calls.map((c) => c[0])
    expect(urls).toEqual([
      `/design/${ENC}`,
      `/vectors/${ENC}`,
      `/vectors/${ENC}/map`,
      `/vectors/preview/ncbi/${ENC}`,
      `/design/batch/${ENC}`
    ])
    for (const u of urls) {
      expect(u).not.toContain('?')
      expect(u).not.toContain('#')
      expect(u).not.toContain('../')
    }
  })

  it('写操作接口编码 ID', async () => {
    await deleteVector(EVIL)
    await updateVector(EVIL, { name: 'n' })
    await updateAdminUser(EVIL, { is_active: false })
    await deleteAdminUser(EVIL)
    await invalidateDesignCache(EVIL)
    expect(mocks.delete.mock.calls.map((c) => c[0])).toEqual([
      `/vectors/${ENC}`,
      `/admin/users/${ENC}`
    ])
    expect(mocks.put.mock.calls.map((c) => c[0])).toEqual([
      `/vectors/${ENC}`,
      `/admin/users/${ENC}`
    ])
    expect(mocks.post.mock.calls[0][0]).toBe(`/cache/invalidate/design/${ENC}`)
  })

  it('测序分析接口编码 analysis_id', async () => {
    await getReadTrace(EVIL, 2)
    await getSequencingAnalysis(EVIL)
    await deleteSequencingAnalysis(EVIL)
    expect(mocks.get.mock.calls.map((c) => c[0])).toEqual([
      `/sequencing/analyses/${ENC}/trace/2`,
      `/sequencing/analyses/${ENC}`
    ])
    expect(mocks.delete.mock.calls[0][0]).toBe(`/sequencing/analyses/${ENC}`)
  })

  it('普通 ID 不受影响', async () => {
    await getDesign('abc-123_x')
    expect(mocks.get.mock.calls[0][0]).toBe('/design/abc-123_x')
  })
})
