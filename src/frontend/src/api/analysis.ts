// 本文件由 api/index.ts 按域拆分而来（纯搬运）：index.ts 保留 re-export 兼容层，
// 新代码请直接从 '@/api/<域>' 导入。

import { api, saveBlobResponse } from './client'

export async function analyzeSequence(sequence: string, sequenceType: string = 'dna'): Promise<any> {
  const response = await api.post('/analysis/analyze', {
    sequence,
    sequence_type: sequenceType
  })
  return response.data
}

// 限制性位点分析
export async function findRestrictionSites(sequence: string, enzymes?: string[]): Promise<any> {
  const response = await api.post('/analysis/restriction-sites', {
    sequence,
    enzymes
  })
  return response.data
}

// ORF 预测
export async function predictORFs(sequence: string, minLength: number = 100): Promise<any> {
  const response = await api.post('/analysis/orfs', {
    sequence,
    min_length: minLength
  })
  return response.data
}

// 酶切消化模拟（完全消化，返回片段大小）；circular=true 按环状质粒首尾相连
export async function simulateDigest(sequence: string, enzymes: string[], circular: boolean = false): Promise<any> {
  const response = await api.post('/analysis/digest', {
    sequence,
    enzymes,
    circular
  })
  return response.data
}

// GC 分析
export async function analyzeGC(sequence: string, windowSize: number = 100): Promise<any> {
  const response = await api.post('/analysis/gc-analysis', {
    sequence,
    window_size: windowSize
  })
  return response.data
}

// 序列导出
export async function exportSequence(sequence: string, features: any[], format: string, name: string = 'sequence'): Promise<void> {
  const response = await api.post('/analysis/export', {
    sequence,
    features,
    format,
    name
  }, { responseType: 'blob' })
  const extensions: Record<string, string> = {
    genbank: 'gb', fasta: 'fasta', snapgene: 'dna', benchling: 'json', sbol: 'json'
  }
  await saveBlobResponse(response, `${name}.${extensions[format] || format}`)
}

// 酶信息（对应后端 GET /analysis/enzymes 返回结构）
export interface EnzymeInfo {
  recognition_sequence: string
  cut_type: string
  is_type_iis: boolean
}

/** Golden Gate 候选酶（REBASE：可商购、切出 4 nt 5' 突出端的 Type IIS 酶；BsaI/BsmBI/BbsI 在前） */
export interface GoldenGateEnzyme {
  name: string
  recognition_sequence: string
}

// 获取可用酶列表
export async function getEnzymes(): Promise<{
  total: number
  enzymes: Record<string, EnzymeInfo>
  golden_gate_enzymes?: GoldenGateEnzyme[]
}> {
  const response = await api.get('/analysis/enzymes')
  return response.data
}
// 用户认证
export async function getExportFormats(): Promise<any[]> {
  const response = await api.get('/analysis/export/formats')
  return response.data
}

// 导出所有格式为 ZIP
export async function exportAllFormats(
  sequence: string,
  features: any[],
  name: string,
  description: string = '',
  isCircular: boolean = true
): Promise<void> {
  const response = await api.post('/analysis/export/all', {
    sequence,
    features,
    name,
    description,
    is_circular: isCircular
  }, { responseType: 'blob' })
  await saveBlobResponse(response, `${name}_exports.zip`)
}

// 克隆兼容性检查（JSON body，与后端 CompatibilityRequest 对齐）
