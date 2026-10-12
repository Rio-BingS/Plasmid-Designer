/**
 * 已取到的酶详情（GET /api/map/enzymes/{name}）的同步缓存：api.ts 写入，
 * 悬停提示（hits.ts）只读。单独成模块，hits 不必依赖 axios 封装。
 */
import type { EnzymeDetail } from './types'

const details = new Map<string, EnzymeDetail>()

export function rememberEnzymeDetail(d: EnzymeDetail): void {
  details.set(d.name, d)
}

export function cachedEnzymeDetail(name: string): EnzymeDetail | undefined {
  return details.get(name)
}

export function clearEnzymeDetails(): void {
  details.clear()
}

/** 厂商名缩写（悬停提示里放不下全名） */
const SHORT: Record<string, string> = {
  N: 'NEB', B: 'Thermo', I: 'SibEnzyme', V: 'Vivantis', K: 'Takara', J: 'Nippon Gene', M: 'Roche',
  X: 'EURx', Q: 'CHIMERx', C: 'Minotech', R: 'Promega', S: 'Sigma', O: 'Toyobo', Y: 'SinaClon', E: 'Agilent',
}

export function supplierShortName(s: { code: string; name: string }): string {
  return SHORT[s.code] ?? s.name
}

/** 悬停提示的附加行：Type IIS / Golden Gate 与厂商摘要 */
export function enzymeDetailLines(d: EnzymeDetail, maxSuppliers = 4): string[] {
  const lines: string[] = []
  const tags = [d.type_iis && 'Type IIS', d.golden_gate && '可用于 Golden Gate'].filter(Boolean)
  if (tags.length) lines.push(tags.join(' · '))
  if (d.suppliers.length) {
    const names = d.suppliers.map(supplierShortName)
    const head = names.slice(0, maxSuppliers).join('、')
    lines.push(`厂商：${head}${names.length > maxSuppliers ? ` 等 ${names.length} 家` : ''}`)
  }
  return lines
}
