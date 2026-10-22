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
}>()

const VB_W = 1000
const VB_H = 150
const PAD = 28
const BACKBONE_Y = 96
const GENE_H = 18
const GENE_Y = 68
const MARK_H = 12
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

const markerFeatures = computed(() => {
  const p = props.preview
  if (!p) return []
  return (p.features || []).filter((f) => markerTypes.includes((f.type || '').toLowerCase()))
})

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
  return `5′ 端接 ${p.gene_5prime_enzyme} · 3′ 端接 ${p.gene_3prime_enzyme} · ${strandText.value}`
})

const evidenceText = computed(() => {
  const p = props.preview
  if (!p) return ''
  if (p.source === 'user') return '按你选择的方向组装（反义构建/手动指定）'
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
</script>

<template>
  <div class="rp">
    <p v-if="error" class="rp-error">⚠️ {{ error }}</p>
    <p v-else-if="loading && !preview" class="rp-loading">正在计算构建体…</p>

    <template v-if="preview && !error">
      <p class="rp-summary" :class="{ 'rp-summary-minus': strand === -1 }">{{ summary }}</p>

      <svg class="rp-svg" :viewBox="`0 0 ${VB_W} ${VB_H}`" role="img" :aria-label="summary">
        <!-- 载体骨架 -->
        <line :x1="PAD - 8" :x2="VB_W - PAD + 8" :y1="BACKBONE_Y" :y2="BACKBONE_Y"
              stroke="#4a5568" stroke-width="2" />
        <line :x1="PAD - 8" :x2="VB_W - PAD + 8" :y1="BACKBONE_Y + 4" :y2="BACKBONE_Y + 4"
              stroke="#cbd5e0" stroke-width="1" />

        <!-- 切点：竖线 + 酶名/坐标 -->
        <g v-for="site in preview.sites" :key="site.role + site.enzyme">
          <line :x1="toX(site.cut)" :x2="toX(site.cut)" :y1="30" :y2="BACKBONE_Y - 2"
                stroke="#2d3748" stroke-width="1.5" />
          <text :x="toX(site.cut)" y="20" class="rp-site" text-anchor="middle">
            {{ site.enzyme }} ({{ site.cut }})
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

        <!-- 附近元件（启动子/终止子/RBS）：箭头方向 = 元件自身 strand -->
        <g v-for="(feat, i) in markerFeatures" :key="`${feat.name}-${feat.start}-${i}`">
          <path :d="arrowPath(toX(feat.start), toX(feat.end), MARK_Y, MARK_H, feat.strand === '-' ? -1 : 1)"
                fill="#38a169" opacity="0.85" />
          <text :x="(toX(feat.start) + toX(feat.end)) / 2" :y="MARK_Y + 24"
                class="rp-marker" text-anchor="middle">{{ shortName(feat.name) }}</text>
        </g>

        <!-- 窗口刻度 -->
        <g>
          <line :x1="PAD" :x2="VB_W - PAD" y1="142" y2="142" stroke="#a0aec0" stroke-width="1" />
          <text :x="PAD" y="138" class="rp-ruler" text-anchor="start">{{ rulerTicks[0] }}</text>
          <text :x="VB_W - PAD" y="138" class="rp-ruler" text-anchor="end">{{ rulerTicks[1] }}</text>
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

.rp-meta {
  margin: 0.15rem 0 0;
  font-size: 0.78rem;
  color: #4a5568;
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
