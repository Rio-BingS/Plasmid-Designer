import { describe, it, expect, vi, beforeEach } from 'vitest'

// 环状开关的 API 层：circular 必须进入请求体，否则后端按默认线性消化
const { mockPost } = vi.hoisted(() => ({ mockPost: vi.fn() }))
vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      get: vi.fn(),
      post: mockPost,
      delete: vi.fn(),
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    })),
  },
}))

import { simulateDigest } from '@/api'

describe('simulateDigest circular 字段', () => {
  beforeEach(() => {
    mockPost.mockReset()
    mockPost.mockResolvedValue({ data: { fragments: [] } })
  })

  it.each([true, false])('circular=%s 原样写入请求体', async (circular) => {
    await simulateDigest('GAATTC', ['EcoRI'], circular)
    expect(mockPost).toHaveBeenCalledWith('/analysis/digest', {
      sequence: 'GAATTC', enzymes: ['EcoRI'], circular,
    })
  })

  it('不传时默认线性（与后端默认一致）', async () => {
    await simulateDigest('GAATTC', ['EcoRI'])
    expect(mockPost.mock.calls[0][1].circular).toBe(false)
  })
})
