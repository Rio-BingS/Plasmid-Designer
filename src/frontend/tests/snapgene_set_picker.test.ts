import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SnapgeneSetPicker from '@/components/SnapgeneSetPicker.vue'
import { resolveSnapgeneSet } from '@/utils/snapgeneSet'

const sets = [
  { set_id: 'basic_cloning_vectors', name: 'Basic Cloning Vectors' },
  { set_id: 'yeast_plasmids', name: 'Yeast Plasmids' },
]

describe('resolveSnapgeneSet', () => {
  it('按 ID、名称（不分大小写）、名称（id）解析', () => {
    expect(resolveSnapgeneSet('yeast_plasmids', sets)).toBe('yeast_plasmids')
    expect(resolveSnapgeneSet('  yeast plasmids ', sets)).toBe('yeast_plasmids')
    expect(resolveSnapgeneSet('Basic Cloning Vectors（basic_cloning_vectors）', sets)).toBe('basic_cloning_vectors')
    expect(resolveSnapgeneSet('Yeast_Plasmids', sets)).toBe('yeast_plasmids')
    expect(resolveSnapgeneSet('yeast', sets)).toBe('yeast_plasmids')
  })
  it('列表已加载时，不是任何集合（如质粒名 pet-30a）返回空串', () => {
    expect(resolveSnapgeneSet('pet-30a', sets)).toBe('')
    expect(resolveSnapgeneSet('crispr plasmids', sets)).toBe('')
    expect(resolveSnapgeneSet('<script>', sets)).toBe('')
    expect(resolveSnapgeneSet('', sets)).toBe('')
  })
  it('列表没拿到时，形似 ID 的原样交给后端', () => {
    expect(resolveSnapgeneSet('crispr plasmids', [])).toBe('crispr_plasmids')
    expect(resolveSnapgeneSet('<script>', [])).toBe('')
  })
})

describe('SnapgeneSetPicker', () => {
  it('可手动输入，回车后更新选中集合并触发 change', async () => {
    const w = mount(SnapgeneSetPicker, {
      props: {
        modelValue: 'basic_cloning_vectors', sets,
        'onUpdate:modelValue': (v: string) => w.setProps({ modelValue: v }),
      },
    })
    const input = w.get('input')
    expect((input.element as HTMLInputElement).value).toBe('Basic Cloning Vectors')
    await input.setValue('yeast plasmids')
    await input.trigger('keydown', { key: 'Enter' })
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual(['yeast_plasmids'])
    expect(w.emitted('change')?.length).toBe(1) // setValue 触发 change，回车时值已一致不重复触发
    expect((input.element as HTMLInputElement).value).toBe('Yeast Plasmids')
  })
  it('从候选点选完整名称立即生效', async () => {
    const w = mount(SnapgeneSetPicker, { props: { modelValue: 'basic_cloning_vectors', sets } })
    await w.get('input').setValue('Yeast Plasmids')
    expect(w.emitted('change')?.[0]).toEqual(['yeast_plasmids'])
  })
  it('输入质粒名（不是集合）发出 search，保留原文，同一文字只搜一次', async () => {
    const w = mount(SnapgeneSetPicker, { props: { modelValue: 'basic_cloning_vectors', sets } })
    const input = w.get('input')
    await input.setValue('pet-30a')
    await input.trigger('keydown', { key: 'Enter' })
    expect(w.emitted('search')).toEqual([['pet-30a']])
    expect(w.emitted('change')).toBeUndefined()
    expect((input.element as HTMLInputElement).value).toBe('pet-30a')
  })
  it('无法解析的输入恢复为当前集合', async () => {
    const w = mount(SnapgeneSetPicker, { props: { modelValue: 'basic_cloning_vectors', sets } })
    const input = w.get('input')
    await input.setValue('<>')
    await input.trigger('change')
    expect(w.emitted('change')).toBeUndefined()
    expect((input.element as HTMLInputElement).value).toBe('Basic Cloning Vectors')
  })
})
