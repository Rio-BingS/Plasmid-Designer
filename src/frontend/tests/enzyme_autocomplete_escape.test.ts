/** EnzymeAutocomplete 不再用 v-html 拼酶名（审查 03-Low 10） */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import EnzymeAutocomplete from '@/components/EnzymeAutocomplete.vue'

describe('EnzymeAutocomplete 不再用 v-html', () => {
  it('酶名里的 HTML 按文本显示，匹配部分仍加下划线', async () => {
    const evil = 'Bsa<img src=x onerror=alert(1)>I'
    const w = mount(EnzymeAutocomplete, { props: { enzymes: [evil, 'BsmBI'], modelValue: '' } })
    await w.find('input').setValue('bs')
    await w.find('input').trigger('focus')
    const opts = w.findAll('.es-name')
    expect(opts.length).toBe(2)
    expect(w.find('.es-dropdown img').exists()).toBe(false)
    expect(opts[0].text()).toBe(evil)
    expect(opts[0].find('u').text()).toBe('Bs')
    expect(opts[1].find('u').text()).toBe('Bs')
  })
})
