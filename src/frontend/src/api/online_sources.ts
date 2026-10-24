// 本文件由 api/index.ts 按域拆分而来（纯搬运）：index.ts 保留 re-export 兼容层，
// 新代码请直接从 '@/api/<域>' 导入。

import { api } from './client'
import type {
  DesignVectorPayload
} from '@/types'

export async function getDesignVectorSources(): Promise<{
  enabled: boolean
  snapgene_online_enabled?: boolean
  disclaimer: string
  sources: OnlineSourceInfo[]
}> {
  const response = await api.get('/design/vector/sources')
  return response.data
}

/** 上传载体文件（GenBank / FASTA / SnapGene .dna）→ 一次性载体载荷 */
export async function uploadDesignVector(file: File): Promise<DesignVectorPayload> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post('/design/vector/upload', formData)
  return response.data
}

export interface DesignVectorOnlineRequest {
  source: 'snapgene' | 'ensembl' | 'ncbi' | 'addgene'
  set_id?: string
  plasmid_id?: string
  identifier?: string
  seq_type?: 'genomic' | 'cds' | 'cdna'
  accession?: string
  addgene_id?: string
  fmt?: 'genbank' | 'dna'
}

/** 按来源在线取一条序列 → 一次性载体载荷（SnapGene 需登录；Addgene 需管理员） */
export async function importDesignVectorOnline(
  payload: DesignVectorOnlineRequest
): Promise<DesignVectorPayload> {
  const response = await api.post('/design/vector/online', payload)
  return response.data
}

/** SnapGene 在线库按质粒名跨集合搜索（只给候选，选中后再取全长） */
export async function searchDesignVectorSnapgene(q: string, limit = 20): Promise<{
  query: string
  count: number
  truncated?: boolean
  plasmids: {
    plasmid_id: string
    name: string
    filename?: string
    set_id?: string
    set_name?: string
  }[]
  failed_sets?: string[]
}> {
  const response = await api.get('/design/vector/snapgene/search', { params: { q, limit } })
  return response.data
}

// 更新载体信息
export interface OnlineSourceInfo {
  id: string
  name: string
  kind: string
  url: string
  license: string
  importable: boolean
  note: string
  /** 该来源的全长序列是否需要登录（Addgene） */
  requires_login?: boolean
  /** 部署方是否配置了用户自备凭据（Addgene 会话 Cookie） */
  session_configured?: boolean
  /** 该来源是否已由部署方开启（SnapGene 在线库默认开启，部署方可用 SNAPGENE_ONLINE_ENABLED=false 关闭） */
  enabled?: boolean
}

export interface OnlinePreview {
  source: string
  id: string
  name: string
  url: string
  length: number
  topology: string
  gc_content: number
  features: { name?: string; type?: string; start?: number; end?: number; strand?: number }[]
  warnings: string[]
  license: string
}

export async function getOnlineSources(): Promise<{
  enabled: boolean
  /** SnapGene 在线库开关（默认 false）；开启后也只对已登录用户开放 */
  snapgene_online_enabled?: boolean
  disclaimer: string
  sources: OnlineSourceInfo[]
}> {
  const response = await api.get('/vectors/import/sources')
  return response.data
}

export async function getSnapgeneSets(): Promise<{ count: number; sets: { set_id: string; name: string }[]; license: string }> {
  const response = await api.get('/vectors/import/snapgene/sets')
  return response.data
}

export async function getSnapgeneSetIndex(setId: string): Promise<{ set_id: string; count: number; plasmids: any[] }> {
  const response = await api.get(`/vectors/import/snapgene/sets/${encodeURIComponent(setId)}`)
  return response.data
}

export async function searchSnapgene(q: string): Promise<{
  query: string; count: number; truncated: boolean; plasmids: any[]; failed_sets: string[]
}> {
  const response = await api.get('/vectors/import/snapgene/search', { params: { q }, timeout: 90_000 })
  return response.data
}

export async function previewSnapgene(setId: string, plasmidId: string): Promise<OnlinePreview> {
  const response = await api.get('/vectors/import/snapgene/preview', {
    params: { set_id: setId, plasmid_id: plasmidId }
  })
  return response.data
}

export async function importFromSnapgene(setId: string, plasmidId: string): Promise<any> {
  const response = await api.post('/vectors/import/snapgene', { set_id: setId, plasmid_id: plasmidId })
  return response.data
}

export async function lookupEnsembl(identifier: string, expand: boolean = false): Promise<any> {
  const response = await api.get(`/vectors/import/ensembl/lookup/${encodeURIComponent(identifier)}`, {
    params: { expand }
  })
  return response.data
}

export async function previewEnsembl(identifier: string, seqType: string = 'cds'): Promise<OnlinePreview> {
  const response = await api.get('/vectors/import/ensembl/preview', {
    params: { identifier, seq_type: seqType }
  })
  return response.data
}

export async function importFromEnsembl(identifier: string, seqType: string = 'cds', name?: string): Promise<any> {
  const response = await api.post('/vectors/import/ensembl', { identifier, seq_type: seqType, name })
  return response.data
}

export async function searchAddgeneIndex(term: string, limit: number = 20): Promise<any> {
  const response = await api.get('/vectors/import/addgene/search', { params: { term, limit } })
  return response.data
}

export async function getAddgeneMetadata(plasmidId: string, trySequence: boolean = false): Promise<any> {
  const response = await api.get(`/vectors/import/addgene/${encodeURIComponent(plasmidId)}`, {
    params: { try_sequence: trySequence }
  })
  return response.data
}

// Addgene 全长序列导入（需管理员 + 部署方已配置用户自备会话 Cookie）：
// 后端不代登录、不绕登录墙，只带用户自己的凭据取自己有权访问的序列。
export async function importFromAddgene(
  plasmidId: string, fmt: 'genbank' | 'dna' = 'genbank', name?: string
): Promise<any> {
  const response = await api.post(`/vectors/import/addgene/${encodeURIComponent(plasmidId)}`, null, {
    params: { fmt, name }
  })
  return response.data
}

// -------------------- UniProt（蛋白库：只做反查，不给质粒序列） --------------------

export interface UniProtNucleotide {
  source: string
  accession: string
  label: string
  url: string
}

export interface UniProtEntry {
  accession: string
  entry_id: string
  name: string
  gene: string
  organism: string
  length: number
  reviewed: boolean
  cross_references: { database: string; id: string; protein_id?: string; molecule_type?: string }[]
}

export async function searchUniprot(
  query: string, limit: number = 10, reviewedOnly: boolean = false
): Promise<{ query: string; count: number; results: UniProtEntry[]; license: string; note: string }> {
  const response = await api.get('/vectors/import/uniprot/search', {
    params: { query, limit, reviewed_only: reviewedOnly }
  })
  return response.data
}

export async function getUniprotEntry(
  accession: string, withFasta: boolean = false
): Promise<{ accession: string; entry: UniProtEntry; nucleotide: UniProtNucleotide[]; url: string; license: string; note: string; fasta?: string }> {
  const response = await api.get(`/vectors/import/uniprot/${encodeURIComponent(accession)}`, {
    params: { with_fasta: withFasta }
  })
  return response.data
}

// 图谱页「另存为载体」（需管理员）：序列与特征由图谱页提交，后端不再外呼，
// 保证保存的与用户眼前看到的是同一份数据。
