// TypeScript 类型定义

export type SequenceType = 'amino_acid' | 'dna'
export type CloningMethod = 'gibson' | 'golden_gate' | 'restriction' | 'gene_synthesis'
export type DesignStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface DesignRequest {
  sequence: string
  sequence_type: SequenceType
  sequence_name: string
  vector_id: string
  cloning_method: CloningMethod
  optimize_codons: boolean
  target_species: string
  gc_min: number
  gc_max: number
  homology_arm: number
  enzyme: string
  oligo_length?: number
  overlap_length?: number
  include_report: boolean
  protocol_language?: 'zh' | 'en'
}

export interface PrimerInfo {
  name: string
  sequence: string
  full_sequence: string
  tm: number
  gc_content: number
  length: number
  overhang?: string
  notes?: string
}

export interface DesignResult {
  design_id: string
  status: DesignStatus
  input_sequence: string
  optimized_sequence?: string
  construct_sequence?: string
  construct_features?: { name: string; type: string; start: number; end: number; strand?: string; description?: string }[]
  insert_start?: number
  insert_end?: number
  cai?: number
  gc_content?: number
  vector_id: string
  vector_name: string
  final_length?: number
  primers: PrimerInfo[]
  cloning_method: CloningMethod
  clone_protocol?: string
  validation_passed: boolean
  warnings: string[]
  errors: string[]
  created_at: string
  completed_at?: string
}

export interface VectorInfo {
  id: string
  name: string
  source: string
  vector_type: string
  host: string[]
  antibiotic_resistance: string[]
  copy_number: string
  description: string
  features: { name: string; type: string }[]
  mcs_enzymes: string[]
}

export interface CodonTable {
  id: string
  name: string
  file: string
  species?: string
}
