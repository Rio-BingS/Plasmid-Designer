/**
 * 图谱端点封装（契约 plasmid-map/1，前缀 /api/map，功能开关 map）。
 * 复用全站 axios 实例（会话 Cookie + CSRF 头 + 401 处理）。
 */
import { formatApiError, http } from '@/api'
import type {
  AnalyzeRequest, EnsemblImportRequest, EnzymeDetail, EnzymeSet, EnzymeSupplier, MapDocument, NcbiImportRequest,
  OnlineSourcesPayload, ParseOptions, SnapgeneImportRequest, SnapgenePlasmid, SnapgeneSet, Topology,
} from './types'
import { clearEnzymeDetails, rememberEnzymeDetail } from './enzymeMeta'

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

/** 按 NCBI accession（如 L09137、M77789.1）导入 GenBank 记录 */
export async function importNcbi(req: NcbiImportRequest): Promise<MapDocument> {
  const r = await http.post('/map/import/ncbi', req)
  return r.data
}

// ---------------------------------------------------------------- 在线序列来源

/** 在线来源清单与许可摘要（与载体库页同一份数据） */
export async function getOnlineSources(): Promise<OnlineSourcesPayload> {
  const r = await http.get('/map/import/sources')
  return r.data
}

/** SnapGene 在线库：集合列表（后端与载体库侧共用 1 小时缓存；需部署方开启且已登录） */
export async function getOnlineSnapgeneSets(): Promise<{ count: number; sets: SnapgeneSet[]; license: string }> {
  const r = await http.get('/map/import/snapgene/sets')
  return r.data
}

/** SnapGene 在线库：按质粒名跨集合搜索（冷缓存约 20 秒） */
export async function searchOnlineSnapgene(q: string): Promise<{
  query: string; count: number; truncated: boolean; plasmids: SnapgenePlasmid[]; failed_sets: string[]
}> {
  const r = await http.get('/map/import/snapgene/search', { params: { q }, timeout: 90_000 })
  return r.data
}

/** SnapGene 在线库：集合内全部序列 */
export async function getOnlineSnapgeneIndex(
  setId: string,
): Promise<{ set_id: string; count: number; plasmids: SnapgenePlasmid[] }> {
  const r = await http.get(`/map/import/snapgene/sets/${encodeURIComponent(setId)}`)
  return r.data
}

/** SnapGene 在线库：下载 .dna 并打开成图谱（同参数重复打开走服务端缓存） */
export async function importOnlineSnapgene(req: SnapgeneImportRequest): Promise<MapDocument> {
  const r = await http.post('/map/import/snapgene', req)
  return r.data
}

/** Ensembl：取 DNA 序列（genomic/cds/cdna）并打开成图谱（线性片段） */
export async function importOnlineEnsembl(req: EnsemblImportRequest): Promise<MapDocument> {
  const r = await http.post('/map/import/ensembl', req)
  return r.data
}

/** 前端粗校验 Ensembl ID：ENSG/ENST/ENS… + 数字（后端再严格校验） */
export const ENSEMBL_ID_RE = /^[A-Za-z]{3,6}[A-Za-z0-9_.:-]{0,58}$/

/** 前端粗校验 NCBI 核酸 accession（后端再严格校验）：GenBank / WGS / RefSeq，版本号可选 */
export const NCBI_ACCESSION_RE = /^(?:[A-Z]{1,2}\d{5,8}|[A-Z]{4,6}\d{8,10}|[A-Z]{2}_(?:[A-Z]{2,4})?\d{6,11})(?:\.\d{1,3})?$/

export function normalizeAccession(raw: string): string {
  return raw.replace(/\s+/g, '').toUpperCase()
}

// 酶详情 / 厂商表是静态数据：会话内缓存，同一请求并发只发一次
const enzymeDetailCache = new Map<string, Promise<EnzymeDetail>>()

export function getEnzymeDetail(name: string): Promise<EnzymeDetail> {
  let p = enzymeDetailCache.get(name)
  if (!p) {
    p = http.get(`/map/enzymes/${encodeURIComponent(name)}`).then((r) => {
      rememberEnzymeDetail(r.data)
      return r.data as EnzymeDetail
    })
    p.catch(() => enzymeDetailCache.delete(name))
    enzymeDetailCache.set(name, p)
  }
  return p
}

let suppliersPromise: Promise<EnzymeSupplier[]> | null = null
export function getSuppliers(): Promise<EnzymeSupplier[]> {
  if (!suppliersPromise) {
    suppliersPromise = http.get('/map/suppliers').then((r) => r.data as EnzymeSupplier[])
    suppliersPromise.catch(() => { suppliersPromise = null })
  }
  return suppliersPromise
}

/** 测试用：清空会话缓存 */
export function _resetEnzymeMetaCache(): void {
  enzymeDetailCache.clear()
  clearEnzymeDetails()
  suppliersPromise = null
}

/** MapDocument → GenBank 文本（Blob） */
export async function exportGenbank(doc: MapDocument): Promise<Blob> {
  const r = await http.post('/map/export/genbank', doc, { responseType: 'blob' })
  return r.data
}

export async function getVectorMap(vectorId: string, opts?: Pick<ParseOptions, 'enzyme_set' | 'orf_min_aa'>): Promise<MapDocument> {
  const url = `/vectors/${encodeURIComponent(vectorId)}/map`
  const r = opts ? await http.get(url, { params: parseParams(opts) }) : await http.get(url)
  return r.data
}

export async function getDesignMap(designId: string): Promise<MapDocument> {
  const r = await http.get(`/design/${encodeURIComponent(designId)}/map`)
  return r.data
}

// ---------------------------------------------------------------- 上传限制与错误

/** 上传上限（MB）：后端 MAP_MAX_UPLOAD_MB 默认 5，且未暴露查询端点，前端按默认值预检 */
export const MAP_UPLOAD_LIMIT_MB = 5
/** 后端可直接解析的文件扩展名 */
export const MAP_FILE_EXTS = ['.gb', '.gbk', '.genbank', '.gbff', '.ape', '.fa', '.fasta', '.fas', '.fna', '.fsa', '.dna']
/** 纯文本：前端读出内容后按粘贴文本解析 */
export const MAP_TEXT_EXTS = ['.txt', '.seq']
export const MAP_ACCEPT = [...MAP_FILE_EXTS, ...MAP_TEXT_EXTS].join(',')

export function fileExt(name: string): string {
  const i = name.lastIndexOf('.')
  return i < 0 ? '' : name.slice(i).toLowerCase()
}

/** 上传前本地预检；返回中文错误或 null */
export function checkMapFile(file: Pick<File, 'name' | 'size'>, limitMb = MAP_UPLOAD_LIMIT_MB): string | null {
  const ext = fileExt(file.name)
  if (![...MAP_FILE_EXTS, ...MAP_TEXT_EXTS].includes(ext)) {
    return `不支持的文件格式${ext ? `（${ext}）` : ''}：请上传 GenBank（.gb/.gbk）、FASTA（.fa/.fasta）、SnapGene（.dna）或纯文本（.txt）`
  }
  if (file.size > limitMb * 1024 * 1024) return `文件过大（上限 ${limitMb} MB），请截取后再打开`
  if (file.size === 0) return '文件为空'
  return null
}

/** 图谱请求错误 → 中文提示（413/415/422 有专门文案，后端中文 detail 优先附上） */
export function describeMapError(e: unknown, limitMb = MAP_UPLOAD_LIMIT_MB): string {
  const err = e as { response?: { status?: number; data?: { detail?: unknown } } }
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  const zh = typeof detail === 'string' && /[\u4e00-\u9fff]/.test(detail) ? detail : ''
  if (status === 413) return zh || `文件过大（上限 ${limitMb} MB），请截取后再打开`
  if (status === 415) return zh || '不支持的文件格式：请上传 GenBank、FASTA、SnapGene（.dna）或纯文本序列'
  // NCBI 导入：未找到 / NCBI 不可用 / 限流 / 超时，后端给中文 detail
  if (status === 404 || status === 502 || status === 503 || status === 504) {
    if (zh) return zh
  }
  if (status === 422) {
    if (zh && /accession/i.test(zh)) return zh
    if (zh) return `序列无法解析：${zh}`
    return '序列无法解析：请检查是否为有效的 DNA 序列（A/C/G/T 及 IUPAC 简并碱基）或完整的 GenBank/FASTA 文本'
  }
  return formatApiError(e, '图谱解析失败')
}
