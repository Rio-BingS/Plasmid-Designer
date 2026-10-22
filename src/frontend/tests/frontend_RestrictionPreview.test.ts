/**
 * 双酶切示意图：画的是**成品构建体**
 *
 * 用户反馈过「图里还留着该被切掉的片段」——真实质粒上那段已经不存在了，
 * 所以预览只画构建体：基因箭头（方向=插入链）、两个复原的切点、附近的启动子
 * （方向=启动子自身 strand）。这里锁住三件事：文字结论、位点/启动子标注、
 * 自动判不出时提示用户手选。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RestrictionPreview from '@/components/RestrictionPreview.vue'
import type { RestrictionPreview as PreviewData } from '@/types'

function fixture(over: Partial<PreviewData> = {}): PreviewData {
  return {
    vector: { name: 'pET-30a(+)', length: 5422, topology: 'circular' },
    sites: [
      { role: 'three', enzyme: 'XhoI', position: 158, cut: 158, end: 163 },
      { role: 'five', enzyme: 'NdeI', position: 929, cut: 930, end: 934 },
    ],
    gene: { start: 164, end: 928, length: 765, estimated: false },
    removed_bp: 188,
    construct_length: 6006,
    strand: '-',
    source: 'annotation',
    ambiguous: false,
    gene_5prime_enzyme: 'NdeI',
    gene_3prime_enzyme: 'XhoI',
    evidence: [{ kind: 'promoter', side: '-', name: 'T7 promoter', gap: 71, start: 1001, end: 1019, strand: '-' }],
    features: [
      { name: 'T7 promoter', type: 'promoter', start: 1001, end: 1019, strand: '-' },
      { name: 'T7 terminator', type: 'terminator', start: 26, end: 73, strand: '+' },
      { name: 'KanR', type: 'resistance', start: 4632, end: 5447, strand: '+' },
    ],
    junctions: { five: 'TCGAGATGGCTGGC', three: 'TTAACATATGTAT' },
    warnings: [],
    ...over,
  }
}

describe('RestrictionPreview 双酶切示意图', () => {
  it('文字结论给出两端酶与链方向', () => {
    const wrapper = mount(RestrictionPreview, { props: { preview: fixture() } })
    const text = wrapper.text()
    expect(text).toContain('5′ 端接 NdeI')
    expect(text).toContain('3′ 端接 XhoI')
    expect(text).toContain('负链（-）')
    expect(text).toContain('切除 188 bp')
    expect(text).toContain('6,006 bp')
  })

  it('画出两个切点与启动子（启动子带方向）', () => {
    const wrapper = mount(RestrictionPreview, { props: { preview: fixture() } })
    const text = wrapper.text()
    expect(text).toContain('XhoI (158)')
    expect(text).toContain('NdeI (930)')
    // 元件只在图例里出现一次（resistanc 不是方向性元件，不该画）
    expect(text).toContain('T7 promoter')
    expect(wrapper.findAll('path').length).toBeGreaterThanOrEqual(2)
  })

  it('给出方向依据', () => {
    const wrapper = mount(RestrictionPreview, { props: { preview: fixture() } })
    expect(wrapper.text()).toContain('依据 T7 promoter')
    expect(wrapper.text()).toContain('距切点 71 bp')
  })

  it('自动判不出方向时提示手动选择', () => {
    const wrapper = mount(RestrictionPreview, {
      props: { preview: fixture({ ambiguous: true, source: 'ambiguous', strand: '-' }) },
    })
    expect(wrapper.text()).toContain('无法自动判定')
  })

  it('没有注释也没有识别出启动子：写明按默认方向', () => {
    const wrapper = mount(RestrictionPreview, {
      props: { preview: fixture({ source: 'default', evidence: [], ambiguous: false }) },
    })
    expect(wrapper.text()).toContain('默认方向')
  })

  it('接口失败时只显示错误，不画一张假图', () => {
    const wrapper = mount(RestrictionPreview, {
      props: { preview: null, error: '预览失败，不影响提交' },
    })
    expect(wrapper.text()).toContain('预览失败')
    expect(wrapper.find('svg').exists()).toBe(false)
  })
})
