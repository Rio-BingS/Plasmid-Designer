/** 序列工具：互补、切片（跨原点）、翻译、GC、Tm */
import type { MapFeature, Range } from '../types'
import { isWrap, splitRange } from './coords'

const COMP: Record<string, string> = {
  A: 'T', T: 'A', G: 'C', C: 'G', U: 'A', R: 'Y', Y: 'R', S: 'S', W: 'W', K: 'M', M: 'K',
  B: 'V', V: 'B', D: 'H', H: 'D', N: 'N', '-': '-',
}

export function complement(s: string): string {
  let out = ''
  for (const c of s.toUpperCase()) out += COMP[c] ?? 'N'
  return out
}

export function reverseComplement(s: string): string {
  return complement(s).split('').reverse().join('')
}

/** 取 1-based 闭区间（环状 start > end 跨原点） */
export function sliceRange(seq: string, r: Range): string {
  if (!isWrap(r)) return seq.slice(r.start - 1, r.end)
  return seq.slice(r.start - 1) + seq.slice(0, r.end)
}

const AA = 'FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG'
const IDX: Record<string, number> = { T: 0, C: 1, A: 2, G: 3 }

/** 标准密码子表翻译；含模糊碱基的密码子译为 X */
export function translate(dna: string): string {
  let out = ''
  const s = dna.toUpperCase().replace(/U/g, 'T')
  for (let i = 0; i + 3 <= s.length; i += 3) {
    const a = IDX[s[i]], b = IDX[s[i + 1]], c = IDX[s[i + 2]]
    out += a === undefined || b === undefined || c === undefined ? 'X' : AA[a * 16 + b * 4 + c]
  }
  return out
}

export interface FeatureTranslation {
  protein: string
  /** 第 i 个氨基酸对应密码子中间碱基的序列位置（1-based），用于在序列视图对位 */
  codonCenters: number[]
}

/** CDS 翻译（join 段、跨原点、反链均支持） */
export function translateFeature(f: MapFeature, seq: string): FeatureTranslation {
  const L = seq.length
  const segs: Range[] = f.segments?.length ? f.segments : [{ start: f.start, end: f.end }]
  const positions: number[] = []
  for (const s of segs) {
    for (const part of splitRange(s, L)) {
      for (let p = part.start; p <= part.end; p++) positions.push(p)
    }
  }
  if (f.strand === -1) positions.reverse()
  let dna = ''
  for (const p of positions) dna += seq[p - 1] ?? 'N'
  if (f.strand === -1) dna = complement(dna)
  const protein = translate(dna)
  const codonCenters: number[] = []
  for (let i = 0; i < protein.length; i++) codonCenters.push(positions[i * 3 + 1])
  return { protein, codonCenters }
}

export function gcPercent(s: string): number {
  let gc = 0, n = 0
  for (const c of s.toUpperCase()) {
    if (c === 'G' || c === 'C' || c === 'S') { gc++; n++ } else if (c === 'A' || c === 'T' || c === 'W') n++
  }
  return n ? (gc / n) * 100 : 0
}

// 最近邻参数：SantaLucia 1998 unified（ΔH kcal/mol, ΔS cal/K·mol）
const NN: Record<string, [number, number]> = {
  AA: [-7.9, -22.2], TT: [-7.9, -22.2], AT: [-7.2, -20.4], TA: [-7.2, -21.3],
  CA: [-8.5, -22.7], TG: [-8.5, -22.7], GT: [-8.4, -22.4], AC: [-8.4, -22.4],
  CT: [-7.8, -21.0], AG: [-7.8, -21.0], GA: [-8.2, -22.2], TC: [-8.2, -22.2],
  CG: [-10.6, -27.2], GC: [-9.8, -24.4], GG: [-8.0, -19.9], CC: [-8.0, -19.9],
}

export const TM_CONDITIONS = { naMolar: 0.05, oligoMolar: 250e-9 }

/**
 * 熔解温度（°C）：
 * - < 14 nt：Wallace 规则 2(A+T) + 4(G+C)；
 * - 14~60 nt：最近邻（SantaLucia 1998），[Na+] 50 mM、引物 250 nM，
 *   盐校正 ΔS += 0.368·(N-1)·ln[Na+]；
 * - > 60 nt：经验式 81.5 + 16.6·log10[Na+] + 0.41·GC% − 675/N。
 * 模糊碱基不参与计算。
 */
export function meltingTemp(seqIn: string): number {
  const s = seqIn.toUpperCase().replace(/[^ACGT]/g, '')
  const n = s.length
  if (!n) return 0
  const gc = (s.match(/[GC]/g) || []).length
  if (n < 14) return 2 * (n - gc) + 4 * gc
  if (n > 60) return 81.5 + 16.6 * Math.log10(TM_CONDITIONS.naMolar) + 0.41 * (gc / n) * 100 - 675 / n
  let dH = 0, dS = 0
  for (const end of [s[0], s[n - 1]]) {
    if (end === 'G' || end === 'C') { dH += 0.1; dS += -2.8 } else { dH += 2.3; dS += 4.1 }
  }
  for (let i = 0; i < n - 1; i++) {
    const p = NN[s.slice(i, i + 2)]
    dH += p[0]
    dS += p[1]
  }
  if (s === reverseComplement(s)) dS += -1.4
  dS += 0.368 * (n - 1) * Math.log(TM_CONDITIONS.naMolar)
  const R = 1.987
  const ct = s === reverseComplement(s) ? TM_CONDITIONS.oligoMolar : TM_CONDITIONS.oligoMolar / 4
  return (dH * 1000) / (dS + R * Math.log(ct)) - 273.15
}

export interface SelectionStats { length: number; gc: number; tm: number }

export function selectionStats(seq: string, r: Range): SelectionStats {
  const s = sliceRange(seq, r)
  return { length: s.length, gc: gcPercent(s), tm: meltingTemp(s) }
}
