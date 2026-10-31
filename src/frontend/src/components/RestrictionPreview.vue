<script setup lang="ts">
/**
 * 双酶切示意：画**成品构建体**（被切走的载体片段已经不在真实质粒上）。
 *
 * 坐标全部来自后端预览（它用占位基因跑了一遍真实组装），所以图上基因、
 * 位点、启动子的位置与提交后拿到的结果一致；比例按真实 bp 换算。
 */
import { computed } from 'vue'
import type { RestrictionPreview } from '@/types'

const props = defineProps<{
  preview: RestrictionPreview | null
  loading?: boolean
  error?: string
  /** 多切点酶切知情确认（v-model）：骨架重切风险由用户决定是否接受 */
  allowBackboneCuts?: boolean
  /** 用户选择的骨架片段（v-model）：null = 自动推荐 */
  cutPair?: [number, number] | null
}>()

const emit = defineEmits<{
  'update:allowBackboneCuts': [boolean]
  'update:cutPair': [[number, number] | null]
}>()

const VB_W = 1000
const VB_H = 176
const PAD = 28
const BACKBONE_Y = 96
const GENE_H = 18
const GENE_Y = 68
const MARK_Y = 116

/** 窗口：基因 + 两个位点 + 附近元件，再留一点边距 */
const window0 = computed(() => {
  const p = props.preview
  if (!p) return null
  const points: number[] = [p.gene.start, p.gene.end]
  for (const site of p.sites) points.push(site.position, site.end)
  for (const feat of markerFeatures.value) points.push(feat.start, feat.end)
  const lo = Math.min(...points)
  const hi = Math.max(...points)
  const pad = Math.max(80, Math.round((hi - lo) * 0.3))
  return { start: lo - pad, end: hi + pad }
})

const windowSpan = computed(() => {
  const w = window0.value
  return w ? Math.max(1, w.end - w.start) : 1
})

function toX(pos: number): number {
  const w = window0.value
  if (!w) return PAD
  return PAD + ((pos - w.start) / windowSpan.value) * (VB_W - PAD * 2)
}

const markerTypes = ['promoter', 'terminator', 'rbs', 'regulatory']

/** 切点标签防重叠：全部切点都标「酶 (坐标)」，按 x 排序贪心分行，最多两行
 *  （近距切点如 2136/2208 单行会叠成 "230872"，实测不可读）；超两行的极端
 *  密集场景挤最后一行，保底不省名 */
const SITE_LABEL_MAX_ROWS = 2
const SITE_LABEL_Y = [20, 9]
const siteLabelLayout = computed(() => {
  const p = props.preview
  if (!p) return []
  const items = p.sites.map((site) => {
    const text = `${site.enzyme} (${site.cut})`
    return { site, x: toX(site.cut), text, major: !!site.junction, width: text.length * 6.2 + 8 }
  }).sort((a, b) => a.x - b.x)
  const rowEnds: number[] = []
  return items.map((it) => {
    let row = rowEnds.findIndex((end) => it.x - it.width / 2 > end)
    if (row === -1) {
      row = rowEnds.length < SITE_LABEL_MAX_ROWS
        ? rowEnds.push(it.x + it.width / 2) - 1
        : SITE_LABEL_MAX_ROWS - 1
    }
    rowEnds[row] = it.x + it.width / 2
    return { ...it, y: SITE_LABEL_Y[row] }
  })
})

const markerFeatures = computed(() => {
  const p = props.preview
  if (!p) return []
  return (p.features || []).filter((f) => markerTypes.includes((f.type || '').toLowerCase()))
})

/** 元件按类型分色（与结果页环形图 palette 一致）：启动子最醒目——
 *  它决定基因方向，是预览图上最需要看见的元件 */
const MARKER_STYLES: Record<string, { fill: string; label: string; height: number }> = {
  promoter: { fill: '#e05252', label: '#c53030', height: 18 },
  terminator: { fill: '#47b096', label: '#2c7a67', height: 12 },
  rbs: { fill: '#f3ab68', label: '#b5622d', height: 10 },
  regulatory: { fill: '#a0aec0', label: '#718096', height: 10 },
}

function markerStyle(type: string | undefined): { fill: string; label: string; height: number } {
  return MARKER_STYLES[(type || '').toLowerCase()] || MARKER_STYLES.regulatory
}

/** 标注防重叠：按 x 排序从左往右放，与前一标签相交就换下一行
 *  （启动子与 RBS 紧邻时标签会叠在一起，实测 "RBS/promoter" 完全不可读） */
const markerLabelLayout = computed(() => {
  const items = markerFeatures.value.map((feat) => {
    const x = (toX(feat.start) + toX(feat.end)) / 2
    return { feat, x, label: shortName(feat.name) }
  }).sort((a, b) => a.x - b.x)
  const rowEnds: number[] = []
  return items.map((it) => {
    const width = it.label.length * 6.5 + 10
    let row = rowEnds.findIndex((end) => it.x - width / 2 > end)
    if (row === -1) {
      rowEnds.push(it.x + width / 2)
      row = rowEnds.length - 1
    } else {
      rowEnds[row] = it.x + width / 2
    }
    return { ...it, row }
  })
})

const LABEL_ROW_H = 13
const LABEL_Y0 = 134
const MAX_LABEL_ROWS = 3

/** 元件名称太长时截断（图上只留识别用的部分） */
function shortName(name: string, max = 18): string {
  const text = (name || '').trim()
  return text.length > max ? text.slice(0, max - 1) + '…' : text
}

function arrowPath(x1: number, x2: number, y: number, h: number, dir: 1 | -1): string {
  const top = y - h / 2
  const bottom = y + h / 2
  const head = Math.min(Math.max(8, Math.abs(x2 - x1) * 0.25), 18)
  if (dir === 1) {
    const xh = Math.max(x1 + 1, x2 - head)
    return `M${x1},${top} L${xh},${top} L${x2},${y} L${xh},${bottom} L${x1},${bottom} Z`
  }
  const xh = Math.min(x2 - 1, x1 + head)
  return `M${x2},${top} L${xh},${top} L${x1},${y} L${xh},${bottom} L${x2},${bottom} Z`
}

const strand = computed(() => (props.preview?.strand === '-' ? -1 : 1) as 1 | -1)
const strandText = computed(() => (strand.value === -1 ? '负链（-）' : '正链（+）'))

const geneX1 = computed(() => (props.preview ? toX(props.preview.gene.start) : 0))
const geneX2 = computed(() => (props.preview ? toX(props.preview.gene.end) : 0))
const geneLabelFits = computed(() => geneX2.value - geneX1.value > 190)

/** 5′ 端在基因哪一侧：负链基因的 5′ 端在右边 */
const fiveX = computed(() => (strand.value === -1 ? geneX2.value : geneX1.value))
const threeX = computed(() => (strand.value === -1 ? geneX1.value : geneX2.value))

const summary = computed(() => {
  const p = props.preview
  if (!p) return ''
  // 切点数：多切点酶在成品图谱上会有多个切点（如 pcDNA3.1 的 NcoI 4 个），
  // 选择时就看见，避免图谱上「插入看起来落在别的酶之间」的误读
  const count = (name: string) => {
    const n = p.enzyme_counts?.[name]
    return n == null ? '' : n > 1 ? `（${n} 个切点）` : ''
  }
  return `5′ 端接 ${p.gene_5prime_enzyme}${count(p.gene_5prime_enzyme)}`
    + ` · 3′ 端接 ${p.gene_3prime_enzyme}${count(p.gene_3prime_enzyme)} · ${strandText.value}`
})

const evidenceText = computed(() => {
  const p = props.preview
  if (!p) return ''
  if (p.source === 'user') return '方向由你的填写决定（同源臂定位 / 手动指定方向）'
  const promoter = p.evidence?.find((e) => e.kind === 'promoter')
  const parts: string[] = []
  if (p.ambiguous) {
    parts.push('插入区间两侧都有启动子/RBS 证据，无法自动判定 —— 请手动选择方向')
  }
  if (p.source === 'auto_detected') parts.push('载体没有元件注释，启动子由序列自动识别')
  if (promoter) {
    const where = promoter.start && promoter.end ? `（${promoter.start}..${promoter.end}，${promoter.strand === '-' ? '负链' : '正链'}）` : ''
    parts.push(
      `依据 ${promoter.name}${where}` + (promoter.gap != null ? `，距切点 ${promoter.gap} bp` : ''),
    )
  } else if (!p.ambiguous && p.source === 'default') {
    parts.push('载体上没有可判定的启动子/RBS，按默认方向（5′ 端接上游切点）')
  }
  return parts.join('；')
})

const riskWarnings = computed(() => {
  const noisy = ['推测 MCS', '已按 MCS 区间替换', '基本落在被切除区间', '跨过切点']
  return (props.preview?.warnings || []).filter((w) => !noisy.some((n) => w.includes(n))).slice(0, 3)
})

const rulerTicks = computed(() => {
  const w = window0.value
  if (!w) return []
  return [w.start, w.end]
})

// ---- 片段选择图：原载体坐标，按消化带点击选择 ----
const VEC_H = 104
const VEC_Y = 62
const VEC_X1 = 90
const VEC_X2 = VB_W - 60
const vectorLength = computed(() => props.preview?.vector.length ?? 1)

const cutMarks = computed(() => props.preview?.cut_marks ?? [])
const bands = computed(() => props.preview?.cut_options ?? [])

/** 选中带：用户点击优先（cutPair=补集切点对），否则无高亮 */
const activePair = computed<[number, number] | null>(() => props.cutPair ?? props.preview?.cut_pair ?? null)

const vecX = (pos: number) => VEC_X1 + ((pos - 1) / Math.max(1, vectorLength.value)) * (VEC_X2 - VEC_X1)
/** 酶名 → 稳定配色（图①刻度/标签与图②带端刻度同色，便于对应） */
const ENZYME_COLORS = ['#2b6cb0', '#2f855a', '#b7791f', '#c05621', '#6b46c1', '#2c7a7b', '#b83280', '#4a5568']
const enzymeColorCache = new Map<string, string>()
const enzymeColor = (name: string) => {
  let c = enzymeColorCache.get(name)
  if (!c) {
    c = ENZYME_COLORS[enzymeColorCache.size % ENZYME_COLORS.length]
    enzymeColorCache.set(name, c)
  }
  return c
}

/** 标注文本宽度估算：CJK 按 10px，其余按 6.4px */
const textW = (t: string) => [...t].reduce((w, ch) => w + (ch.charCodeAt(0) > 0x2e7f ? 10 : 6.4), 0) + 8

/** 点了某条带：把带换算成补集切点对回写（回收=切走补集）；再点已选中的带 = 取消 */
function pickBand(i: number) {
  const b = bands.value[i]
  if (!b) return
  const pair: [number, number] = [b.left_cut, b.right_cut]
  const cur = props.cutPair ?? props.preview?.cut_pair ?? null
  emit('update:cutPair', cur && cur[0] === pair[0] && cur[1] === pair[1] ? null : pair)
}

/** 带在图上的区间（原载体线性展开，跨原点带拆两段） */
function bandSegs(o: { left_cut: number; right_cut: number; removed_bp: number; kept_bp: number }) {
  // cut_pair (l,r) 切走 (l→r]；回收的带 = 补集 (r→l]
  const l = o.left_cut, r = o.right_cut
  return l < r
    ? [{ x1: vecX(r), x2: VEC_X2 }, { x1: VEC_X1, x2: vecX(l) }]
    : [{ x1: vecX(r), x2: vecX(l) }]
}
/** 带两端切点的着色刻度位置（与图①同色） */
function bandTicks(o: { left_cut: number; right_cut: number; left_enzyme: string; right_enzyme: string }) {
  return [
    { x: vecX(o.right_cut), c: enzymeColor(o.right_enzyme) },
    { x: vecX(o.left_cut), c: enzymeColor(o.left_enzyme) },
  ]
}

const isActive = (o: { left_cut: number; right_cut: number }) => {
  const p = activePair.value
  return !!p && p[0] === o.left_cut && p[1] === o.right_cut
}

/** 每条带的长度标注：常显，居中于带的最宽段，两行防重叠 */
const bandLabelLayout = computed(() => {
  const rows = [{ end: -1, y: VEC_Y - 16 }, { end: -1, y: VEC_Y - 30 }]
  return bands.value.map((b) => {
    const segs = bandSegs(b)
    const widest = segs.reduce((a, s) => (s.x2 - s.x1 > a.x2 - a.x1 ? s : a), segs[0])
    const sites = `${b.left_enzyme}(${b.left_cut})–${b.right_enzyme}(${b.right_cut})`
    const text = isActive(b)
      ? `插入位置 · ${sites} · 回收 ${b.kept_bp.toLocaleString()} bp`
      : `${sites} · 回收 ${b.kept_bp.toLocaleString()} bp`
    const width = textW(text)
    const x = Math.min(Math.max((widest.x1 + widest.x2) / 2, VEC_X1 + width / 2), VEC_X2 - width / 2)
    let row = rows.findIndex((r) => x - width / 2 > r.end)
    if (row === -1) row = 0
    rows[row].end = x + width / 2
    return { x, y: rows[row].y, text }
  })
})

/** 位点标签：上下两行就近排布（标签贴着自己的刻度），同排挤时左右让位，引线连回刻度 */
const markLayout = computed(() => {
  const items = cutMarks.value.map((m) => {
    const text = `${m.enzyme} (${m.vector_cut})`
    return { m, x: vecX(m.vector_cut), text, width: textW(text), cx: 0, y: 0 }
  }).sort((a, b) => a.x - b.x)
  const rows = [{ cursor: VEC_X1 - 4, y: VEC_Y - 16 }, { cursor: VEC_X1 - 4, y: VEC_Y + 30 }]
  return items.map((it) => {
    const want = it.x - it.width / 2
    // 两行各算一次让位距离，位移小者优先；平手选上行（就近、引线短）
    const cands = rows
      .map((r, i) => ({ i, left: Math.max(want, r.cursor) }))
      .sort((a, b) => (a.left - want) - (b.left - want) || a.i - b.i)
    const pick = cands[0]
    const left = Math.min(Math.max(pick.left, VEC_X1 - 4), VEC_X2 - it.width)
    rows[pick.i].cursor = left + it.width
    it.cx = left + it.width / 2
    it.y = rows[pick.i].y
    return { ...it, color: enzymeColor(it.m.enzyme) }
  })
})
</script>

<template>
  <div class="rp">
    <p v-if="error" class="rp-error">⚠️ {{ error }}</p>
    <p v-else-if="loading && !preview" class="rp-loading">正在计算构建体…</p>

    <template v-if="preview && !error">
      <p class="rp-summary" :class="{ 'rp-summary-minus': strand === -1 }">{{ summary }}</p>

      <div v-if="preview.backbone_cut_risk" class="rp-recut" role="alert">
        <p v-for="w in preview.warnings.filter((x) => x.includes('保留骨架'))" :key="w" class="rp-warn rp-recut-text">⚠️ {{ w }}</p>
        <label class="rp-recut-ack">
          <input
            type="checkbox"
            :checked="allowBackboneCuts"
            data-testid="allow-backbone-cuts"
            @change="emit('update:allowBackboneCuts', ($event.target as HTMLInputElement).checked)"
          />
          我了解该酶会把载体骨架切成多段，有意使用多切点酶切方案
        </label>
      </div>


      <!-- 位点分布：两把酶在原载体上的全部位点（只看分布，不可点） -->
      <svg v-if="cutMarks.length" class="rp-svg rp-vector-svg" :viewBox="`0 0 ${VB_W} ${VEC_H}`"
           role="img" aria-label="酶切位点分布">
        <text :x="PAD" y="14" class="rp-fig-title">酶切位点分布（原载体 {{ vectorLength.toLocaleString() }} bp）</text>
        <rect :x="VEC_X1" :y="VEC_Y - 2" :width="VEC_X2 - VEC_X1" height="4" fill="#cbd5e0" />
        <g v-for="(m, i) in markLayout" :key="`vec-mark-${i}`">
            <line :x1="m.x" :x2="m.cx" :y1="m.y < VEC_Y ? VEC_Y - 12 : VEC_Y + 12" :y2="m.y < VEC_Y ? m.y + 3 : m.y - 9"
                  :stroke="m.color" stroke-width="1" opacity="0.5" />
            <line :x1="m.x" :x2="m.x" :y1="VEC_Y - 12" :y2="VEC_Y + 12"
                  :stroke="m.color" stroke-width="2" />
            <text :x="m.cx" :y="m.y" class="rp-vec-mark" :style="{ fill: m.color }" text-anchor="middle">{{ m.text }}</text>
        </g>
        <text :x="VEC_X1" :y="VEC_Y + 38" class="rp-ruler" text-anchor="start">1</text>
        <text :x="VEC_X2" :y="VEC_Y + 38" class="rp-ruler" text-anchor="end">{{ vectorLength.toLocaleString() }}</text>
      </svg>

      <!-- 插入位置选择：可回收的消化带常显（浅绿），点击选中（深绿+「插入位置」），再点取消 -->
      <svg v-if="bands.length" class="rp-svg rp-vector-svg" :viewBox="`0 0 ${VB_W} ${VEC_H}`"
           role="img" aria-label="插入位置选择">
        <text :x="PAD" y="14" class="rp-fig-title">选择插入位置（点击要回收的片段，再点一次取消）</text>
        <rect :x="VEC_X1" :y="VEC_Y - 2" :width="VEC_X2 - VEC_X1" height="4" fill="#cbd5e0" />
        <g v-for="(o, i) in bands" :key="`band-${i}`" class="rp-band" @click="pickBand(i)">
          <title>回收 {{ o.kept_bp.toLocaleString() }} bp（{{ o.left_enzyme }}({{ o.left_cut }}) × {{ o.right_enzyme }}({{ o.right_cut }})）</title>
          <!-- 热区 = 该带自身区间（跨原点拆两段），两条带互不遮挡 -->
          <rect v-for="(seg, si) in bandSegs(o)" :key="`bh-${i}-${si}`"
                :x="seg.x1" :y="VEC_Y - 16" :width="Math.max(seg.x2 - seg.x1, 6)" height="34" fill="transparent" />
          <!-- 带本体常显：浅绿 = 可选，深绿 = 已选中 -->
          <rect v-for="(seg, si) in bandSegs(o)" :key="`bs-${i}-${si}`"
                :x="seg.x1" :y="VEC_Y - 5" :width="Math.max(seg.x2 - seg.x1, 2)" height="10" rx="3"
                :fill="isActive(o) ? '#38a169' : '#a2d5ab'" opacity="0.9" />
          <!-- 带两端切点着色刻度（与图①同色） -->
          <line v-for="(t, ti) in bandTicks(o)" :key="`bt-${i}-${ti}`"
                :x1="t.x" :x2="t.x" :y1="VEC_Y - 11" :y2="VEC_Y + 11" :stroke="t.c" stroke-width="2" />
          <text v-if="bandLabelLayout[i]" :x="bandLabelLayout[i].x" :y="bandLabelLayout[i].y"
                :class="['rp-band-label', { 'rp-band-label-active': isActive(o) }]" text-anchor="middle">
            {{ bandLabelLayout[i].text }}
          </text>
        </g>
        <text :x="VEC_X1" :y="VEC_Y + 38" class="rp-ruler" text-anchor="start">1</text>
        <text :x="VEC_X2" :y="VEC_Y + 38" class="rp-ruler" text-anchor="end">{{ vectorLength.toLocaleString() }}</text>
      </svg>

      <!-- 拼接完成的构建体（提交后拿到的成品；被切走的片段已不在里面） -->
      <svg class="rp-svg" :viewBox="`0 0 ${VB_W} ${VB_H + 12}`" role="img" :aria-label="summary">
        <text :x="PAD" y="12" class="rp-fig-title">插入后的构建体（{{ preview.construct_length.toLocaleString() }} bp）</text>
        <g transform="translate(0, 14)">
        <!-- 载体骨架 -->
        <line :x1="PAD - 8" :x2="VB_W - PAD + 8" :y1="BACKBONE_Y" :y2="BACKBONE_Y"
              stroke="#4a5568" stroke-width="2" />
        <line :x1="PAD - 8" :x2="VB_W - PAD + 8" :y1="BACKBONE_Y + 4" :y2="BACKBONE_Y + 4"
              stroke="#cbd5e0" stroke-width="1" />

        <!-- 切点：竖线 + 酶名/坐标。junction（决定末端的那一个）加粗强调 -->
        <g v-for="(item, i) in siteLabelLayout"
           :key="`${item.site.role}-${item.site.enzyme}-${item.site.cut}-${i}`">
          <line :x1="item.x" :x2="item.x" y1="30" :y2="BACKBONE_Y - 2"
                :stroke="item.major ? '#2d3748' : '#a0aec0'"
                :stroke-width="item.major ? 1.5 : 1" />
          <text :x="item.x" :y="item.y" class="rp-site"
                :class="{ 'rp-site-minor': !item.major }" text-anchor="middle">
            {{ item.text }}
          </text>
        </g>

        <!-- 基因：箭头方向 = 插入链 -->
        <path :d="arrowPath(geneX1, geneX2, GENE_Y, GENE_H, strand)" fill="#6b46c1" opacity="0.92" />
        <text v-if="geneLabelFits" :x="(geneX1 + geneX2) / 2" :y="GENE_Y + 4"
              class="rp-gene-label" text-anchor="middle">
          基因 {{ preview.gene.length }} bp{{ preview.gene.estimated ? '（估算）' : '' }}
        </text>
        <text v-else :x="(geneX1 + geneX2) / 2" y="52" class="rp-gene-label" text-anchor="middle">
          基因 {{ preview.gene.length }} bp{{ preview.gene.estimated ? '（估算）' : '' }}
        </text>

        <!-- 5′ / 3′ 端标注 -->
        <text :x="fiveX" :y="GENE_Y + GENE_H / 2 + 30" class="rp-end" text-anchor="middle">5′</text>
        <text :x="threeX" :y="GENE_Y + GENE_H / 2 + 30" class="rp-end" text-anchor="middle">3′</text>

        <!-- 附近元件（启动子/终止子/RBS）：按类型分色，启动子最大最红；
             箭头方向 = 元件自身 strand；标签防重叠自动分行 -->
        <g v-for="(item, i) in markerLabelLayout" :key="`${item.feat.name}-${item.feat.start}-${i}`">
          <path
            :d="arrowPath(toX(item.feat.start), toX(item.feat.end), MARK_Y, markerStyle(item.feat.type).height, item.feat.strand === '-' ? -1 : 1)"
            :fill="markerStyle(item.feat.type).fill" opacity="0.92"
          >
            <title>{{ item.feat.name }}（{{ item.feat.type }}）</title>
          </path>
          <text
            v-if="item.row < MAX_LABEL_ROWS"
            :x="item.x" :y="LABEL_Y0 + item.row * LABEL_ROW_H"
            class="rp-marker" :fill="markerStyle(item.feat.type).label" text-anchor="middle"
            :font-weight="item.feat.type?.toLowerCase() === 'promoter' ? 700 : 400"
          >
            {{ item.label }}
          </text>
        </g>

        <!-- 窗口刻度 -->
        <g>
          <line :x1="PAD" :x2="VB_W - PAD" y1="168" y2="168" stroke="#a0aec0" stroke-width="1" />
          <text :x="PAD" y="164" class="rp-ruler" text-anchor="start">{{ rulerTicks[0] }}</text>
          <text :x="VB_W - PAD" y="164" class="rp-ruler" text-anchor="end">{{ rulerTicks[1] }}</text>
        </g>
        </g>
      </svg>

      <p class="rp-meta">
        切除 {{ preview.removed_bp }} bp 载体片段 · 构建体 {{ preview.construct_length.toLocaleString() }} bp
        <span v-if="preview.gene.estimated">（按当前输入估算基因长度，提交后以实际优化序列为准）</span>
      </p>
      <p v-if="evidenceText" class="rp-evidence" :class="{ 'rp-warn': preview.ambiguous }">{{ evidenceText }}</p>
      <p v-for="warn in riskWarnings" :key="warn" class="rp-warn">⚠️ {{ warn }}</p>
      <p v-if="loading" class="rp-loading">更新中…</p>
    </template>
  </div>
</template>

<style scoped>
.rp {
  border: 1px solid #e2e8f0;
  border-radius: 0.5rem;
  padding: 0.75rem 0.9rem 0.6rem;
  background: #fbfcfe;
  margin-top: 0.5rem;
}

.rp-svg {
  width: 100%;
  height: auto;
  display: block;
}

.rp-summary {
  margin: 0 0 0.35rem;
  font-size: 0.9rem;
  font-weight: 600;
  color: #2d3748;
}

.rp-summary-minus {
  color: #553c9a;
}

.rp-site {
  font-size: 11px;
  fill: #2d3748;
  font-weight: 600;
}

.rp-site-minor {
  font-size: 10px;
  fill: #718096;
  font-weight: 400;
}

.rp-gene-label {
  font-size: 11px;
  fill: #fff;
  font-weight: 600;
}

.rp-end {
  font-size: 11px;
  fill: #4a5568;
  font-weight: 600;
}

.rp-marker {
  font-size: 10px;
  fill: #276749;
}

.rp-ruler {
  font-size: 10px;
  fill: #718096;
}

.rp-vector-svg {
  margin-top: 0.2rem;
  border-top: 1px dashed #e2e8f0;
}

.rp-band-label {
  font-size: 10px;
  fill: #2f855a;
  font-weight: 600;
}

.rp-band-label-active {
  fill: #22543d;
  font-size: 10.5px;
}

.rp-band {
  cursor: pointer;
}

.rp-fig-title {
  font-size: 11.5px;
  fill: #2d3748;
  font-weight: 600;
}

.rp-vec-mark {
  font-size: 9.5px;
  fill: #2d3748;
  font-weight: 600;
}

.rp-meta {
  margin: 0.15rem 0 0;
  font-size: 0.78rem;
  color: #4a5568;
}

.rp-recut {
  margin: 0.3rem 0 0.1rem;
  padding: 0.45rem 0.6rem;
  border: 1px solid #f6ad55;
  border-radius: 0.375rem;
  background: #fffaf0;
}

.rp-recut-ack {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0.3rem 0 0;
  font-size: 0.8rem;
  color: #7b341e;
  font-weight: 600;
  cursor: pointer;
}

.rp-evidence {
  margin: 0.2rem 0 0;
  font-size: 0.78rem;
  color: #2f855a;
}

.rp-warn {
  margin: 0.2rem 0 0;
  font-size: 0.78rem;
  color: #c05621;
}

.rp-error {
  margin: 0;
  font-size: 0.82rem;
  color: #c53030;
}

.rp-loading {
  margin: 0.2rem 0 0;
  font-size: 0.78rem;
  color: #718096;
}
</style>
