// 本文件由 api/index.ts 按域拆分而来（纯搬运）：index.ts 保留 re-export 兼容层，
// 新代码请直接从 '@/api/<域>' 导入。

import { api, saveBlobResponse } from './client'
import type {
  BatchDesignRequestPayload, BatchProgress, BatchStatus
} from '@/types'

export async function submitBatchDesign(request: BatchDesignRequestPayload): Promise<BatchStatus> {
  const response = await api.post('/design/batch', request)
  return response.data
}

export async function getBatchProgress(batchId: string): Promise<BatchProgress> {
  const response = await api.get(`/design/batch/${encodeURIComponent(batchId)}`)
  return response.data
}

export async function getBatchReport(batchId: string): Promise<Record<string, unknown>> {
  const response = await api.get(`/design/batch/${encodeURIComponent(batchId)}/report`)
  return response.data
}

export async function downloadBatchResults(batchId: string): Promise<void> {
  const response = await api.get(`/design/batch/${encodeURIComponent(batchId)}/download`, { responseType: 'blob' })
  await saveBlobResponse(response, `${batchId}_results.zip`)
}


// 载体序列下载
