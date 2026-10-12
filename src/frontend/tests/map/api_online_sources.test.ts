import { describe, it, expect, vi, beforeEach } from 'vitest'

// 图谱页「在线导入」的 API 层：路径/参数必须与后端契约一致
// （/api/map/import/sources|snapgene|ensembl），否则点了没反应
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
  ENSEMBL_ID_RE, getOnlineSnapgeneIndex, getOnlineSnapgeneSets, getOnlineSources,
  importOnlineEnsembl, importOnlineSnapgene,
} from '@/map/api'

describe('map/api 在线来源', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue({ data: {} })
    mocks.post.mockResolvedValue({ data: { schema: 'plasmid-map/1' } })
  })

  it('来源清单与集合/索引走只读端点，路径参数要编码', async () => {
    await getOnlineSources()
    await getOnlineSnapgeneSets()
    await getOnlineSnapgeneIndex('a/b?c')
    expect(mocks.get.mock.calls.map((c) => c[0])).toEqual([
      '/map/import/sources',
      '/map/import/snapgene/sets',
      `/map/import/snapgene/sets/${encodeURIComponent('a/b?c')}`,
    ])
  })

  it('SnapGene 打开把 set/plasmid + 选项一起提交', async () => {
    await importOnlineSnapgene({
      set_id: 'coronavirus_resources', plasmid_id: 'SARS_Genome',
      annotate: false, enzyme_set: 'golden_gate', orf_min_aa: 50,
    })
    expect(mocks.post).toHaveBeenCalledWith('/map/import/snapgene', {
      set_id: 'coronavirus_resources', plasmid_id: 'SARS_Genome',
      annotate: false, enzyme_set: 'golden_gate', orf_min_aa: 50,
    })
  })

  it('Ensembl 打开提交 identifier + seq_type', async () => {
    await importOnlineEnsembl({ identifier: 'ENST00000288602', seq_type: 'cds' })
    expect(mocks.post).toHaveBeenCalledWith('/map/import/ensembl', {
      identifier: 'ENST00000288602', seq_type: 'cds',
    })
  })

  it('前端 Ensembl ID 粗校验：合法 ID 通过，含空格的拒绝', () => {
    for (const ok of ['ENSG00000139618', 'ENST00000288602', 'ENSG00000139618.14', 'FBgn0000001']) {
      expect(ENSEMBL_ID_RE.test(ok)).toBe(true)
    }
    for (const bad of ['', 'ENSG 1', 'x', 'ENSG;rm']) {
      expect(ENSEMBL_ID_RE.test(bad)).toBe(false)
    }
  })
})
