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

  it('多切点酶在分布图上全部标出、全部带酶名，近距切点防重叠分行', () => {
    // 2026-10-29 用户报告：SphI 4 个切点只画 1 个；修正后全部标「酶 (坐标)」
    // 并分行。2026-10-31 三图方案：全部切点在分布图标注，主图（构建
    // 体）只画 junction——构建体上其它切点已随废片段消失。
    const wrapper = mount(RestrictionPreview, {
      props: { preview: fixture({
        sites: [
          { role: 'five', enzyme: 'EcoRI', position: 952, cut: 952, end: 957, junction: true },
          { role: 'three', enzyme: 'SphI', position: 1562, cut: 1562, end: 1567, junction: true },
        ],
        gene_5prime_enzyme: 'EcoRI', gene_3prime_enzyme: 'SphI',
        cut_marks: [
          { enzyme: 'EcoRI', vector_cut: 952, in_excised: false, side: 'left' },
          { enzyme: 'SphI', vector_cut: 1228, in_excised: true, side: 'excised' },
          { enzyme: 'SphI', vector_cut: 2136, in_excised: true, side: 'excised' },
          { enzyme: 'SphI', vector_cut: 2208, in_excised: true, side: 'excised' },
          { enzyme: 'SphI', vector_cut: 3003, in_excised: false, side: 'right' },
        ],
      }) },
    })
    const text = wrapper.text()
    expect(text).toContain('EcoRI (952)')
    for (const cut of [1228, 2136, 2208, 3003]) {
      expect(text).toContain(`SphI (${cut})`)
    }
    expect(text.match(/SphI \(/g)!.length).toBeGreaterThanOrEqual(4)
    // 近距切点分行（2136 与 2208 不同 y）
    const labels = wrapper.findAll('text').map((t) => ({
      y: t.attributes('y'), text: t.text(),
    })).filter((t) => t.text.includes('2136') || t.text.includes('2208'))
    expect(labels).toHaveLength(2)
    expect(labels[0].y).not.toBe(labels[1].y)
  })

  it('骨架重切风险：展示警告横幅与知情勾选，勾选向父组件发 update', async () => {
    const wrapper = mount(RestrictionPreview, {
      props: {
        preview: fixture({
          backbone_cut_risk: true,
          warnings: ['SphI 在保留骨架上还有 3 个切点（1802、1874、2669）：酶切会把骨架切成 4 段'],
        }),
        allowBackboneCuts: false,
      },
    })
    expect(wrapper.text()).toContain('保留骨架上还有 3 个切点')
    expect(wrapper.text()).toContain('有意使用多切点酶切方案')
    const box = wrapper.find('[data-testid="allow-backbone-cuts"]')
    expect(box.exists()).toBe(true)
    await box.setValue(true)
    expect(wrapper.emitted('update:allowBackboneCuts')?.[0]).toEqual([true])
  })

  it('无骨架重切风险时不显示确认区', () => {
    const wrapper = mount(RestrictionPreview, { props: { preview: fixture() } })
    expect(wrapper.find('[data-testid="allow-backbone-cuts"]').exists()).toBe(false)
  })

  it('多切点时显示可回收片段带，点击带经 emit 回传补集切点对', async () => {
    // 2026-10-31 三图方案：cut_options=完整消化的两端异酶带；点击带 = 回收
    // 该带 = 补集切点对。回收主链(3711) → cutPair (952,2669)。
    const wrapper = mount(RestrictionPreview, {
      props: {
        preview: fixture({
          cut_options: [
            { left_enzyme: 'EcoRI', left_cut: 952, right_enzyme: 'SphI', right_cut: 2669,
              removed_bp: 1717, kept_bp: 3711 },
          ],
          cut_marks: [
            { enzyme: 'EcoRI', vector_cut: 952, in_excised: false, side: 'left' },
            { enzyme: 'SphI', vector_cut: 2669, in_excised: false, side: 'right' },
          ],
        }),
        cutPair: null,
      },
    })
    const band = wrapper.find('.rp-band')
    expect(band.exists()).toBe(true)
    expect(wrapper.text()).toContain('回收 3,711 bp')
    await band.trigger('click')
    expect(wrapper.emitted('update:cutPair')?.[0]).toEqual([[952, 2669]])
  })

  it('单条可回收带也显示片段热区（点击即选）', () => {
    const wrapper = mount(RestrictionPreview, {
      props: { preview: fixture({ cut_options: [
        { left_enzyme: 'NdeI', left_cut: 238, right_enzyme: 'XhoI', right_cut: 158,
          removed_bp: 80, kept_bp: 5289 },
      ], cut_marks: [
        { enzyme: 'NdeI', vector_cut: 238, in_excised: false, side: 'left' },
        { enzyme: 'XhoI', vector_cut: 158, in_excised: false, side: 'right' },
      ] }) },
    })
    expect(wrapper.find('.rp-band').exists()).toBe(true)
  })
})
