<script setup lang="ts">
/**
 * 双酶切示意：画**成品构建体**（被切走的载体片段已经不在真实质粒上）。
 *
 * 坐标全部来自后端预览（它用占位基因跑了一遍真实组装），所以图上基因、
 * 位点、启动子的位置与提交后拿到的结果一致；比例按真实 bp 换算。
 */
import { computed } from 'vue'
import type { RestrictionPreview } from '@/types'
import CutPairPicker from '@/components/CutPairPicker.vue'

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

// 片段选择的双向中转：props 只读，经 emit 回写父组件
const localCutPair = computed<[number, number] | null>({
  get: () => props.cutPair ?? null,
  set: (v) => emit('update:cutPair', v),
})

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

// ---- 原载体示意条：全部用原载体坐标（cut_marks / cut_options 同口径） ----
const VEC_H = 74
const VEC_Y = 40
const VEC_X1 = 90
const VEC_X2 = VB_W - 60
const vectorLength = computed(() => props.preview?.vector.length ?? 1)

const cutMarks = computed(() => props.preview?.cut_marks ?? [])

/** 当前所选片段（与下拉框同源：cut_pair，未选时取首个候选=自动推荐） */
const activePair = computed(() => {
  const p = props.preview
  if (!p) return null
  if (p.cut_pair) return p.cut_pair
  const first = p.cut_options?.[0]
  return first ? [first.left_cut, first.right_cut] : null
})

const vecX = (pos: number) => VEC_X1 + ((pos - 1) / Math.max(1, vectorLength.value)) * (VEC_X2 - VEC_X1)

const excisedBp = computed(() => {
  const pair = activePair.value
  if (!pair || vectorLength.value <= 1) return 0
  const d = pair[1] - pair[0]
  return d > 0 ? d : d + vectorLength.value
})

/** 保留骨架在切点两侧各是一段；不跨原点时左段为空数组 */
const keptSegments = computed(() => {
  const pair = activePair.value
  const L = vectorLength.value
  if (!pair || L <= 1) return []
  const [l, r] = pair
  if (l < r) return [{ from: r, to: L, label: `${L - r} bp` }]
  return [{ from: r, to: l, label: `${l - r} bp` }]
})

const segLeft = computed(() => {
  const seg = keptSegments.value[0]
  if (!seg) return null
  return { x1: vecX(seg.from), x2: vecX(seg.to), label: seg.label }
})

/** 废片段区间（原载体上 (l, r]，跨原点时拆两段只画主段） */
const segExcised = computed(() => {
  const pair = activePair.value
  if (!pair || vectorLength.value <= 1) return null
  const [l, r] = pair
  return l < r ? { x1: vecX(l), x2: vecX(r) } : { x1: vecX(l), x2: VEC_X2 }
})

/** 候选位点标签防重叠：按 x 排序贪心分行（多切点酶近距位点会叠） */
const markLayout = computed(() => {
  const items = cutMarks.value.map((m) => {
    const text = `${m.enzyme} (${m.vector_cut})`
    return { m, x: vecX(m.vector_cut), text, width: text.length * 6.2 + 6 }
  }).sort((a, b) => a.x - b.x)
  const rows = [{ end: -1, y: VEC_Y - 20 }, { end: -1, y: VEC_Y + 26 }]
  return items.map((it) => {
    let row = rows.findIndex((r) => it.x - it.width / 2 > r.end)
    if (row === -1) row = 0
    rows[row].end = it.x + it.width / 2
    return { ...it, y: rows[row].y, in_excised: it.m.in_excised }
  })
})
</script>

<template>
  <div class="rp">
    <p v-if="error" class="rp-error">⚠️ {{ error }}</p>
    <p v-else-if="loading && !preview" class="rp-loading">正在计算构建体…</p>

    <template v-if="preview && !error">
      <p class="rp-summary" :class="{ 'rp-summary-minus': strand === -1 }">{{ summary }}</p>

      <CutPairPicker
        :options="preview.cut_options ?? []"
        v-model="localCutPair"
      />

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

      <svg class="rp-svg" :viewBox="`0 0 ${VB_W} ${VB_H}`" role="img" :aria-label="summary">
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
      </svg>

      <!-- 原载体示意条（原载体坐标，与「回收片段」下拉同一套数字）：回收
           片段 = 绿色线，被切除的废片段 = 橙色区间（内部候选位点可见），
           两把酶的全部候选位点标「酶 (坐标)」。构建体主图是切完之后的图，
           原理上画不出正在选择的废片段，所以这一条单独用原载体坐标画 -->
      <svg v-if="cutMarks.length" class="rp-svg rp-vector-svg" :viewBox="`0 0 ${VB_W} ${VEC_H}`"
           role="img" aria-label="原载体候选切点示意">
        <text :x="PAD" y="14" class="rp-ruler">原载体 {{ vectorLength.toLocaleString() }} bp · 废片段 {{ excisedBp.toLocaleString() }} bp（橙色区间，胶回收后丢弃）</text>
        <rect :x="VEC_X1" :y="VEC_Y - 2" :width="VEC_X2 - VEC_X1" height="4" fill="#cbd5e0" />
        <rect v-if="segLeft" :x="segLeft.x1" :y="VEC_Y - 4" :width="segLeft.x2 - segLeft.x1" height="8" rx="2" fill="#38a169" />
        <text v-if="segLeft" :x="(segLeft.x1 + segLeft.x2) / 2" :y="VEC_Y + 22" class="rp-vec-kept" text-anchor="middle">回收 {{ segLeft.label }}</text>
        <rect v-if="segExcised" :x="segExcised.x1" :y="VEC_Y - 9" :width="Math.max(segExcised.x2 - segExcised.x1, 3)" height="18" fill="#f6ad55" opacity="0.45" />
        <text v-if="segExcised && segExcised.x2 - segExcised.x1 > 46" :x="(segExcised.x1 + segExcised.x2) / 2" :y="VEC_Y + 4" class="rp-vec-excised" text-anchor="middle">废片段</text>
        <g v-for="(m, i) in markLayout" :key="`vec-mark-${i}`">
          <line :x1="m.x" :x2="m.x" :y1="VEC_Y - 12" :y2="VEC_Y + 12"
                :stroke="m.in_excised ? '#dd6b20' : '#38a169'" stroke-width="1.5" />
          <text :x="m.x" :y="m.y" class="rp-vec-mark" text-anchor="middle">{{ m.text }}</text>
        </g>
        <text :x="VEC_X1" :y="VEC_Y + 38" class="rp-ruler" text-anchor="start">1</text>
        <text :x="VEC_X2" :y="VEC_Y + 38" class="rp-ruler" text-anchor="end">{{ vectorLength.toLocaleString() }}</text>
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

.rp-vec-kept {
  font-size: 10px;
  fill: #276749;
  font-weight: 600;
}

.rp-vec-excised {
  font-size: 10px;
  fill: #7b341e;
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
