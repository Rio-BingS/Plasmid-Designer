import { describe, it, expect } from 'vitest'
import type { MapDocument } from '@/map/types'
import { addFeature, deleteFeature, nextFeatureId, pushHistory, updateFeature, validateRange } from '@/map/core/edit'
import pUC19 from '../fixtures/map/pUC19.json'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

describe('edit：特征编辑（纯函数）', () => {
  it('重命名/改色不改原文档；空名保留旧名；color="" 恢复类型配色', () => {
    const d = puc()
    const f = d.features[0]
    const d2 = updateFeature(d, f.id, { name: '  新名  ', color: '#ff0000' })
    expect(d.features[0].name).toBe(f.name)
    expect(d2.features[0]).toMatchObject({ name: '新名', color: '#ff0000' })
    expect(updateFeature(d2, f.id, { name: '   ' }).features[0].name).toBe('新名')
    expect(updateFeature(d2, f.id, { color: '' }).features[0].color).toBeUndefined()
    expect(updateFeature(d, 'nope', { name: 'x' })).toBe(d)
  })

  it('编辑自动注释特征后转为 source=file 并去掉 auto', () => {
    const d = puc()
    d.features[0] = { ...d.features[0], source: 'auto', auto: { library: 'x', identity: 1, coverage: 1, fragment: false } }
    const f = updateFeature(d, d.features[0].id, { name: 'AmpR*' }).features[0]
    expect(f.source).toBe('file')
    expect(f.auto).toBeUndefined()
  })

  it('删除', () => {
    const d = puc()
    const id = d.features[1].id
    const d2 = deleteFeature(d, id)
    expect(d2.features.find((f) => f.id === id)).toBeUndefined()
    expect(d2.features).toHaveLength(d.features.length - 1)
  })

  it('从选区新增：唯一 id、跨原点（环状）、线性拒绝反向、空名报错', () => {
    const d = puc()
    const d2 = addFeature(d, { start: 2600, end: 50 }, { name: 'wrap', type: 'misc_feature', strand: 1 })
    const f = d2.features[d2.features.length - 1]
    expect(f).toMatchObject({ id: 'user-1', name: 'wrap', start: 2600, end: 50, strand: 1, source: 'file' })
    expect(nextFeatureId(d2)).toBe('user-2')
    expect(validateRange({ ...d, topology: 'linear' }, { start: 2600, end: 50 })).toContain('线性')
    expect(validateRange(d, { start: 0, end: 5 })).toContain('1..')
    expect(validateRange(d, null)).toContain('拖选')
    expect(() => addFeature(d, { start: 1, end: 5 }, { name: ' ', type: 'cds', strand: 1 })).toThrow('名称')
  })

  it('撤销栈有上限', () => {
    let st: number[] = []
    for (let i = 0; i < 40; i++) st = pushHistory(st, i, 30)
    expect(st).toHaveLength(30)
    expect(st[29]).toBe(39)
  })
})
