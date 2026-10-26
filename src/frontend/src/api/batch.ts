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

/** 合并引物订购表：整批所有引物汇总成一张 TSV（按基因分组核对订购单） */
export async function downloadBatchPrimers(batchId: string): Promise<void> {
  const response = await api.get(`/design/batch/${encodeURIComponent(batchId)}/download/primers`, { responseType: 'blob' })
  await saveBlobResponse(response, `${batchId}_all_primers.tsv`)
}

/** 表格上传解析（.xlsx/.xlsm/.csv/.tsv → 批量序列清单；后端 openpyxl 解析） */
export async function parseSpreadsheetFile(
  file: File, sequenceType: 'amino_acid' | 'dna'
): Promise<{ sequences: { name: string; sequence: string; named: boolean }[]; count: number }> {
  const form = new FormData()
  form.append('file', file)
  const response = await api.post(
    `/design/batch/parse-spreadsheet?sequence_type=${sequenceType}`, form,
    { headers: { 'Content-Type': 'multipart/form-data' } }
  )
  return response.data
}

/** 批量上传表格模板 .xlsx（序列清单 + 填写说明） */
export async function downloadBatchTemplate(): Promise<void> {
  const response = await api.get('/design/batch/template', { responseType: 'blob' })
  await saveBlobResponse(response, 'batch_sequences_template.xlsx')
}


// 载体序列下载
