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

  it('CircularMap primers: plain text label + position range (SnapGene map view style)', async () => {
    // SnapGene 图谱视图：引物 = 细弧段 + 环外纯文字 + 位置范围 "(234 .. 260)"，
    // 不用名牌色块（那是序列视图的画法；v2.33.x 曾张冠李戴）。
    // 优先级与普通特征同级，20+ oligo 放不下自动隐藏（v2.33.0 教训）
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/CircularMap.vue', 'utf-8')
    expect(src).toContain("kind: 'text', color: '#2f6aa8'")
    expect(src).toContain("(${p.pos})")
    expect(src).not.toContain("kind: 'chip', fill: '#4a90d9'")
    expect(src).not.toContain('priority: 5e7')
    expect(src).not.toContain('internalArcLabel(p.name')
    // 引物轨道保持原版细弧带（无 13px 加高）
    expect(src).toContain('lane * 6')
  })
})
