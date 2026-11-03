/**
 * 双酶切示意图：画的是**成品构建体**
 *
 * 用户反馈过「图里还留着该被切掉的片段」——真实质粒上那段已经不存在了，
 * 所以预览只画构建体：基因箭头（方向=插入链）、两个复原的切点、附近的启动子
 * （方向=启动子自身 strand）。这里锁住三件事：文字结论、位点/启动子标注、
 * 自动判不出时提示用户手选。
 */
import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import RestrictionPreview from '@/components/RestrictionPreview.vue'
import type { RestrictionPreview as PreviewData } from '@/types'

/** 图①（酶切位点分布）切点竖线的 x 坐标（viewBox 系；刻度线 y 固定 61→75） */
function ticksOfFigure1(wrapper: VueWrapper): number[] {
  const svg1 = wrapper.findAll('svg').find((s) => s.attributes('aria-label') === '酶切位点分布')
  expect(svg1).toBeTruthy()
  return svg1!.findAll('line')
    .filter((l) => l.attributes('y1') === '61' && l.attributes('y2') === '75')
    .map((l) => Number(l.attributes('x1')))
}

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
    // 2026-11-01 标签改为文字直排（无括号、无盒子）
    expect(text).toContain('EcoRI 952')
    for (const cut of [1228, 2136, 2208, 3003]) {
      expect(text).toContain(`SphI ${cut}`)
    }
    expect(text.match(/SphI \d/g)!.length).toBeGreaterThanOrEqual(4)
    // 2026-10-31 用户反馈：结合上下排布与左右错开——标签就近贴自己的刻度，
    // 近距切点落到不同行（或同行左右让位），两个标签位置必须错开
    const labels = wrapper.findAll('text').map((t) => ({
      x: Number(t.attributes('x')), y: t.attributes('y'), text: t.text(),
    })).filter((t) => t.text.includes('2136') || t.text.includes('2208'))
    expect(labels).toHaveLength(2)
    expect(labels[0].x !== labels[1].x || labels[0].y !== labels[1].y).toBe(true)
    // 2026-11-01 改用宽度比例尺后刻度坐标非线性：不变量改为
    // 「同行标签必须互不重叠（中心距 ≥ 芯片宽 70px），异行则天然错开」
    if (labels[0].y === labels[1].y) {
      expect(Math.abs(labels[0].x - labels[1].x)).toBeGreaterThanOrEqual(70)
    } else {
      expect(true).toBe(true)
    }
  })

  it('无回收带（cut_options 为空）时分布图退化为纯线性比例', () => {
    // 2026-11-04 goal 验证项：vecScale 的两轮分配只服务带区场景；
    // 没有可选带区时切点位置必须平滑退化回纯 bp 线性映射，不许残留加权
    const wrapper = mount(RestrictionPreview, {
      props: { preview: fixture({
        sites: [
          { role: 'five', enzyme: 'EcoRI', position: 952, cut: 952, end: 957, junction: true },
          { role: 'three', enzyme: 'SphI', position: 2669, cut: 2669, end: 2674, junction: true },
        ],
        gene_5prime_enzyme: 'EcoRI', gene_3prime_enzyme: 'SphI',
        cut_marks: [
          { enzyme: 'EcoRI', vector_cut: 952, in_excised: false, side: 'left' },
          { enzyme: 'SphI', vector_cut: 2669, in_excised: false, side: 'right' },
        ],
      }) },
    })
    const ticks = ticksOfFigure1(wrapper)
    expect(ticks).toHaveLength(2)
    // linX：VEC_X1=90 起，(cut-1)/5422 × 850px
    const lin = (cut: number) => 90 + ((cut - 1) / 5422) * 850
    expect(Math.abs(ticks[0] - lin(952))).toBeLessThan(1)
    expect(Math.abs(ticks[1] - lin(2669))).toBeLessThan(1)
  })

  it('仅一条跨原点带（2 个带区段）：封顶放宽到池/n，两段各半、无振荡', () => {
    // 2026-11-04 审核发现：2 段带区各 ≤45% 数学上不可行（2×45%<100%），
    // 旧实现两段互踢振荡耗尽 5 轮，终态 [374,306] 违反封顶 22%。
    // 修复后封顶放宽到 池/2=340：初分配 [235,445] → 一轮收敛 [340,340]。
    // 灰轨 1717bp → 占比 0.087 → 兜底 20% = 170px。
    const wrapper = mount(RestrictionPreview, {
      props: { preview: fixture({
        sites: [
          { role: 'five', enzyme: 'EcoRI', position: 952, cut: 952, end: 957, junction: true },
          { role: 'three', enzyme: 'SphI', position: 2669, cut: 2669, end: 2674, junction: true },
        ],
        gene_5prime_enzyme: 'EcoRI', gene_3prime_enzyme: 'SphI',
        cut_marks: [
          { enzyme: 'EcoRI', vector_cut: 952, in_excised: false, side: 'left' },
          { enzyme: 'SphI', vector_cut: 2669, in_excised: false, side: 'right' },
        ],
        cut_options: [
          { left_enzyme: 'EcoRI', left_cut: 952, right_enzyme: 'SphI', right_cut: 2669, removed_bp: 1717, kept_bp: 3705 },
        ],
      }) },
    })
    const ticks = ticksOfFigure1(wrapper)
    expect(ticks).toHaveLength(2)
    expect(Math.abs(ticks[0] - 430)).toBeLessThan(0.6) // 90 + 340
    expect(Math.abs(ticks[1] - 600)).toBeLessThan(0.6) // 430 + 170（灰）→ 右臂 340 到 850
  })

  it('仅一条内段带（1 个带区段）：封顶放宽到整池，占满带区池不留洞', () => {
    // 2026-11-04 审核发现：唯一带区段初分配 552.5px 触 45% 封顶(248.6)后被
    // 锁死且无自由段反哺 → 尾部留 ~300px 无映射空洞。修复后 n=1 封顶=整池。
    // 灰轨 5145bp → 占比 0.469 → 上限 35% = 297.5px；带区池 552.5px。
    const wrapper = mount(RestrictionPreview, {
      props: { preview: fixture({
        sites: [
          { role: 'five', enzyme: 'EcoRI', position: 952, cut: 952, end: 957, junction: true },
          { role: 'three', enzyme: 'SphI', position: 1228, cut: 1228, end: 1233, junction: true },
        ],
        gene_5prime_enzyme: 'EcoRI', gene_3prime_enzyme: 'SphI',
        cut_marks: [
          { enzyme: 'EcoRI', vector_cut: 952, in_excised: false, side: 'left' },
          { enzyme: 'SphI', vector_cut: 1228, in_excised: false, side: 'right' },
        ],
        cut_options: [
          { left_enzyme: 'SphI', left_cut: 1228, right_enzyme: 'EcoRI', right_cut: 952, removed_bp: 5146, kept_bp: 276 },
        ],
      }) },
    })
    const ticks = ticksOfFigure1(wrapper)
    expect(ticks).toHaveLength(2)
    expect(Math.abs(ticks[0] - 145)).toBeLessThan(0.6) // 90 + 55（左臂灰 951bp 线性份额）
    expect(Math.abs(ticks[1] - 697.5)).toBeLessThan(0.6) // 145 + 552.5（唯一带区段拿满池）
  })

  it('图③基因段夹取 30%~70%：极小基因触下限，箭头永不越上限', () => {
    // 2026-11-04 用户定案：图③是结构示意图不保比例，基因段显示占比
    // 夹取 [30%,70%]。默认 fixture 基因 765bp / 窗口 ~8673bp（KanR 撑宽）
    // 真实占比 ~9% → 抬到 30%；巨大基因构造验证箭头永不越过 70%。
    const usable = 1000 - 28 * 2
    const small = mount(RestrictionPreview, { props: { preview: fixture() } })
    const vm1 = small.vm as unknown as { geneX1: number; geneX2: number }
    expect(vm1.geneX2 - vm1.geneX1).toBeGreaterThanOrEqual(usable * 0.3 - 1)

    const big = mount(RestrictionPreview, {
      props: { preview: fixture({
        gene: { start: 200, end: 4800, length: 4600, estimated: false },
        sites: [
          { role: 'five', enzyme: 'NdeI', position: 192, cut: 193, end: 198 },
          { role: 'three', enzyme: 'XhoI', position: 4808, cut: 4801, end: 4806 },
        ],
      }) },
    })
    const vm2 = big.vm as unknown as { geneX1: number; geneX2: number }
    expect(vm2.geneX2 - vm2.geneX1).toBeLessThanOrEqual(usable * 0.7 + 1)
    expect(vm1.geneX1).toBeLessThan(vm1.geneX2)
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
