// 本文件由 api/index.ts 按域拆分而来（纯搬运）：index.ts 保留 re-export 兼容层，
// 新代码请直接从 '@/api/<域>' 导入。

import { api, assertBlobIsGenbank } from './client'
import type {
  BatchImportResult, DeleteVectorResult, NcbiIdImportResult, NcbiImportResult, NcbiSearchResult, VectorInfo, VectorPreview, VectorUploadResult
} from '@/types'

export async function getVectors(
  vectorType?: string,
  host?: string
): Promise<VectorInfo[]> {
  const params: Record<string, string> = {}
  if (vectorType) params.vector_type = vectorType
  if (host) params.host = host
  const response = await api.get('/vectors', { params })
  return response.data
}

export async function getVector(vectorId: string): Promise<VectorInfo> {
  const response = await api.get(`/vectors/${encodeURIComponent(vectorId)}`)
  return response.data
}

export async function searchNcbi(query: string, limit: number = 10): Promise<NcbiSearchResult> {
  const response = await api.get('/vectors/search/ncbi', { params: { query, limit } })
  return response.data
}

// NCBI 导入
export async function importFromNcbi(query: string, limit: number = 5): Promise<NcbiImportResult> {
  const response = await api.post('/vectors/import/ncbi', null, { params: { query, limit } })
  return response.data
}

// NCBI ID 直接导入
export async function importFromNcbiId(seqId: string): Promise<NcbiIdImportResult> {
  const response = await api.post('/vectors/import/ncbi-id', null, { params: { seq_id: seqId } })
  return response.data
}

// NCBI 预览
export async function previewNcbi(seqId: string): Promise<VectorPreview> {
  const response = await api.get(`/vectors/preview/ncbi/${encodeURIComponent(seqId)}`)
  return response.data
}

// 删除载体
export async function deleteVector(vectorId: string): Promise<DeleteVectorResult> {
  const response = await api.delete(`/vectors/${encodeURIComponent(vectorId)}`)
  return response.data
}

// 批量设计
export async function getVectorSequence(vectorId: string, format: string = 'fasta'): Promise<string> {
  const response = await api.get(`/vectors/${encodeURIComponent(vectorId)}/sequence`, {
    params: { format },
    responseType: 'text'
  })
  return response.data
}

// 上传载体文件导入
export async function uploadVectorFile(file: File): Promise<VectorUploadResult> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post('/vectors/import/upload', formData)
  return response.data
}

// ==================== 设计页一次性载体（本地上传 / 在线导入） ====================
// 与 /vectors/import/* 的分工：那条路写**共享**载体库、需管理员；这条路只把
// 载体取回来随本次设计提交（后端不落盘），普通用户也能用自己手里的载体。

/** 在线来源清单与许可摘要（不出网；与载体库/图谱页同一份数据） */
export async function updateVector(vectorId: string, data: {
  name?: string; description?: string; vector_type?: string;
  host?: string[]; antibiotic_resistance?: string[]; copy_number?: string
}): Promise<any> {
  const response = await api.put(`/vectors/${encodeURIComponent(vectorId)}`, data)
  return response.data
}

// 批量导入载体
export async function batchImportVectors(ncbiIds: string[], filePaths: string[] = []): Promise<BatchImportResult> {
  const response = await api.post('/vectors/import/batch', {
    ncbi_ids: ncbiIds,
    file_paths: filePaths
  })
  return response.data
}

// ==================== 在线序列来源（SnapGene 在线库 / Ensembl / Addgene） ====================
// 许可提示随数据一起下发（见后端 /api/vectors/import/sources）：SnapGene 库学术/非营利/
// 政府可无条件使用但须引用来源、商业用途须获 GraphPad 许可；Addgene 全长序列需登录，
// 后端不代为登录——前端只做展示与提示，不做任何绕过。

export async function fetchVectorGenbankFile(vectorId: string): Promise<File> {
  const response = await api.get(`/vectors/${encodeURIComponent(vectorId)}/sequence`, {
    params: { format: 'genbank' },
    responseType: 'blob'
  })
  assertBlobIsGenbank(response.data, vectorId)
  return new File([response.data], `${vectorId}.gb`, { type: 'application/octet-stream' })
}

export async function saveMapAsVector(payload: {
  name: string
  sequence: string
  topology: string
  features: {
    name: string; type: string; start: number; end: number
    strand?: number; notes?: string; color?: string
  }[]
  source?: string
  url?: string
  description?: string
}): Promise<any> {
  const response = await api.post('/vectors/import/from-map', payload)
  return response.data
}

// 获取缓存信息
