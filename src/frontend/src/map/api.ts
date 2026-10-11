/**
 * 图谱端点封装（契约 plasmid-map/1，前缀 /api/map，功能开关 map）。
 * 复用全站 axios 实例（会话 Cookie + CSRF 头 + 401 处理）。
 */
import { http } from '@/api'
import type { AnalyzeRequest, EnzymeSet, MapDocument, ParseOptions, Topology } from './types'

function parseParams(opts: ParseOptions): Record<string, string | number | boolean> {
  const p: Record<string, string | number | boolean> = {}
  if (opts.annotate !== undefined) p.annotate = opts.annotate
  if (opts.enzyme_set) p.enzyme_set = opts.enzyme_set
  if (opts.orf_min_aa !== undefined) p.orf_min_aa = opts.orf_min_aa
  return p
}

/** 上传文件解析（GenBank/FASTA/SnapGene .dna） */
export async function parseMapFile(file: File, opts: ParseOptions = {}): Promise<MapDocument> {
  const form = new FormData()
  form.append('file', file, file.name)
  const r = await http.post('/map/parse', form, { params: parseParams(opts) })
  return r.data
}

/** 粘贴文本解析（原始序列 / FASTA / GenBank 文本） */
export async function parseMapText(
  text: string,
  extra: { name?: string; topology?: Topology } = {},
  opts: ParseOptions = {},
): Promise<MapDocument> {
  const r = await http.post('/map/parse', { text, ...extra }, { params: parseParams(opts) })
  return r.data
}

/** 对（编辑后的）序列/特征重算酶切、ORF、自动注释 */
export async function analyzeMap(req: AnalyzeRequest): Promise<MapDocument> {
  const r = await http.post('/map/analyze', req)
  return r.data
}

export async function getEnzymeSets(): Promise<EnzymeSet[]> {
  const r = await http.get('/map/enzyme-sets')
  return r.data
}

/** MapDocument → GenBank 文本（Blob） */
export async function exportGenbank(doc: MapDocument): Promise<Blob> {
  const r = await http.post('/map/export/genbank', doc, { responseType: 'blob' })
  return r.data
}

export async function getVectorMap(vectorId: string): Promise<MapDocument> {
  const r = await http.get(`/vectors/${encodeURIComponent(vectorId)}/map`)
  return r.data
}

export async function getDesignMap(designId: string): Promise<MapDocument> {
  const r = await http.get(`/design/${encodeURIComponent(designId)}/map`)
  return r.data
}
