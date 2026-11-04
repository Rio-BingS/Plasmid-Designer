// 本文件由 api/index.ts 按域拆分而来（纯搬运）：index.ts 保留 re-export 兼容层，
// 新代码请直接从 '@/api/<域>' 导入。

import { api, saveBlobResponse, assertBlobIsGenbank } from './client'
import type {
  DesignRequest, DesignResult, DesignVectorInput, InsertDirection, RestrictionPreview, CodonTable
} from '@/types'

export async function submitDesign(request: DesignRequest): Promise<{ design_id: string; status: string }> {
  const response = await api.post('/design', request)
  return response.data
}

/**
 * 双酶切实时预览：选好两个酶就把成品构建体的布局算出来（不提交任务、不落库）。
 * 方向由后端按载体表达盒判定，front 只负责把用户的选择传过去。
 * cloning_method='gibson' 时按 Gibson 双酶切线性化预览（keep_restriction_sites 生效）；
 * cloning_method='custom_arms' 时按自定义同源臂定位预览（臂定位失败/歧义直接报错展示）。
 */
export async function getRestrictionPreview(payload: {
  enzymes?: string[]
  vector_id?: string
  vector?: DesignVectorInput
  insert_length?: number
  direction?: InsertDirection
  cloning_method?: 'restriction' | 'gibson' | 'custom_arms'
  keep_restriction_sites?: boolean
  homology_arm_5?: string
  homology_arm_3?: string
  /** 用户显式选择的骨架片段（切点对）；缺省 = 自动 */
  cut_pair?: [number, number]
}): Promise<RestrictionPreview> {
  const response = await api.post('/design/restriction-preview', payload)
  return response.data
}

export async function getDesign(designId: string): Promise<DesignResult> {
  const response = await api.get(`/design/${encodeURIComponent(designId)}`)
  return response.data
}

export async function downloadGenbank(designId: string): Promise<void> {
  const response = await api.get(`/design/${encodeURIComponent(designId)}/download/genbank`, { responseType: 'blob' })
  await saveBlobResponse(response, `${designId}.gb`)
}

export async function downloadPrimers(designId: string): Promise<void> {
  const response = await api.get(`/design/${encodeURIComponent(designId)}/download/primers`, { responseType: 'blob' })
  await saveBlobResponse(response, `${designId}_primers.tsv`)
}

// 载体库
export async function getCodonTables(): Promise<CodonTable[]> {
  const response = await api.get('/codon-tables')
  return response.data
}

// 载体/设计图谱（MapDocument）见 map/api.ts 的 getVectorMap / getDesignMap

// NCBI 搜索
export async function checkCompatibility(
  insertSequence: string,
  vectorSequence: string,
  enzymes: string[]
): Promise<any> {
  const response = await api.post('/analysis/compatibility', {
    insert_sequence: insertSequence,
    vector_sequence: vectorSequence,
    enzymes
  })
  return response.data
}

// 缓存 — 设计缓存失效
export async function fetchDesignGenbankFile(designId: string): Promise<File> {
  const response = await api.get(`/design/${encodeURIComponent(designId)}/download/genbank`, { responseType: 'blob' })
  assertBlobIsGenbank(response.data, designId)
  return new File([response.data], `${designId}.gb`, { type: 'application/octet-stream' })
}

/** 深链取参考序列时校验响应确实是 GenBank 文本——401/404 时错误 JSON 会被包成
 *  Blob 传给后端解析，用户只看到莫名的解析报错而看不到根因 */
