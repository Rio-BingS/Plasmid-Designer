import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick, ref } from 'vue'
import ReliableZoneInputs from '@/components/sequencing/ReliableZoneInputs.vue'
import {
  DEFAULT_RELIABLE_ZONE, reliableZoneFormValue, validateReliableZone, type ReliableZone,
} from '@/utils/reliableZone'

describe('read 可信区参数', () => {
  it('默认值发空串（后端走默认），改动后发 4 个数', () => {
    expect(reliableZoneFormValue({ ...DEFAULT_RELIABLE_ZONE })).toBe('')
    expect(reliableZoneFormValue({ ...DEFAULT_RELIABLE_ZONE, tailGray: 650 })).toBe('30,60,650,900')
  })

  it('校验与后端一致', () => {
    expect(validateReliableZone({ ...DEFAULT_RELIABLE_ZONE })).toBe('')
    expect(validateReliableZone({ frontHard: 60, frontGray: 30, tailGray: 700, tailHard: 900 })).toMatch('首端硬区')
    expect(validateReliableZone({ ...DEFAULT_RELIABLE_ZONE, tailHard: 600 })).toMatch('尾端灰区')
    expect(validateReliableZone({ ...DEFAULT_RELIABLE_ZONE, frontHard: -1 })).toMatch('非负整数')
  })

  it('输入框改值、显示错误、恢复默认', async () => {
    const zone = ref<ReliableZone>({ ...DEFAULT_RELIABLE_ZONE })
    const w = mount(ReliableZoneInputs, {
      props: { modelValue: zone.value, 'onUpdate:modelValue': (v: ReliableZone) => { zone.value = v; w.setProps({ modelValue: v }) } },
    })
    const tail = w.get('[data-testid="zone-tail-gray"]')
    ;(tail.element as HTMLInputElement).value = '950'
    await tail.trigger('change')
    await nextTick()
    expect(zone.value.tailGray).toBe(950)
    expect(w.find('[data-testid="zone-error"]').exists()).toBe(true)
    await w.get('.zone-reset').trigger('click')
    await nextTick()
    expect(zone.value).toEqual({ ...DEFAULT_RELIABLE_ZONE })
    expect(w.find('[data-testid="zone-error"]').exists()).toBe(false)
  })
})
