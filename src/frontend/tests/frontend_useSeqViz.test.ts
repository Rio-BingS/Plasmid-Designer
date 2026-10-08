/**
 * useSeqViz composable 独立单测（不经 SequencingPanel 组件）
 *
 * 覆盖峰图融合视图核心逻辑：
 * - 峰图缓存 / 在途去重 / 失败缓存
 * - 勾选 toggleRead 与取消后选中回退
 * - 跳转（jumpToRefPos / jumpToVariant）与证据行 composeSeqInfo
 * - 缩放（钳制 / 中心锚 / 点锚 / 适应全宽）与滚动同步
 * - 覆盖简图域（ovDomain）/ 行布局（rowLayouts）
 * - 点选分流（简图命中 read → 选中缩放；空白 → 仅跳列；字母行 → 切 read）
 * - resetSeqViz 状态复位
 *
 * 环境约束：happy-dom 无 canvas 2d（drawSeq 直接跳过），clientWidth=0 时
 * 视窗视为全参考；需要视口/滚动的分支用假 wrap 元素注入 seqBox ref。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { defineComponent, ref, nextTick } from 'vue'
import { useSeqViz } from '@/composables/useSeqViz'
import type { SequencingAnalysis, AlignmentView, ReadTrace, SequencingVariant } from '@/api'

vi.mock('@/api', () => ({
  getReadTrace: vi.fn(),
}))

import { getReadTrace } from '@/api'

const mockTrace: ReadTrace = {
  filename: 'r1.ab1',
  bases: 'ACGTACGTAC',
  quality: [40, 40, 40, 40, 40, 12, 40, 40, 40, 40],
  channels: { A: [], T: [], G: [], C: [] },
  peak_indices: [],
}

/** 100 列对齐（参考起点 101）：col 5 一处错配（参考 A → read G，Q=12 低质量） */
function makeAlignmentView(): AlignmentView {
  const ref = 'ACGTA'.repeat(20)
  const read = ref.split('')
  read[5] = 'G'
  return {
    ref_start: 101,
    ref_aligned: ref,
    read_aligned: read.join(''),
    q_aligned: Array.from({ length: 100 }, (_, i) => (i === 5 ? 12 : 40)),
  }
}

function mkRead(over: Record<string, unknown> = {}) {
  return {
    index: 0, filename: 'r1.ab1', raw_length: 500, trimmed_length: 480,
    mean_q: 38, direction: '+', ref_start: 100, ref_end: 580,
    identity: 0.998, mixed_positions: [], ...over,
  }
}

function mkAnalysis(reads: ReturnType<typeof mkRead>[], over: Record<string, unknown> = {}) {
  return {
    analysis_id: 'seq_viz1', sample_name: 's', created_at: '2026-01-01T00:00:00',
    engine: 'internal', conclusion: '', reads,
    variants: [] as unknown[],
    consensus: { sequence: '', covered_ranges: [], coverage_percent: 0 },
    coverage_ranges: [[100, 580]], mixed_detected: {}, errors: [],
    reference_length: 5000, features: [], ...over,
  } as unknown as SequencingAnalysis
}

/** 假滚动容器：注入 seqBox ref 驱动需要视口宽度/滚动位置的交互分支 */
function fakeWrap(clientWidth = 1000) {
  return {
    clientWidth, clientHeight: 300, scrollLeft: 0,
    scrollTo: vi.fn(), scrollBy: vi.fn(), scrollIntoView: vi.fn(),
    getBoundingClientRect: () => ({ top: 0, left: 0, width: clientWidth, height: 300 }),
  } as unknown as HTMLElement
}

type Viz = ReturnType<typeof useSeqViz>

let wrapper: VueWrapper | null = null

/** 最小宿主组件挂载 composable（onMounted/onBeforeUnmount 需要组件实例） */
function mountViz() {
  const analysis = ref<SequencingAnalysis | null>(null)
  const errorMsg = ref('')
  let viz!: Viz
  wrapper = mount(defineComponent({
    setup() {
      viz = useSeqViz({ analysis, errorMsg })
      return () => null
    },
  }))
  return { analysis, errorMsg, viz }
}

/** 有对齐 read 的分析（2 条：r1 100-580 正向，r2 300-700 正向） */
function mountWithAligned(opts: { wrap?: HTMLElement; variants?: SequencingVariant[] } = {}) {
  const ctx = mountViz()
  ctx.analysis.value = mkAnalysis([
    mkRead({ alignment_view: makeAlignmentView() }),
    mkRead({ index: 1, filename: 'r2.ab1', ref_start: 300, ref_end: 700,
      alignment_view: makeAlignmentView() }),
  ], opts.variants ? { variants: opts.variants } : {})
  if (opts.wrap) ctx.viz.seqBox.value = opts.wrap
  return ctx
}

describe('useSeqViz composable', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('requestAnimationFrame', () => 0)
    vi.stubGlobal('cancelAnimationFrame', () => undefined)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('initializes with defaults and empty-overview layout', () => {
    const { analysis, viz } = mountViz()
    analysis.value = mkAnalysis([mkRead()])
    expect(viz.seqColW.value).toBe(12)
    expect(viz.visibleReads.value).toEqual([])
    expect(viz.selectedReadIdx.value).toBeNull()
    expect(viz.seqInfo.value).toBe('')
    expect(viz.jumpInput.value).toBe('')
    // 空视图高度：单泳道简图42 + 尺14 + 参考行16 + 间隔4 + 底8 + 滚动条补偿18
    expect(viz.seqWrapH.value).toBe(102)
    expect(viz.seqSpacerW.value).toBe(60000)   // 5000bp × 12px
  })

  it('overview domain narrows to primer span with 2% padding', () => {
    const { analysis, viz } = mountViz()
    analysis.value = mkAnalysis([
      mkRead(),
      mkRead({ index: 1, filename: 'r2.ab1', ref_start: 300, ref_end: 700 }),
    ])
    // 覆盖区段 100-700，跨 600bp → pad 12 → 87-712
    expect(viz.ovDomain.value).toEqual({ lo: 87, hi: 712 })
    // 无覆盖 read 时域回退整参考
    const ctx2 = mountViz()
    ctx2.analysis.value = mkAnalysis([mkRead({ ref_end: 0 })])
    expect(ctx2.viz.ovDomain.value).toEqual({ lo: 0, hi: 5000 })
  })

  it('loadSeqTrace caches per read and dedupes concurrent fetches', async () => {
    vi.mocked(getReadTrace).mockImplementation(() =>
      new Promise((resolve) => setTimeout(() => resolve(mockTrace), 10)))
    const { analysis, viz } = mountViz()
    analysis.value = mkAnalysis([mkRead({ alignment_view: makeAlignmentView() })])

    // 在途去重：第二个并发调用不再发请求，直接返回 null（缓存尚未写入；
    // 调用方只靠返回值判空的场景是绘制 watch，缓存写入后的重绘会补上）
    const [a, b] = await Promise.all([viz.loadSeqTrace(0), viz.loadSeqTrace(0)])
    expect(getReadTrace).toHaveBeenCalledTimes(1)
    expect(a).toBe(mockTrace)
    expect(b).toBeNull()

    // 二次调用走缓存（注意：经 ref 缓存取出的是 reactive 代理，结构相等而非同一引用）
    const c = await viz.loadSeqTrace(0)
    expect(getReadTrace).toHaveBeenCalledTimes(1)
    expect(c).toEqual(mockTrace)
    expect(viz.traceCache.value[0]).toEqual(mockTrace)
  })

  it('loadSeqTrace caches null on failure and writes errorMsg', async () => {
    vi.mocked(getReadTrace).mockRejectedValue({ response: { data: { detail: '峰图过期' } } })
    const { analysis, errorMsg, viz } = mountViz()
    analysis.value = mkAnalysis([mkRead()])

    const t = await viz.loadSeqTrace(0)
    expect(t).toBeNull()
    expect(errorMsg.value).toBe('峰图过期')
    // 失败也进缓存（null）：不再重复拉取
    expect(viz.traceCache.value[0]).toBeNull()
    await viz.loadSeqTrace(0)
    expect(getReadTrace).toHaveBeenCalledTimes(1)
  })

  it('toggleRead adds with selection and trace fetch, remove falls selection back', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { analysis, viz } = mountViz()
    analysis.value = mkAnalysis([
      mkRead({ alignment_view: makeAlignmentView() }),
      mkRead({ index: 1, filename: 'r2.ab1', ref_start: 300, ref_end: 700,
        alignment_view: makeAlignmentView() }),
    ])

    await viz.toggleRead(0)
    expect(viz.visibleReads.value).toEqual([0])
    expect(viz.selectedReadIdx.value).toBe(0)
    expect(getReadTrace).toHaveBeenCalledWith('seq_viz1', 0)

    await viz.toggleRead(1)
    expect(viz.selectedReadIdx.value).toBe(1)

    // 取消当前选中的 read → 回退到最后一条仍显示的
    await viz.toggleRead(1)
    expect(viz.visibleReads.value).toEqual([0])
    expect(viz.selectedReadIdx.value).toBe(0)
    // 全部取消 → 选中清空
    await viz.toggleRead(0)
    expect(viz.visibleReads.value).toEqual([])
    expect(viz.selectedReadIdx.value).toBeNull()
  })

  it('toggleRead jumps to an off-screen read but stays when covered', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { analysis, viz } = mountViz()
    analysis.value = mkAnalysis([
      mkRead({ alignment_view: makeAlignmentView() }),
      mkRead({ index: 1, filename: 'r2.ab1', ref_start: 3000, ref_end: 3400,
        alignment_view: makeAlignmentView() }),
    ])
    const wrap = fakeWrap(8000)   // 视窗 0-666.7bp（colW=12）盖住 r1 起点 100
    viz.seqBox.value = wrap

    // r1 落在当前视窗内 → 不跳
    await viz.toggleRead(0)
    expect(wrap.scrollTo).not.toHaveBeenCalled()

    // r2 在 3000+，视窗外 → 跳到其起点居中
    await viz.toggleRead(1)
    expect(wrap.scrollTo).toHaveBeenCalledTimes(1)
    const arg = vi.mocked(wrap.scrollTo).mock.calls[0][0] as ScrollToOptions
    // target = (3000 - 0.5) * 12 - 8000/2 = 31994
    expect(arg.left).toBe(31994)
    expect(arg.behavior).toBe('auto')
  })

  it('rowLayouts filters by visibility and viewport intersection, sorted by ref_start', () => {
    const { analysis, viz } = mountViz()
    analysis.value = mkAnalysis([
      mkRead({ alignment_view: makeAlignmentView() }),
      mkRead({ index: 1, filename: 'r2.ab1', ref_start: 300, ref_end: 700,
        alignment_view: makeAlignmentView() }),
    ])
    // 两条都勾选（直改数组绕过 trace 拉取）
    viz.visibleReads.value = [1, 0]
    expect(viz.selectedReadIdx.value).toBeNull()
    // happy-dom clientWidth=0 → 视窗视为全参考，两行按参考起点排序
    expect(viz.rowLayouts().map((L) => L.ri)).toEqual([0, 1])
    // 指定视野只盖到第二条 → 只有它
    expect(viz.rowLayouts({ uLeft: 620, uRight: 700 }).map((L) => L.ri)).toEqual([1])
    // 未勾选的 read 不出行
    viz.visibleReads.value = [0]
    expect(viz.rowLayouts({ uLeft: 620, uRight: 700 })).toEqual([])
    // 假 wrap 有宽度：视野按 scrollLeft/colW 计算
    const wrap = fakeWrap(600)
    viz.seqBox.value = wrap
    viz.seqScrollX.value = 2400   // 200-300bp（colW 12）
    expect(viz.rowLayouts().map((L) => L.ri)).toEqual([0])
    // 选中 read 的条带加高 SEL_STRIP_EXTRA=14
    viz.selectedReadIdx.value = 0
    const L0 = viz.rowLayouts()[0]
    expect(L0.stripH).toBe(62 + 14)
    expect(L0.stripTop).toBe(L0.rowY + 16)
    expect(L0.baseline).toBe(L0.stripTop + L0.stripH - 10)
  })

  it('jumpToRefPos validates input, selects column and fills evidence line', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { analysis, viz } = mountWithAligned()
    viz.visibleReads.value = [0]

    viz.jumpInput.value = '99999'   // 越界忽略
    viz.jumpToRefPos()
    expect(viz.selRefPos.value).toBeNull()

    viz.jumpInput.value = 'abc'
    viz.jumpToRefPos()
    expect(viz.selRefPos.value).toBeNull()

    viz.jumpInput.value = '106'
    viz.jumpToRefPos()
    expect(viz.selRefPos.value).toBe(106)
    // 证据行：col 5（101+5）错配 G Q12 + 差异注释
    expect(viz.seqInfo.value).toContain('参考位置 106')
    expect(viz.seqInfo.value).toContain('G（Q12）')
  })

  it('jumpToVariant ensures target read visible+selected, loads its trace and scrolls', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { analysis, viz } = mountWithAligned()
    const wrap = fakeWrap(1000)
    viz.seqBox.value = wrap
    const v = { ref_pos: 320, type: 'substitution', ref_base: 'A', alt_base: 'G',
      read: 'r2.ab1' } as unknown as SequencingVariant

    await viz.jumpToVariant(v)
    // r2 原本未显示 → 自动加入并选中、拉峰图
    expect(viz.visibleReads.value).toContain(1)
    expect(viz.selectedReadIdx.value).toBe(1)
    expect(getReadTrace).toHaveBeenCalledWith('seq_viz1', 1)
    expect(viz.selRefPos.value).toBe(320)
    expect(viz.seqInfo.value).toContain('参考位置 320')
    expect(wrap.scrollIntoView).toHaveBeenCalled()
  })

  it('seqZoom clamps 1-28 and anchors toolbar zoom at viewport center', () => {
    const { viz } = mountViz()
    viz.seqBox.value = fakeWrap(1000)

    // 中心锚：u = (0 + 500)/12，新列宽 24 → scrollLeft = u*24 - 500 = 500
    viz.seqZoom(2)
    expect(viz.seqColW.value).toBe(24)
    expect(viz.seqScrollX.value).toBe(500)

    // 反向：锚点保持在视口中心
    viz.seqZoom(0.5)
    expect(viz.seqColW.value).toBe(12)
    expect(viz.seqScrollX.value).toBe(0)

    // 钳制：不超上限 28、不破下限 1
    viz.seqZoom(100)
    expect(viz.seqColW.value).toBe(28)
    viz.seqZoom(0.001)
    expect(viz.seqColW.value).toBe(1)

    // 极限处缩放无效时不再重排
    const w0 = viz.seqColW.value
    viz.seqZoom(0.5)
    expect(viz.seqColW.value).toBe(w0)
  })

  it('seqZoomAt anchors at the given pixel so the pointed column stays put', () => {
    const { viz } = mountViz()
    viz.seqBox.value = fakeWrap(1000)
    viz.seqScrollX.value = 1200   // 指针在 x=600 → u = 1800/12 = 150
    // seqZoomAt 是内部函数：经 onSeqWheel 的 Ctrl 分支驱动（系数 1.2，offsetX 即锚点）
    const ev = { ctrlKey: true, deltaY: -100, offsetX: 600, preventDefault: vi.fn() } as unknown as WheelEvent
    viz.onSeqWheel(ev)
    expect(viz.seqColW.value).toBeCloseTo(14.4)   // 12 × 1.2
    // scrollLeft = 150×14.4 − 600 = 1560：参考位置 150 仍在指针下
    expect(viz.seqScrollX.value).toBeCloseTo(1560)
  })

  it('seqFit scales the whole reference into the viewport', () => {
    const { analysis, viz } = mountViz()
    analysis.value = mkAnalysis([mkRead()])
    const wrap = fakeWrap(1000)
    viz.seqBox.value = wrap
    viz.seqColW.value = 20
    viz.seqFit()
    // Math.max(1, floor(1000/5000)) = 1：5000bp 压进 1000px 每列 1px
    expect(viz.seqColW.value).toBe(1)
    expect(viz.seqScrollX.value).toBe(0)
  })

  it('scrollToRefPos centers the column and flashes it for ~1.6s', async () => {
    vi.useFakeTimers()
    const { viz } = mountViz()
    const wrap = fakeWrap(1000)
    viz.seqBox.value = wrap

    viz.scrollToRefPos(200, true)
    expect(wrap.scrollTo).toHaveBeenCalledWith({ left: (200 - 0.5) * 12 - 500, behavior: 'auto' })
    expect(viz.flashRefPos.value).toBe(200)
    vi.advanceTimersByTime(1600)
    expect(viz.flashRefPos.value).toBeNull()
  })

  it('onSeqScroll mirrors scrollLeft; onSeqWheel routes vertical wheel to horizontal scroll', () => {
    const { viz } = mountViz()
    const wrap = fakeWrap(1000)
    viz.seqBox.value = wrap

    wrap.scrollLeft = 480
    viz.onSeqScroll()
    expect(viz.seqScrollX.value).toBe(480)

    // 非 Ctrl 滚轮 → 横向滚动一行峰图浏览器惯例
    const ev = { ctrlKey: false, deltaY: 240, preventDefault: vi.fn() } as unknown as WheelEvent
    viz.onSeqWheel(ev)
    expect(wrap.scrollBy).toHaveBeenCalledWith({ left: 240 })
    expect(ev.preventDefault).toHaveBeenCalled()

    // Ctrl+滚轮 → 缩放（放大）并以指针为锚：u = (0+500)/12 = 41.67，
    // 新列宽 14.4 → scrollLeft = 41.67×14.4 − 500 = 100（指针下列不变）。
    // 注意缩放锚点读的是 seqScrollX ref 而非 wrap.scrollLeft，须同步复位
    viz.seqScrollX.value = 0
    const ev2 = { ctrlKey: true, deltaY: -100, offsetX: 500, preventDefault: vi.fn() } as unknown as WheelEvent
    viz.onSeqWheel(ev2)
    expect(viz.seqColW.value).toBeCloseTo(12 * 1.2)
    expect(viz.seqScrollX.value).toBeCloseTo(100)
    expect(ev2.preventDefault).toHaveBeenCalled()
  })

  it('onSeqClick on overview hits a read → select + zoom-to-fit; on blank → jump only', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { analysis, viz } = mountWithAligned({ wrap: fakeWrap(1000) })
    // 简图域 87-712，宽 1000：x = (refPos - 87) / 625 * 1000

    // 点 r1 箭头（覆盖 100-580，中心 x≈342）→ 选中 r1 + 缩放到其覆盖区
    // span=481 → nu = 976/481 ≈ 2.03 < 6 → 塞不下，落 read 起点不缩放
    await viz.onSeqClick({ clientX: 342, clientY: 15 } as MouseEvent)
    await flushPromises()
    expect(viz.visibleReads.value).toContain(0)
    expect(viz.selectedReadIdx.value).toBe(0)
    expect(viz.seqInfo.value).toContain('已选中 r1.ab1')
    expect(getReadTrace).toHaveBeenCalledWith('seq_viz1', 0)
    // 长 read 塞不下 → 列宽钳到可读下限 6，scrollLeft 落在起点-12px
    expect(viz.seqColW.value).toBe(6)
    expect(viz.seqScrollX.value).toBe(Math.max(0, (100 - 1) * 6 - 12))

    // 点简图空白（无 read 覆盖的位置，比如 650 → x=(650-87)/625*1000≈901 但 r2 覆盖 300-700）
    // 取 87-712 内、无 read 处不存在（两条 read 已拼满）→ 用主区空白代替：见下个用例。
    // 这里改测：点击落在 r2 内的空白 y 仍属简图 → 选中 r2
    viz.resetSeqViz()
    const x2 = Math.round(((500 - 87) / 625) * 1000)   // refPos≈500 在 r1∩r2 交集
    await viz.onSeqClick({ clientX: x2, clientY: 15 } as MouseEvent)
    await flushPromises()
    // 同泳道两条 read 都覆盖 500 → 取距离最近者（均 d=0 → 先见者，按装箱顺序 r1）
    expect(viz.selectedReadIdx.value).toBe(0)
  })

  it('onSeqClick in main area picks the read via its letter row and resolves the column', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { viz } = mountWithAligned({ wrap: fakeWrap(1000) })
    viz.visibleReads.value = [0]
    viz.selectedReadIdx.value = 1
    // 视窗 100-183bp（scrollX 1200 / colW 12）：r1 在视野内有行，r2 没有
    viz.seqScrollX.value = 1200

    // 主区点击：2 泳道 ovH=64 + 尺14 + 参考行16 + 间隔4 → 首行字母行 y 100-116
    await viz.onSeqClick({ clientX: 600, clientY: 108 } as MouseEvent)
    expect(viz.selectedReadIdx.value).toBe(0)   // 字母行命中 → 峰图切到 r1
    // 同一次点击反解列：x = 600+1200 → refPos 151（r1 该列 A/Q40 有证据）
    expect(viz.selRefPos.value).toBe(151)
    expect(viz.seqInfo.value).toContain('参考位置 151')
    expect(viz.seqInfo.value).toContain('r1.ab1 A（Q40）')
  })

  it('clicking the mismatched column fills the evidence line with base and Q', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { viz } = mountWithAligned({ wrap: fakeWrap(1000) })
    viz.visibleReads.value = [0]
    // colW=6：x=633 → refPos = floor(633/6)+1 = 106（col 5 错配列 G/Q12）
    viz.seqColW.value = 6
    await viz.onSeqClick({ clientX: 633, clientY: 300 } as MouseEvent)
    expect(viz.selRefPos.value).toBe(106)
    expect(viz.seqInfo.value).toContain('参考位置 106')
    expect(viz.seqInfo.value).toContain('G（Q12）')
  })

  it('onSeqClick guards out-of-reference column clicks', async () => {
    const { viz } = mountWithAligned({ wrap: fakeWrap(1000) })
    // x=-50 → refPos = floor(-50/12)+1 < 1 → 越界守卫不写选中列
    await viz.onSeqClick({ clientX: -50, clientY: 300 } as MouseEvent)
    expect(viz.selRefPos.value).toBeNull()
  })

  it('resetSeqViz clears every view state', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { analysis, viz } = mountWithAligned()
    await viz.toggleRead(0)
    viz.seqColW.value = 20
    viz.selRefPos.value = 150
    viz.jumpInput.value = '150'

    viz.resetSeqViz()
    await nextTick()
    expect(viz.visibleReads.value).toEqual([])
    expect(viz.selectedReadIdx.value).toBeNull()
    expect(viz.traceCache.value).toEqual({})
    expect(viz.selRefPos.value).toBeNull()
    expect(viz.seqInfo.value).toBe('')
    expect(viz.jumpInput.value).toBe('')
    expect(viz.seqColW.value).toBe(12)
    expect(viz.seqScrollX.value).toBe(0)
  })

  it('watching visibleReads refills traces for newly checked reads', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { analysis, viz } = mountWithAligned()
    // 深度 watch 补拉：直接 push 进数组也应触发（原位变更）
    viz.visibleReads.value.push(0)
    await flushPromises()
    expect(getReadTrace).toHaveBeenCalledWith('seq_viz1', 0)
    expect(getReadTrace).toHaveBeenCalledTimes(1)
    // 已缓存的 read 不重复拉取
    viz.visibleReads.value.push(0)
    await flushPromises()
    expect(getReadTrace).toHaveBeenCalledTimes(1)
  })

  it('ovLayoutFor-driven seqWrapH grows with lanes and rows', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    const { analysis, viz } = mountWithAligned()
    // 2 泳道简图 64 + 尺14 + 参考行16 + 间隔4 + 底8 + 滚动条18 = 124
    expect(viz.seqWrapH.value).toBe(124)
    // 勾选 1 条：+ 字母行16 + 条带62
    viz.visibleReads.value = [0]
    await nextTick()
    expect(viz.seqWrapH.value).toBe(124 + 16 + 62)
    // 选中加高 14
    viz.selectedReadIdx.value = 0
    await nextTick()
    expect(viz.seqWrapH.value).toBe(124 + 16 + 62 + 14)
  })
})
