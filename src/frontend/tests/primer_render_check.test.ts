// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

describe('primer SnapGene style markers', () => {
  it('SequenceMap primers: in-bar label, SnapGene blue, no floating badge', async () => {
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/SequenceMap.vue', 'utf-8')
    expect(src).toContain("'#4a90d9'")
    // 名牌悬空结构不应存在（曾压住序列行）
    expect(src).not.toContain("kind: 'primer'")
    expect(src).not.toContain('nameFits')
    // 名字在条形内（fitsInside 判定）
    expect(src).toContain('fitsInside(p.name, 10')
  })

  it('CircularMap primers: SnapGene map style — magenta name, coords behind a Locations toggle', async () => {
    // 对照 SnapGene 官方文档（Show or Hide Restriction Site or Primer Coordinates on a Map）
    // 与官方截图：引物标签 = 环外洋红纯文字 + 位置由「位置」开关统一控制；
    // 绝不截断坐标（`(1434 .. 14…` 比不写更糟），也绝不整体隐藏引物
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/CircularMap.vue', 'utf-8')
    const mod = readFileSync('src/map/core/primer_labels.ts', 'utf-8')
    expect(src).toContain("kind: 'primer'")
    expect(src).toContain('PRIMER_LABEL_COLOR')
    expect(src).toContain('buildPrimerLabel')
    expect(mod).toContain("PRIMER_LABEL_COLOR = '#a21caf'")
    // 坐标放不下时整段省略，不截断
    expect(mod).toContain('fitsPos ? p.name + posText : p.name')
    expect(src).toContain('store.show.locations')
    // 不得再出现 v2.33.4 的「超上限整体省略」与更早的名牌/沿弧方案
    expect(src).not.toContain('MAX_PRIMER_LABELS')
    expect(src).not.toContain("kind: 'chip', fill: '#4a90d9'")
    expect(src).not.toContain('priority: 5e7')
    expect(src).not.toContain('internalArcLabel(p.name')
    // 引物轨道保持原版细弧带（无 13px 加高）
    expect(src).toContain('lane * 6')
  })

  it('MapViewer exposes the Locations toggle (SnapGene Show Locations)', async () => {
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/MapViewer.vue', 'utf-8')
    expect(src).toContain("{ key: 'locations', label: '位置' }")
  })
})
