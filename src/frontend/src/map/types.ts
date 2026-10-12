/**
 * 质粒图谱契约 plasmid-map/1 的前端镜像（与 work/map-contract.md 逐字段一致）。
 *
 * 坐标一律 1-based 闭区间；环状分子上 start > end 表示跨原点
 * （如 5000 bp 上 start=4800,end=200）。图谱组件只消费 MapDocument。
 */

export type Topology = 'circular' | 'linear'
export type Strand = 1 | -1 | 0

export type FeatureType =
  | 'cds' | 'promoter' | 'terminator' | 'rep_origin' | 'rbs' | 'marker' | 'tag'
  | 'signal_peptide' | 'polyA_signal' | 'enhancer' | 'ltr' | 'primer_bind'
  | 'protein_bind' | 'misc_rna' | 'misc_feature' | 'insert' | 'mcs'

export interface MapSegment { start: number; end: number }

export interface MapFeature {
  id: string
  name: string
  type: FeatureType
  start: number
  end: number
  strand: Strand
  /** join() 的各段；单段时省略 */
  segments?: MapSegment[]
  /** 仅文件/用户指定时有；否则前端按类型配色 */
  color?: string
  source: 'file' | 'auto' | 'design' | 'vector'
  notes?: string
  qualifiers?: Record<string, string>
  /** 仅 source=auto；fragment = coverage < 0.95 */
  auto?: { library: string; identity: number; coverage: number; fragment: boolean }
}

export interface MapEnzymeSite {
  /** 识别序列第一个碱基（1-based） */
  position: number
  /** 切在其后的碱基序号（0..length） */
  cut_top: number
  cut_bottom: number
  strand: 1 | -1
  /** 该位点自身的突出端序列（与酶级 overhang 不同时给出，如 IIS 型酶的实际 4 nt） */
  overhang?: string | null
  /** 该位点被宿主甲基化阻断（dam+/dcm+ 菌株中不切） */
  blocked_by?: MethylationKind[]
}

export type MethylationKind = 'dam' | 'dcm'

export interface MapEnzyme {
  name: string
  recognition: string
  ends: "5'" | "3'" | 'blunt'
  overhang: string | null
  cut_count: number
  sites: MapEnzymeSite[]
  /** 位点过多时 sites 只含部分明细（cut_count 仍为完整计数）；后端仅在截断时给出 true */
  sites_truncated?: boolean
  /** 酶级甲基化敏感性（如 ["dam"]），来自 REBASE（后端可能省略） */
  methylation?: MethylationKind[]
}

export interface MapOrf { start: number; end: number; strand: 1 | -1; frame: 0 | 1 | 2; aa_length: number }

export interface MapPrimer { id: string; name: string; start: number; end: number; strand: 1 | -1; sequence: string }

export interface MapDocument {
  schema: 'plasmid-map/1'
  name: string
  topology: Topology
  length: number
  sequence: string
  features: MapFeature[]
  enzymes: MapEnzyme[]
  orfs: MapOrf[]
  primers: MapPrimer[]
  meta: {
    source_format: 'genbank' | 'fasta' | 'snapgene' | 'raw' | 'design' | 'vector'
    enzyme_set: string
    warnings: string[]
  }
}

export interface EnzymeSet { id: string; name: string; description: string; enzymes: string[] }

export interface AnalyzeRequest {
  sequence: string
  topology: Topology
  name?: string
  features?: MapFeature[]
  primers?: MapPrimer[]
  annotate?: boolean
  enzyme_set?: string
  enzymes?: string[]
  orf_min_aa?: number
}

export interface ParseOptions {
  annotate?: boolean
  enzyme_set?: string
  orf_min_aa?: number
}

/** 1-based 闭区间（start > end 且环状 = 跨原点） */
export interface Range { start: number; end: number }

/** GET /api/vectors 列表项附带的轻量图谱预览（缩略图用，可能缺省） */
export interface MapPreview {
  length: number
  topology: Topology
  features: { name: string; type: string; start: number; end: number; strand: Strand }[]
}

/** GET /api/map/enzymes/{name}：单把酶详情（REBASE / Bio.Restriction） */
export interface EnzymeDetail {
  name: string
  site: string
  recognition: string
  ends: MapEnzyme['ends']
  overhang: string | null
  /** 非回文识别序列、在位点同一侧之外切割 */
  type_iis: boolean
  /** 可商购、切出 4 nt 5' 突出端的 Type IIS 酶（可用于 Golden Gate 引物设计） */
  golden_gate: boolean
  /** REBASE 厂商代码与名称 */
  suppliers: { code: string; name: string }[]
  /** 可商购同切酶（识别序列与切点都相同） */
  isoschizomers: string[]
  /** 可商购新切酶（识别序列相同、切点不同） */
  neoschizomers: string[]
  methylation?: MethylationKind[]
}

/** GET /api/map/suppliers：REBASE 厂商及其可供的酶 */
export interface EnzymeSupplier { code: string; name: string; enzymes: string[] }

export interface NcbiImportRequest {
  accession: string
  annotate?: boolean
  enzyme_set?: string
  orf_min_aa?: number
}

// ==================== 在线序列来源（SnapGene 在线库 / Ensembl） ====================
// 许可提示随 /api/map/import/sources 下发；Addgene 后端不代登录，本页不提供其入口

export interface OnlineSourceInfo {
  id: string
  name: string
  kind: string
  url: string
  license: string
  importable: boolean
  note: string
}

export interface OnlineSourcesPayload {
  enabled: boolean
  /** SnapGene 在线库开关（默认 false）；开启后也只对已登录用户开放 */
  snapgene_online_enabled?: boolean
  disclaimer: string
  sources: OnlineSourceInfo[]
}

export interface SnapgeneSet { set_id: string; name: string }

/** 集合索引里的条目：plasmid_id 是下载用的 ID，filename 是落盘名（更接近真实质粒名） */
export interface SnapgenePlasmid {
  plasmid_id: string
  name: string
  filename: string
  index: number
}

export interface SnapgeneImportRequest {
  set_id: string
  plasmid_id: string
  annotate?: boolean
  enzyme_set?: string
  orf_min_aa?: number
}

export interface EnsemblImportRequest {
  identifier: string
  seq_type: 'genomic' | 'cds' | 'cdna'
  annotate?: boolean
  enzyme_set?: string
  orf_min_aa?: number
}

