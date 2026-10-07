/**
 * Sanger 比对峰图融合视图（SnapGene 式）composable
 *
 * 从 SequencingPanel.vue 抽离的状态与逻辑壳：参考碱基行 + 各 read 碱基行 +
 * 四通道峰图画在同一个参考坐标轴上（差异列证据一屏看完），横轴 = 参考坐标
 * （插入列在左右两列间插缝），Ctrl+滚轮缩放。DOM 绑定（seqBox/seqCanvas）、
 * 缩放/滚动/点选/跳转交互、canvas 绘制（drawSeq）、峰图按 read 缓存加载、
 * 行布局（只渲染覆盖当前视野的 read）都在这里；纯函数列模型/泳道装箱在
 * utils/seqPanelModel。
 *
 * 组件侧用法：const seqviz = useSeqViz({ analysis, errorMsg }) 后解构，
 * 模板绑定与测试（wrapper.vm.xxx）访问路径与拆分前完全一致。
 */
import { computed, ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import type { Ref } from 'vue'
import { getReadTrace, type SequencingAnalysis, type SequencingVariant, type ReadTrace } from '@/api'
import { buildSeqCols, packLanes, shortName, type SeqCol } from '@/utils/seqPanelModel'

interface UseSeqVizOptions {
  /** 分析结果（父组件持有；preset 注入或分析完成时写入） */
  analysis: Ref<SequencingAnalysis | null>
  /** 错误信息 ref（峰图加载失败时写入提示） */
  errorMsg: Ref<string>
}

export function useSeqViz(options: UseSeqVizOptions) {
  const { analysis, errorMsg } = options

  const seqBox = ref<HTMLElement | null>(null)
  const seqCanvas = ref<HTMLCanvasElement | null>(null)
  const seqColW = ref(12)                 // 每个参考 bp 的像素宽（缩放）
  const visibleReads = ref<number[]>([])  // 显示中的 read index（按行序）
  const traceCache = ref<Record<number, ReadTrace | null>>({})
  const seqTraceLoading = ref(false)
  // 在途去重：preset watch（immediate）与 visibleReads deep watch 同帧触发，
  // 缓存尚未写入时会并发重入拉取同一条峰图
  const traceInFlight = new Set<number>()
  const selRefPos = ref<number | null>(null)   // 点选列（参考坐标）
  const flashRefPos = ref<number | null>(null) // 跳转高亮列（短暂）
  const seqInfo = ref('')
  const jumpInput = ref('')
  // 横向滚动位置（响应式：行布局按视野过滤，行数随滚动增减 → 高度要跟着变）
  const seqScrollX = ref(0)
  // 布局重算 tick：挂载后拿到真实 clientWidth、窗口 resize 时行集合会变
  const seqLayoutTick = ref(0)
  let seqRaf = 0
  let flashTimer: ReturnType<typeof setTimeout> | undefined

  const SEQ_STRIP_H = 62   // 每条 read 的峰图条带高（直接交接在各自字母行下方）
  const SEL_STRIP_EXTRA = 14  // 选中 read 的条带加高量（着重显示）
  const SEQ_ROW_H = 16     // 碱基字母行高
  const OV_ARROW_H = 18    // 覆盖简图每条引物箭头高
  const OV_LANE_GAP = 4    // 覆盖简图泳道间隔
  /** 覆盖简图布局（简图固定整参考宽度，不随横向滚动移动；seqWrapH 与 drawSeq 共用一套数字） */
  function ovLayoutFor(laneCount: number) {
    const arrowBlock = 6 + laneCount * (OV_ARROW_H + OV_LANE_GAP)
    return { arrowBlock, ovCovY: arrowBlock + 6, ovH: arrowBlock + 6 + 8 }
  }
  // 泳道分配抽离到 @/utils/seqPanelModel.packLanes（贪心装箱，可独立单测）
  const ovLanes = computed(() => packLanes(analysis.value?.reads ?? []))
  const ovLaneCount = computed(() => Math.max(1, ovLanes.value.lanes.length))
  // 覆盖简图的坐标域：只取引物实际覆盖的区段（四周留 2% 边距）——
  // 参考序列上没有引物覆盖的部分不占位，箭头才能铺满整个简图宽度
  const ovDomain = computed(() => {
    const refLen = analysis.value?.reference_length ?? 0
    const cov = (analysis.value?.reads ?? []).filter((r) => r.ref_end > 0)
    if (!cov.length || !refLen) return { lo: 0, hi: Math.max(1, refLen) }
    const lo0 = Math.min(...cov.map((r) => r.ref_start))
    const hi0 = Math.max(...cov.map((r) => r.ref_end))
    const pad = Math.max(5, Math.round((hi0 - lo0) * 0.02))
    return { lo: Math.max(1, lo0 - 1 - pad), hi: Math.min(refLen, hi0 + pad) }
  })
  const seqWrapH = computed(() => {
    // 覆盖简图带 + 刻度尺14 + 参考行16 + 间隔4 + 视野内各 read 的
    // 字母行 + 各自峰图条带（选中的加高）+ 底部8；末尾 18px 是水平
    // 滚动条补偿：wrap.clientHeight 不含滚动条，少算会把条带底裁掉。
    // 行集合随横向滚动增减（rowLayouts 读 seqScrollX），高度跟着变
    seqLayoutTick.value   // 挂载/resize 后 clientWidth 变化时强制重算
    const { ovH } = ovLayoutFor(ovLaneCount.value)
    const strips = rowLayouts().reduce((s, L) => s + SEQ_ROW_H + L.stripH, 0)
    return ovH + 14 + 16 + 4 + strips + 8 + 18
  })
  const seqSpacerW = computed(() => (analysis.value?.reference_length ?? 0) * seqColW.value)

  // 当前峰图带展示哪条 read（简图/字母行/复选框点选都汇聚到这里）
  const selectedReadIdx = ref<number | null>(null)

  // 逐列列模型与泳道装箱已抽离到 @/utils/seqPanelModel（可独立单测）；
  // 组件只保留缓存壳：alignment_view 是参考方向的逐列对齐（反向 read 已折算）
  const seqColCache: Record<number, SeqCol[] | null> = {}

  function seqColsFor(readIndex: number): SeqCol[] | null {
    if (!(readIndex in seqColCache)) {
      const r = analysis.value?.reads[readIndex]
      seqColCache[readIndex] = r ? buildSeqCols(r) : null
    }
    return seqColCache[readIndex]
  }

  function resetSeqViz() {
    visibleReads.value = []
    traceCache.value = {}
    selRefPos.value = null
    flashRefPos.value = null
    selectedReadIdx.value = null
    seqInfo.value = ''
    jumpInput.value = ''
    seqColW.value = 12
    seqScrollX.value = 0
    for (const k of Object.keys(seqColCache)) delete seqColCache[Number(k)]
  }

  // 每条 read 一个固定颜色（简图箭头 / 字母行芯片 / 工具栏复选框同色），
  // 多条引物交叠时靠颜色区分谁是谁（SnapGene 式）；避开峰图四通道主色
  const READ_COLORS = ['#8E24AA', '#1565C0', '#2E7D32', '#D84315', '#00838F', '#C2185B', '#5D4037', '#455A64']
  function readColor(i: number): string {
    return READ_COLORS[i % READ_COLORS.length]
  }
  function hexA(hex: string, a: number): string {
    const n = parseInt(hex.slice(1), 16)
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
  }

  // 峰图按 read 缓存（多 read 堆叠时各自取用；TTL 清理后为 null，仅剩碱基行）
  async function loadSeqTrace(ri: number): Promise<ReadTrace | null> {
    if (!analysis.value) return null
    if (traceCache.value[ri] !== undefined || traceInFlight.has(ri)) {
      return traceCache.value[ri] ?? null   // 已加载/加载中：避免并发重入重复拉取
    }
    traceInFlight.add(ri)
    seqTraceLoading.value = true
    try {
      const t = await getReadTrace(analysis.value.analysis_id, ri)
      traceCache.value = { ...traceCache.value, [ri]: t ?? null }
      nextSeqDraw()
      return t ?? null
    } catch (e: any) {
      traceCache.value = { ...traceCache.value, [ri]: null }
      errorMsg.value = e.response?.data?.detail || '峰图加载失败'
      return null
    } finally {
      traceInFlight.delete(ri)
      seqTraceLoading.value = false
    }
  }

  function isReadVisible(ri: number) {
    return visibleReads.value.includes(ri)
  }

  async function toggleRead(ri: number) {
    const i = visibleReads.value.indexOf(ri)
    if (i >= 0) {
      visibleReads.value.splice(i, 1)
      // 取消的是当前峰图 read → 回退到最后一条仍显示的
      if (selectedReadIdx.value === ri) {
        selectedReadIdx.value = visibleReads.value.length
          ? visibleReads.value[visibleReads.value.length - 1] : null
      }
      nextSeqDraw()
      return
    }
    visibleReads.value.push(ri)
    selectedReadIdx.value = ri
    await loadSeqTrace(ri)
    nextSeqDraw()
    // SnapGene 习惯：勾选一条 read 即看到它的落点——只有当它不覆盖当前窗口时才跳
    const read = analysis.value?.reads[ri]
    const wrap = seqBox.value
    if (read && wrap && wrap.clientWidth > 0) {
      const uL = seqScrollX.value / seqColW.value
      const uR = (seqScrollX.value + wrap.clientWidth) / seqColW.value
      if (read.ref_end < uL || read.ref_start > uR) scrollToRefPos(read.ref_start, true)
    }
  }

  function safeScrollTo(left: number, smooth: boolean) {
    const wrap = seqBox.value
    if (!wrap) return
    try {
      wrap.scrollTo({ left, behavior: smooth ? 'smooth' : 'auto' })
    } catch {
      wrap.scrollLeft = left   // jsdom 等环境无平滑滚动
    }
  }

  function scrollToRefPos(refPos: number, flash = false) {
    const wrap = seqBox.value
    if (wrap) {
      const target = Math.max(0, (refPos - 0.5) * seqColW.value - wrap.clientWidth / 2)
      // 立即定位（不用平滑滚动）：平滑滚动进行中缩放/连跳会取到中途 scrollLeft，
      // 造成视口漂移；目标列本身有 1.6s 黄色闪烁标识，无需动画引导
      safeScrollTo(target, false)
    }
    if (flash) {
      flashRefPos.value = refPos
      if (flashTimer) clearTimeout(flashTimer)
      flashTimer = setTimeout(() => {
        flashRefPos.value = null
        nextSeqDraw()
      }, 1600)
    }
    nextSeqDraw()
  }

  async function jumpToVariant(v: SequencingVariant) {
    if (!analysis.value) return
    const read = analysis.value.reads.find((r) => r.filename === (v.read || r.filename)) || analysis.value.reads[0]
    if (!read) return
    if (!isReadVisible(read.index)) {
      visibleReads.value.push(read.index)
      await nextTick()
    }
    selectedReadIdx.value = read.index   // 峰图条带切到支持该差异的 read
    await loadSeqTrace(read.index)
    selRefPos.value = v.ref_pos
    seqInfo.value = composeSeqInfo(v.ref_pos)
    seqBox.value?.scrollIntoView?.({ block: 'nearest' })
    scrollToRefPos(v.ref_pos, true)
  }

  function openReadInSeqviz(i: number) {
    const read = analysis.value?.reads[i]
    if (!read) return
    if (!isReadVisible(i)) {
      visibleReads.value.push(i)
      loadSeqTrace(i)
    }
    selectedReadIdx.value = i
    seqBox.value?.scrollIntoView?.({ block: 'nearest' })
    scrollToRefPos(read.ref_start)
  }

  /** 简图点选引物：峰图切到该 read，未显示的自动加入；
   *  zoom=true 时把主视图缩放到恰好容纳该 read 的覆盖区并居中（放大效果） */
  async function selectRead(ri: number, zoom = false) {
    const read = analysis.value?.reads[ri]
    if (!read || read.ref_end <= 0) return
    const first = selectedReadIdx.value !== ri
    selectedReadIdx.value = ri
    if (!isReadVisible(ri)) {
      visibleReads.value.push(ri)
      loadSeqTrace(ri)
    }
    seqInfo.value = `已选中 ${shortName(read.filename)}（${read.direction === '-' ? '←' : '→'} ${read.ref_start}–${read.ref_end} · Q${read.mean_q}）${first ? '，峰图条带已切换到该引物' : ''}`
    const wrap = seqBox.value
    if (zoom && wrap && wrap.clientWidth > 0) {
      const span = Math.max(1, read.ref_end - read.ref_start + 1)
      const avail = wrap.clientWidth - 24
      // 可读性下限 6px/碱基：长 read 不再硬塞进一屏把峰压瘪（列宽 <1 时峰形不可辨）
      let nu = Math.min(28, avail / span)
      const fits = nu >= 6
      if (!fits) nu = 6
      nu = Math.round(nu * 100) / 100
      if (nu !== seqColW.value) seqColW.value = nu
      if (fits) {
        scrollToRefPos((read.ref_start + read.ref_end) / 2, true)
      } else {
        // 塞不下整条 read：以可读密度落在 read 起点处（起点闪黄标识），向右浏览
        seqScrollX.value = Math.max(0, (read.ref_start - 1) * nu - 12)
        safeScrollTo(seqScrollX.value, false)
        flashRefPos.value = read.ref_start
        if (flashTimer) clearTimeout(flashTimer)
        flashTimer = setTimeout(() => {
          flashRefPos.value = null
          nextSeqDraw()
        }, 1600)
        nextSeqDraw()
      }
    }
    nextSeqDraw()
  }

  /** 行布局：只渲染「勾选中、有对齐、与当前视野相交」的 read（SnapGene 式），
   *  按参考起点排序；字母行 + 条带一起出现/消失，空行不占位。
   *  无浏览器环境（happy-dom clientWidth=0）视窗视为全参考，行全出，便于测试。
   *  win 参数供测试直接指定视野。 */
  function rowLayouts(win?: { uLeft: number; uRight: number }) {
    const a = analysis.value
    if (!a) return []
    const cw = seqBox.value?.clientWidth ?? 0
    const colW = seqColW.value
    const uLeft = win ? win.uLeft : cw > 0 ? seqScrollX.value / colW : 0
    const uRight = win ? win.uRight
      : cw > 0 ? (seqScrollX.value + cw) / colW : a.reference_length
    const eligible = visibleReads.value
      .filter((ri) => {
        const r = a.reads[ri]
        return r && r.ref_end > 0 && r.ref_end >= uLeft - 2 && r.ref_start <= uRight + 2
      })
      .sort((x, y) => a.reads[x].ref_start - a.reads[y].ref_start)
    const layouts: { ri: number; rowY: number; stripTop: number; stripH: number; baseline: number }[] = []
    let y0 = ovLayoutFor(ovLaneCount.value).ovH + 14 + 2 + SEQ_ROW_H + 4
    for (const ri of eligible) {
      const stripH = SEQ_STRIP_H + (selectedReadIdx.value === ri ? SEL_STRIP_EXTRA : 0)
      layouts.push({ ri, rowY: y0, stripTop: y0 + SEQ_ROW_H, stripH, baseline: y0 + SEQ_ROW_H + stripH - 10 })
      y0 += SEQ_ROW_H + stripH
    }
    return layouts
  }

  function jumpToRefPos() {
    const p = parseInt(jumpInput.value, 10)
    if (Number.isNaN(p) || !analysis.value) return
    if (p < 1 || p > analysis.value.reference_length) return
    selRefPos.value = p
    seqInfo.value = composeSeqInfo(p)
    scrollToRefPos(p, true)
  }

  function onSeqScroll() {
    seqScrollX.value = seqBox.value?.scrollLeft ?? 0
    nextSeqDraw()
  }

  function onSeqWheel(e: WheelEvent) {
    if (e.ctrlKey) {
      e.preventDefault()
      seqZoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.offsetX)
    } else {
      // 纵向滚轮 → 横向浏览（峰图浏览器惯例）
      e.preventDefault()
      seqBox.value?.scrollBy?.({ left: e.deltaY })
      onSeqScroll()
    }
  }

  function seqZoomAt(f: number, anchorX?: number) {
    const old = seqColW.value
    const nu = Math.max(1, Math.min(28, Math.round(old * f * 100) / 100))
    if (nu === old) return
    if (anchorX != null) {
      const u = (seqScrollX.value + anchorX) / old
      seqColW.value = nu
      seqScrollX.value = Math.max(0, u * nu - anchorX)
      safeScrollTo(seqScrollX.value, false)
    } else {
      seqColW.value = nu
    }
    nextSeqDraw()
  }

  // 工具栏缩放以视口中心为锚——点放大/缩小不丢失正在看的位点
  function seqZoom(f: number) {
    const wrap = seqBox.value
    seqZoomAt(f, wrap ? wrap.clientWidth / 2 : undefined)
  }

  function seqFit() {
    const wrap = seqBox.value
    if (!wrap || !analysis.value) return
    seqColW.value = Math.max(1, Math.min(28, Math.floor(wrap.clientWidth / analysis.value.reference_length)))
    safeScrollTo(0, false)
    nextSeqDraw()
  }

  function onSeqClick(e: MouseEvent) {
    const wrap = seqBox.value
    const a = analysis.value
    if (!wrap || !a) return
    const rect = wrap.getBoundingClientRect()
    const y = e.clientY - rect.top
    const { ovH } = ovLayoutFor(ovLaneCount.value)

    if (y < ovH) {
      // 覆盖简图（固定比例、不随滚动移动，域为引物覆盖区段）：x 反解参考位置
      const cw = wrap.clientWidth > 0 ? wrap.clientWidth : 1000
      const xLocal = e.clientX - rect.left
      const { lo: ovLo, hi: ovHi } = ovDomain.value
      const refPos = Math.min(Math.max(1, Math.round(ovLo + (xLocal / cw) * (ovHi - ovLo))),
        a.reference_length)
      const { lanes } = ovLanes.value
      const lane = Math.floor((y - 6) / (OV_ARROW_H + OV_LANE_GAP))
      // 命中该泳道内覆盖此位置的引物 → 选中并放大到其覆盖区；没命中 → 仅跳转
      let hit = -1
      if (lane >= 0 && lane < lanes.length) {
        let best = Infinity
        for (const i of lanes[lane]) {
          const r = a.reads[i]
          if (refPos < r.ref_start - 1 || refPos > r.ref_end + 1) continue
          const d = refPos < r.ref_start ? r.ref_start - refPos : refPos > r.ref_end ? refPos - r.ref_end : 0
          if (d < best) { best = d; hit = i }
        }
      }
      if (hit >= 0) {
        selectRead(hit, true)
      } else {
        selRefPos.value = refPos
        seqInfo.value = composeSeqInfo(refPos)
        scrollToRefPos(refPos, true)
      }
      return
    }

    // 主区：点在字母行或其峰图条带上 → 选中该 read；列点选证据逻辑不变
    for (const L of rowLayouts()) {
      if (y >= L.rowY && y < L.stripTop + L.stripH) {
        if (selectedReadIdx.value !== L.ri) selectedReadIdx.value = L.ri
        break
      }
    }
    const x = e.clientX - rect.left + seqScrollX.value
    const refPos = Math.floor(x / seqColW.value) + 1
    if (refPos < 1 || refPos > a.reference_length) return
    selRefPos.value = refPos
    seqInfo.value = composeSeqInfo(refPos)
    nextSeqDraw()
  }

  /** 点选列的证据摘要：各可见 read 的碱基/Q/双峰占比 + 落在该位的差异注释 */
  function composeSeqInfo(refPos: number): string {
    const a = analysis.value
    if (!a) return ''
    const parts: string[] = [`参考位置 ${refPos}`]
    for (const ri of visibleReads.value) {
      const cols = seqColsFor(ri)
      const read = a.reads[ri]
      if (!cols || !read) continue
      const hit = cols.find((c) => c.refPos === refPos && c.read !== '-')
      if (hit) {
        let s = `${shortName(read.filename)} ${hit.read}（Q${hit.q || '?'}`
        const md = (read.mixed_detail || []).find((d) => d.pos - 1 === hit.origIdx)
        // ratio 是次峰/主峰面积比，次要克隆占比 = r/(1+r)（与结论聚合口径一致）
        if (md) s += `，双峰：次峰 ${md.secondary_base} 占 ${Math.round((md.ratio / (1 + md.ratio)) * 100)}%`
        s += '）'
        parts.push(s)
      } else {
        const insHere = cols.some((c) => c.ins && Math.floor(c.xu) + 1 === refPos)
        parts.push(`${shortName(read.filename)} 未覆盖${insHere ? '（此处 read 有插入碱基）' : ''}`)
      }
    }
    const v = a.variants.find((x) => x.ref_pos === refPos)
    if (v) {
      const feats = (v.features || []).map((f) => f.name).join('、')
      parts.push(`差异：${v.ref_base}→${v.alt_base}（${feats || '非编码区'}${v.aa_change ? '，' + v.aa_change : ''}）`)
    }
    return parts.join(' · ')
  }

  // ==================== 融合视图绘制 ====================
  function nextSeqDraw() {
    cancelAnimationFrame(seqRaf)
    seqRaf = requestAnimationFrame(drawSeq)
  }

  function seqX(xu: number): number {
    return xu * seqColW.value - seqScrollX.value
  }

  const CHANNEL_COLORS: Record<string, string> = { A: '#2E9E44', T: '#D0342C', G: '#222222', C: '#2456C8' }

  /** 峰图采样窗：可见列的 apex 与邻峰中点围成本碱基区间；同时求通道最大幅值 */
  function buildTraceWins(
    trace: ReadTrace, cols: SeqCol[], uLeft: number, uRight: number,
  ): { wins: { ci: number; lo: number; hi: number; apex: number }[]; maxV: number } {
    const pk = trace.peak_indices || []
    const trim = trace.trim_start ?? 0
    const wins: { ci: number; lo: number; hi: number; apex: number }[] = []
    let maxV = 1
    for (let ci = 0; ci < cols.length; ci++) {
      const c = cols[ci]
      if (c.origIdx < 0) continue
      if (c.xu < uLeft - 2 || c.xu > uRight + 2) continue
      const iRaw = trim + c.origIdx
      const apex = pk[iRaw]
      if (apex == null || apex < 0) continue
      const prevRaw = iRaw > 0 ? pk[iRaw - 1] : null
      const nextRaw = iRaw + 1 < pk.length ? pk[iRaw + 1] : null
      const lo = prevRaw != null ? Math.round((prevRaw + apex) / 2) : Math.max(0, apex - 5)
      const hi = nextRaw != null ? Math.round((apex + nextRaw) / 2) : apex + 5
      wins.push({ ci, lo, hi, apex })
      const ch = trace.channels as Record<string, number[]>
      for (const b of ['A', 'T', 'G', 'C']) {
        const arr = ch[b]
        if (!arr) continue
        for (let s = lo; s <= hi; s++) if (arr[s] > maxV) maxV = arr[s]
      }
    }
    return { wins, maxV }
  }

  /** 单条 read 的峰图条带：逐列取该碱基的采样窗（peak apex 与邻峰中点），
   *  apex 对齐列中心——对任意采样密度/修剪偏移/反向 read 都成立 */
  function drawSeqTrace(
    ctx: CanvasRenderingContext2D, t: ReadTrace, cols: SeqCol[],
    wins: { ci: number; lo: number; hi: number; apex: number }[],
    maxV: number, top: number, baseline: number,
    lineWidth = 1.4,
  ) {
    const ch = t.channels as Record<string, number[]>
    for (const b of ['A', 'T', 'G', 'C']) {
      const arr = ch[b]
      if (!arr) continue
      ctx.beginPath()
      ctx.strokeStyle = CHANNEL_COLORS[b]
      ctx.lineWidth = lineWidth
      let started = false
      for (const w of wins) {
        const c = cols[w.ci]
        const prevXu = w.ci > 0 ? cols[w.ci - 1].xu : c.xu - 1
        const nextXu = w.ci + 1 < cols.length ? cols[w.ci + 1].xu : c.xu + 1
        const xL = seqX((c.xu + prevXu) / 2)
        const xR = seqX((c.xu + nextXu) / 2)
        const xc = seqX(c.xu)
        const loN = Math.max(0, w.lo), hiN = Math.min(arr.length - 1, w.hi)
        for (let s = loN; s <= hiN; s++) {
          const v = arr[s]
          if (v == null) continue
          const x = s <= w.apex
            ? xL + (xc - xL) * (w.apex === w.lo ? 1 : (s - w.lo) / Math.max(1, w.apex - w.lo))
            : xc + (xR - xc) * (w.hi === w.apex ? 1 : (s - w.apex) / Math.max(1, w.hi - w.apex))
          const y = baseline - (baseline - top) * Math.min(1, v / maxV)
          if (!started) { ctx.moveTo(x, y); started = true } else ctx.lineTo(x, y)
        }
      }
      ctx.stroke()
    }
  }

  function drawSeq() {
    const canvas = seqCanvas.value
    const wrap = seqBox.value
    if (!canvas || !wrap) return
    const ctx = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null
    if (!ctx) return   // 无 2d 环境（happy-dom/jsdom 测试）跳过
    const a = analysis.value
    if (!a) return
    const dpr = window.devicePixelRatio || 1
    const w = wrap.clientWidth
    const h = wrap.clientHeight
    canvas.width = Math.max(1, Math.round(w * dpr))
    canvas.height = Math.max(1, Math.round(h * dpr))
    canvas.style.width = `${w}px`
    canvas.style.height = `${h}px`
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)

    const colW = seqColW.value
    const uLeft = seqScrollX.value / colW
    const uRight = (seqScrollX.value + w) / colW
    // 顶部覆盖简图（全景定位条）：固定整参考宽度、不随横向滚动移动，
    // 引物再多/放大多少倍都完整可见（SnapGene 图二式）
    const { ovCovY, ovH } = ovLayoutFor(ovLaneCount.value)
    const rulerH = ovH + 14
    const refRowY = rulerH + 2
    // 简图只覆盖引物实际覆盖的区段（见 ovDomain），无 read 的参考区不占位
    const { lo: ovLo, hi: ovHi } = ovDomain.value
    const ovX = (bp: number) => ((bp - ovLo) / Math.max(1, ovHi - ovLo)) * w
    const sel = selectedReadIdx.value

    // 0a) 覆盖并集绿条 + 轴线 + 变异刻度（低置信黄），与匹配简图同语义
    ctx.fillStyle = '#E2E2E2'
    ctx.fillRect(0, ovCovY + 2, w, 1)
    ctx.fillStyle = 'rgba(46,158,68,0.75)'
    for (const [s, e] of a.coverage_ranges ?? []) {
      ctx.fillRect(ovX(s - 1), ovCovY, Math.max(1, ovX(e) - ovX(s - 1)), 5)
    }
    for (const v of a.variants) {
      ctx.fillStyle = v.confidence === 'low' ? '#E6A700' : '#D0342C'
      ctx.fillRect(ovX(v.ref_pos - 0.5) - 1, ovCovY, 2, 5)
    }

    // 0b) 主视图当前窗口在简图上的投影（蓝框）；画在箭头下层，不切割箭头本体
    const vx1 = Math.max(0, ovX(uLeft))
    const vx2 = Math.min(w, ovX(uRight))
    ctx.fillStyle = 'rgba(36,86,200,0.12)'
    ctx.fillRect(vx1, 2, Math.max(3, vx2 - vx1), ovH - 4)
    ctx.strokeStyle = 'rgba(36,86,200,0.55)'
    ctx.lineWidth = 1
    ctx.strokeRect(vx1 + 0.5, 2.5, Math.max(3, vx2 - vx1) - 1, ovH - 5)

    // 0c) 泳道箭头：每条 read 独立配色，当前视野覆盖到的着重，选中满色加边框并微微加高
    const UNTRUST = 20
    for (let i = 0; i < a.reads.length; i++) {
      const read = a.reads[i]
      const lane = ovLanes.value.laneOf[i]
      if (lane < 0) continue   // 无对齐 read 没有落点，不上图
      const isSel = sel === i
      const y = 6 + lane * (OV_ARROW_H + OV_LANE_GAP) + (isSel ? -1 : 0)
      const ah = OV_ARROW_H + (isSel ? 2 : 0)
      const x1 = ovX(read.ref_start - 1)
      const x2 = ovX(read.ref_end)
      const bw = Math.max(x2 - x1, 4)
      const inView = read.ref_end >= uLeft && read.ref_start <= uRight
      const vis = isReadVisible(i)
      const fwd = read.direction !== '-'
      const head = Math.min(14, Math.max(7, bw * 0.12))
      ctx.beginPath()
      if (fwd) {
        ctx.moveTo(x1, y); ctx.lineTo(x1 + bw - head, y); ctx.lineTo(x1 + bw, y + ah / 2)
        ctx.lineTo(x1 + bw - head, y + ah); ctx.lineTo(x1, y + ah)
      } else {
        ctx.moveTo(x1 + bw, y); ctx.lineTo(x1 + head, y); ctx.lineTo(x1, y + ah / 2)
        ctx.lineTo(x1 + head, y + ah); ctx.lineTo(x1 + bw, y + ah)
      }
      ctx.closePath()
      const rc = readColor(i)
      ctx.fillStyle = isSel ? rc
        : vis ? (inView ? hexA(rc, 0.85) : hexA(rc, 0.5))
        : (inView ? hexA(rc, 0.42) : hexA(rc, 0.24))
      ctx.fill()
      if (isSel) { ctx.strokeStyle = 'rgba(0,0,0,0.55)'; ctx.lineWidth = 1.5; ctx.stroke() }
      ctx.save()
      ctx.clip()
      // 两端不可信区（后端 END_MARGIN=20bp：信号爬升/下降段判读不可靠）
      ctx.fillStyle = 'rgba(255,255,255,0.45)'
      const eL = ovX(read.ref_start - 1 + UNTRUST)
      if (eL > x1) ctx.fillRect(x1, y, eL - x1, ah)
      const eR = ovX(read.ref_end - UNTRUST)
      if (eR < x1 + bw) ctx.fillRect(eR, y, x1 + bw - eR, ah)
      // 双峰位点（read 坐标 → 参考坐标），密集时自然连成"范围"
      ctx.fillStyle = '#F5A623'
      const ocols = seqColsFor(i)
      if (ocols) {
        for (const d of read.mixed_detail || []) {
          const c = ocols.find((cc) => cc.origIdx === d.pos - 1)
          if (!c || c.ref === '-') continue
          const x = ovX(c.xu)
          if (x >= x1 - 2 && x <= x1 + bw + 2) ctx.fillRect(x - 1, y, 2, ah)
        }
      }
      ctx.restore()
      // 名字居中印在箭头内（放得下才画）
      if (bw >= 44) {
        ctx.font = '9px Arial'
        ctx.textAlign = 'center'
        ctx.fillStyle = 'rgba(255,255,255,0.95)'
        ctx.fillText(shortName(read.filename), (x1 + x2) / 2, y + ah - 6, bw - head - 8)
      }
    }

    // 1) 刻度尺 + 纵向网格线（主视图坐标，从简图下沿开始）
    const steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
    const step = steps.find((s) => s * colW >= 60) ?? 10000
    ctx.font = '9px Arial'
    ctx.textAlign = 'center'
    for (let p = Math.max(1, Math.ceil(uLeft / step) * step); p <= uRight; p += step) {
      const x = seqX(p - 0.5)
      ctx.fillStyle = '#999'
      ctx.fillText(p >= 10000 ? `${Math.round(p / 1000)}k` : String(p), x, rulerH - 4)
      ctx.strokeStyle = '#F0F0F0'
      ctx.beginPath()
      ctx.moveTo(x, rulerH)
      ctx.lineTo(x, h)
      ctx.stroke()
    }

    // 2) 参考碱基行（可见 read 的对齐参考并集；覆盖区浅绿底）
    const refCovered = new Map<number, string>()
    for (const ri of visibleReads.value) {
      const cols = seqColsFor(ri)
      if (!cols) continue
      for (const c of cols) {
        if (c.ref !== '-' && !refCovered.has(c.refPos)) refCovered.set(c.refPos, c.ref)
      }
    }
    ctx.fillStyle = 'rgba(46,158,68,0.10)'
    for (const [p] of refCovered) {
      const x = seqX(p - 1)
      if (x > -colW && x < w + colW) ctx.fillRect(x, refRowY, Math.max(colW, 2), SEQ_ROW_H)
    }
    if (colW >= 7) {
      ctx.font = '13px Consolas, monospace'
      ctx.textAlign = 'center'
      ctx.fillStyle = '#333'
      for (const [p, b] of refCovered) {
        const x = seqX(p - 0.5)
        if (x > -colW && x < w + colW) ctx.fillText(b, x, refRowY + 13)
      }
    }
    // 轴上变异刻度块（与匹配简图同语义；低置信差异用黄色区分）
    for (const v of a.variants) {
      const x = seqX(v.ref_pos - 0.5)
      if (x > -4 && x < w + 4) {
        ctx.fillStyle = v.confidence === 'low' ? '#E6A700' : '#D0342C'
        ctx.fillRect(x - 2, refRowY - 3, 4, 3)
      }
    }

    // 3) 各 read 字母行 + 各自峰图条带交接（SnapGene 图三式；选中条带加高）。
    //    行集合 = 视野过滤后的 rowLayouts：没覆盖当前视野的 read 整组不出现
    const layouts = rowLayouts()
    if (!layouts.length) {
      ctx.fillStyle = '#C9C9C9'
      ctx.font = '11px Arial'
      ctx.textAlign = 'left'
      ctx.fillText(visibleReads.value.length
        ? '当前视野没有覆盖中的引物——横向滚动，或点击简图空白跳到有覆盖的位置'
        : '勾选上方引物或点击简图箭头查看峰图', 6, refRowY + SEQ_ROW_H + 24)
    }
    for (const L of layouts) {
      const ri = L.ri
      const read = a.reads[ri]
      if (!read) continue
      const rowY = L.rowY
      const cols = seqColsFor(ri)
      const isSel = sel === ri
      // 行左侧固定名字芯片（滚动时也知道每行是谁）
      const chip = `${read.direction === '-' ? '←' : '→'} ${shortName(read.filename)}${cols ? '' : '（无对齐）'}`
      ctx.font = isSel ? 'bold 10px Arial' : '10px Arial'
      ctx.textAlign = 'left'
      const chipW = Math.min(ctx.measureText(chip).width + 8, w - 4)
      ctx.fillStyle = 'rgba(255,255,255,0.92)'
      ctx.fillRect(0, rowY, chipW, SEQ_ROW_H - 1)
      ctx.fillStyle = !cols ? '#BBB' : hexA(readColor(ri), isSel ? 1 : 0.85)
      ctx.fillText(chip, 4, rowY + 11)

      if (!cols) continue
      const mixedMap = new Map<number, { ratio: number; secondary_base: string }>()
      for (const d of read.mixed_detail || []) mixedMap.set(d.pos - 1, d)
      for (const c of cols) {
        const cx = seqX(c.xu)
        if (cx < -colW * 2 || cx > w + colW * 2) continue
        if (c.read === '-') {
          if (colW >= 7) { ctx.fillStyle = '#D8D8D8'; ctx.fillRect(cx - 1, rowY + 4, 2, 8) }
          continue
        }
        const md = mixedMap.get(c.origIdx)
        if (c.mm) {
          ctx.fillStyle = 'rgba(208,52,44,0.18)'
          ctx.fillRect(cx - colW / 2, rowY, Math.max(colW, 6), SEQ_ROW_H)
        }
        if (md) {
          ctx.fillStyle = 'rgba(230,126,34,0.16)'
          ctx.fillRect(cx - Math.max(colW / 2, 3), rowY, Math.max(colW, 6), SEQ_ROW_H)
        }
        if (colW >= 7) {
          ctx.textAlign = 'center'
          ctx.font = c.mm ? 'bold 13px Consolas, monospace' : '13px Consolas, monospace'
          ctx.fillStyle = c.q > 0 && c.q < 20 ? '#E67E22' : c.mm ? '#B03028' : isSel ? '#111' : '#666'
          ctx.fillText(c.read, cx, rowY + 13)
        }
      }
    }

    // 4) 各 read 的峰图条带：直接交接在该 read 字母行下方，选中者加高并
    //    淡底强调；各条带独立归一幅值，峰形分歧（双峰/错配）一眼可辨
    for (const L of layouts) {
      const read = a.reads[L.ri]
      const cols = seqColsFor(L.ri)
      const isSel = sel === L.ri
      // 条带左缘 read 色竖条与选中淡底：把字母行-条带-简图箭头绑成一组
      ctx.fillStyle = hexA(readColor(L.ri), isSel ? 0.9 : 0.45)
      ctx.fillRect(0, L.stripTop, 3, L.stripH)
      if (isSel) {
        ctx.fillStyle = 'rgba(36,86,200,0.05)'
        ctx.fillRect(3, L.stripTop, w - 3, L.stripH)
      }
      if (!read || !cols) {
        ctx.fillStyle = '#C9C9C9'
        ctx.font = '11px Arial'
        ctx.textAlign = 'left'
        ctx.fillText('该 read 无对齐数据，无法展示峰图', 8, L.stripTop + 20)
        continue
      }
      const mixedMap = new Map<number, { ratio: number; secondary_base: string }>()
      for (const d of read.mixed_detail || []) mixedMap.set(d.pos - 1, d)
      // 合并压缩区灰罩（poly_merged_zones：该 read 对此段无可分辨证据，
      // 逐位判读无效——画在 trace 之下，让"这段峰图不可信"一眼可辨）
      const zoneSpan = new Map<string, { x0: number; x1: number; label: string }>()
      for (const z of read.poly_merged_zones ?? []) {
        let x0 = Infinity
        let x1 = -Infinity
        for (const p of z.positions || []) {
          const c = cols.find((cc) => cc.origIdx === p - 1)
          if (!c || c.read === '-') continue
          const x = seqX(c.xu)
          if (x < x0) x0 = x
          if (x > x1) x1 = x
        }
        if (x0 === Infinity) continue
        x0 = Math.max(x0 - colW / 2, 0)
        x1 = Math.min(x1 + colW / 2, w)
        if (x1 <= 0 || x0 >= w) continue
        const key = `${z.ref_start}-${z.ref_end}`
        const prev = zoneSpan.get(key)
        if (prev) {
          prev.x0 = Math.min(prev.x0, x0)
          prev.x1 = Math.max(prev.x1, x1)
        } else {
          zoneSpan.set(key, { x0, x1, label: `poly(${z.base}) 压缩` })
        }
      }
      for (const z of zoneSpan.values()) {
        if (z.x1 < 0 || z.x0 > w) continue
        ctx.fillStyle = 'rgba(120,120,128,0.13)'
        ctx.fillRect(z.x0, L.stripTop, z.x1 - z.x0, L.stripH)
        ctx.strokeStyle = 'rgba(120,120,128,0.35)'
        ctx.setLineDash([3, 3])
        ctx.strokeRect(z.x0, L.stripTop, z.x1 - z.x0, L.stripH)
        ctx.setLineDash([])
        if (z.x1 - z.x0 > 34 && colW >= 5) {
          ctx.fillStyle = 'rgba(90,90,96,0.8)'
          ctx.font = '9px Arial'
          ctx.textAlign = 'left'
          ctx.fillText('压缩不可判读', z.x0 + 3, L.stripTop + L.stripH - 4)
        }
      }
      // 差异列浅红 / 双峰列橙底贯穿本条带（只画本 read 自己的列）
      for (const c of cols) {
        const cx = seqX(c.xu)
        if (cx < -colW * 2 || cx > w + colW * 2 || c.read === '-') continue
        if (c.mm) {
          ctx.fillStyle = 'rgba(208,52,44,0.06)'
          ctx.fillRect(cx - colW / 2, L.stripTop, Math.max(colW, 6), L.stripH)
        }
        if (mixedMap.get(c.origIdx)) {
          ctx.fillStyle = 'rgba(230,126,34,0.14)'
          ctx.fillRect(cx - Math.max(colW / 2, 3), L.stripTop, Math.max(colW, 6), L.stripH)
        }
      }
      const trace = traceCache.value[L.ri] ?? null
      if (trace) {
        const { wins, maxV } = buildTraceWins(trace, cols, uLeft, uRight)
        if (wins.length) {
          drawSeqTrace(ctx, trace, cols, wins, maxV, L.stripTop, L.baseline, 1.4)
          // 混合位点标注：次峰碱基 + 次要克隆占比（r/(1+r)，与结论口径一致）
          if (colW >= 13) {
            ctx.font = '9px Arial'
            ctx.textAlign = 'center'
            for (const w2 of wins) {
              const c = cols[w2.ci]
              const md = mixedMap.get(c.origIdx)
              if (!md) continue
              ctx.fillStyle = '#E67E22'
              ctx.fillText(`${md.secondary_base}${Math.round((md.ratio / (1 + md.ratio)) * 100)}%`, seqX(c.xu), L.stripTop + 10)
            }
          }
        }
      } else if (traceCache.value[L.ri] === undefined) {
        ctx.fillStyle = '#C9C9C9'
        ctx.font = '11px Arial'
        ctx.textAlign = 'left'
        ctx.fillText('峰图加载中…', 8, L.stripTop + 20)
      } else {
        ctx.fillStyle = '#C9C9C9'
        ctx.font = '11px Arial'
        ctx.textAlign = 'left'
        ctx.fillText('峰图不可用（加载失败或已过期，可重跑分析）', 8, L.stripTop + 20)
      }
      ctx.strokeStyle = '#444'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(0, L.baseline)
      ctx.lineTo(w, L.baseline)
      ctx.stroke()
    }

    // 5) 点选列竖线 + 跳转高亮（从简图下沿开始，避免盖住全景简图）
    if (selRefPos.value != null) {
      const x = seqX(selRefPos.value - 0.5)
      ctx.strokeStyle = '#2456C8'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x, rulerH)
      ctx.lineTo(x, h)
      ctx.stroke()
    }
    if (flashRefPos.value != null) {
      const x = seqX(flashRefPos.value - 1)
      ctx.fillStyle = 'rgba(255, 220, 0, 0.28)'
      ctx.fillRect(x, rulerH, Math.max(colW, 6), h - rulerH)
    }
  }

  // 滚动/缩放/勾选/选中触发重绘；visibleReads 是 push/splice 原位变更，需 deep 才能触发
  watch([visibleReads, seqColW, selectedReadIdx], nextSeqDraw, { deep: true })

  // 峰图带要叠加所有勾选 read：勾选后补拉各自峰图（有缓存/已失败的直接跳过）
  watch(visibleReads, (list) => { for (const ri of list) void loadSeqTrace(ri) }, { deep: true })

  function onSeqResize() {
    seqLayoutTick.value++   // 视野宽度变了 → 行集合/高度重算
    nextSeqDraw()
  }
  onMounted(() => {
    window.addEventListener('resize', onSeqResize)
    seqLayoutTick.value++   // 挂载后才有真实 clientWidth，行布局按视野过滤
  })
  onBeforeUnmount(() => {
    window.removeEventListener('resize', onSeqResize)
    if (flashTimer) clearTimeout(flashTimer)
  })

  return {
    // 模板 ref 与状态（测试经 wrapper.vm 访问同名绑定）
    seqBox, seqCanvas, seqColW, visibleReads, traceCache, seqTraceLoading,
    selRefPos, flashRefPos, seqInfo, jumpInput, seqScrollX, seqWrapH, seqSpacerW,
    selectedReadIdx, ovLanes, ovLaneCount, ovDomain,
    // 交互函数（模板/父组件事件回调）
    readColor, loadSeqTrace, isReadVisible, toggleRead, seqZoom, seqFit,
    jumpToRefPos, rowLayouts, jumpToVariant, openReadInSeqviz, selectRead,
    onSeqScroll, onSeqWheel, onSeqClick, resetSeqViz, scrollToRefPos,
  }
}
