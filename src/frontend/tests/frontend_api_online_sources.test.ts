import { describe, it, expect, vi, beforeEach } from 'vitest'

// 在线序列来源的 API 层：路径/参数必须与后端契约一致（
// /api/vectors/import/snapgene|ensembl|addgene|sources），否则前端点了没反应
const { mockGet, mockPost } = vi.hoisted(() => ({ mockGet: vi.fn(), mockPost: vi.fn() }))
vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      get: mockGet,
      post: mockPost,
      delete: vi.fn(),
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    })),
  },
}))

import {
  getOnlineSources,
  getSnapgeneSets,
  getSnapgeneSetIndex,
  previewSnapgene,
  importFromSnapgene,
  previewEnsembl,
  importFromEnsembl,
  searchAddgeneIndex,
  getAddgeneMetadata,
  importFromAddgene,
  searchUniprot,
  getUniprotEntry,
  saveMapAsVector,
} from '@/api'

describe('在线序列来源 API 层', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockPost.mockReset()
    mockGet.mockResolvedValue({ data: {} })
    mockPost.mockResolvedValue({ data: {} })
  })

  it('来源清单与集合走只读端点', async () => {
    await getOnlineSources()
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/sources')

    await getSnapgeneSets()
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/snapgene/sets')

    await getSnapgeneSetIndex('yeast_plasmids')
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/snapgene/sets/yeast_plasmids')
  })

  it('集合/序列 ID 必须 URL 编码（防路径注入）', async () => {
    await getSnapgeneSetIndex('a/b?c')
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/snapgene/sets/a%2Fb%3Fc')
    await getAddgeneMetadata('#52961')
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/addgene/%2352961',
      { params: { try_sequence: false } })
  })

  it('SnapGene 预览带 set_id/plasmid_id，导入走 POST 请求体', async () => {
    await previewSnapgene('yeast_plasmids', 'pYES2')
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/snapgene/preview',
      { params: { set_id: 'yeast_plasmids', plasmid_id: 'pYES2' } })
    await importFromSnapgene('yeast_plasmids', 'pYES2')
    expect(mockPost).toHaveBeenCalledWith('/vectors/import/snapgene',
      { set_id: 'yeast_plasmids', plasmid_id: 'pYES2' })
  })

  it('Ensembl 预览带 seq_type，导入只允许 DNA 类型', async () => {
    await previewEnsembl('ENST00000288602', 'cds')
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/ensembl/preview',
      { params: { identifier: 'ENST00000288602', seq_type: 'cds' } })
    await importFromEnsembl('ENST00000288602', 'cds', 'myCDS')
    expect(mockPost).toHaveBeenCalledWith('/vectors/import/ensembl',
      { identifier: 'ENST00000288602', seq_type: 'cds', name: 'myCDS' })
  })

  it('Addgene 只查本地索引/元数据（try_sequence 默认关闭，不代登录）', async () => {
    await searchAddgeneIndex('lentiCRISPR', 5)
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/addgene/search',
      { params: { term: 'lentiCRISPR', limit: 5 } })
    await getAddgeneMetadata('244694')
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/addgene/244694',
      { params: { try_sequence: false } })
  })

  it('Addgene 带凭据导入走 POST 且质粒号 URL 编码', async () => {
    await importFromAddgene('#52961', 'dna', 'lentiCRISPRv2')
    expect(mockPost).toHaveBeenCalledWith('/vectors/import/addgene/%2352961', null,
      { params: { fmt: 'dna', name: 'lentiCRISPRv2' } })
  })

  it('UniProt 检索/条目端点参数与后端一致（蛋白库反查核酸来源）', async () => {
    await searchUniprot('TP53', 10, true)
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/uniprot/search',
      { params: { query: 'TP53', limit: 10, reviewed_only: true } })
    await getUniprotEntry('P04637', true)
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/uniprot/P04637',
      { params: { with_fasta: true } })
  })

  it('UniProt 登录号 URL 编码（防路径注入）', async () => {
    await getUniprotEntry('a/b?c')
    expect(mockGet).toHaveBeenCalledWith('/vectors/import/uniprot/a%2Fb%3Fc',
      { params: { with_fasta: false } })
  })

  it('图谱页另存为载体：整份序列与特征走 POST /vectors/import/from-map', async () => {
    const payload = {
      name: 'myConstruct',
      sequence: 'ACGT',
      topology: 'linear',
      features: [{ name: 'ins', type: 'insert', start: 1, end: 4, strand: 1 }],
      source: 'paste',
    }
    await saveMapAsVector(payload)
    expect(mockPost).toHaveBeenCalledWith('/vectors/import/from-map', payload)
  })
})
