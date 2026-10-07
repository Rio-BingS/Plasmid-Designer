<script setup lang="ts">
/**
 * 结论总览卡 + 编码区（CDS）结论卡
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：
 * - 结论文本按组折叠（低置信行 / 逐 read 双峰位点范围行，各带展开钮）；
 * - 覆盖条带图与缺口摘要；
 * - CDS 逐条结论（覆盖状态/SO 后果标签/移码·无义·蛋白长度明细）。
 *
 * showLowConf 用 defineModel 与父组件共享：同一状态也控制突变表的
 * 低置信行折叠（总览里展开 → 突变表同步展开，行为与拆分前一致）。
 */
import { computed, ref } from 'vue'
import type { SequencingAnalysis } from '@/api'

const props = defineProps<{
  analysis: SequencingAnalysis
}>()

// 低置信折叠态与父组件（突变表）共享：拆分前是同一个 ref
const showLowConf = defineModel<boolean>('showLowConf', { default: false })
const showMixedDetail = ref(false)

// ==================== 覆盖条带 ====================
const coverageSegments = computed(() => {
  const a = props.analysis
  const L = a.reference_length
  return a.coverage_ranges.map(([s, e]) => ({ left: ((s - 1) / L) * 100, width: ((e - s + 1) / L) * 100 }))
})

/** 覆盖缺口摘要（取最长 3 段展示） */
const gapSummary = computed(() => {
  const gs = props.analysis.coverage_gaps ?? []
  const parts = gs.slice(0, 3).map((g) => `${g.start}-${g.end}（${g.length}bp）`)
  return parts.join('、') + (gs.length > 3 ? ' 等' : '')
})

// 结论文本按组折叠：低置信行（后端标注“低置信（/低置信度（”）连同紧随的
// “↳ 破坏/新增酶切位点”子注释行一起收起；poly 判读等独立行不受牵连。
// 逐 read 双峰位点范围行（“↳ …双峰 N 处，位于 read …”）单独一组默认收起，
// 展开后可核对位点是否落在 read 首尾不可信区
const conclusionParts = computed(() => {
  const lines = (props.analysis.conclusion ?? '').split('\n')
  const isLowHead = (l: string) => l.includes('低置信（') || l.includes('低置信度（')
  const isSubNote = (l: string) => l.trimStart().startsWith('↳') && l.includes('酶切位点')
  const isMixedDetail = (l: string) => l.trimStart().startsWith('↳') && l.includes('双峰')
  const main: string[] = []
  const low: string[] = []
  const mixed: string[] = []
  let prevFolded = false
  for (const l of lines) {
    if (isMixedDetail(l)) {
      mixed.push(l)
      prevFolded = false
      continue
    }
    const folded: boolean = isLowHead(l) || (isSubNote(l) && prevFolded)
    ;(folded ? low : main).push(l)
    prevFolded = folded
  }
  return { main: main.join('\n'), low, mixed }
})

/** CDS 结论卡的覆盖标签 */
function cdsCoverageLabel(c: { coverage_status: string; covered_percent: number }): string {
  if (c.coverage_status === 'uncovered') return '未覆盖'
  if (c.coverage_status === 'full') return '完整覆盖'
  return `覆盖 ${c.covered_percent}%`
}

// 未覆盖的 CDS 不进卡片：部分测序是常规策略，未测区域按设计序列对待，
// 列一堆“未覆盖”条目只会稀释真正有判读结果的编码区
const judgedCdsReports = computed(() =>
  (props.analysis.cds_reports ?? []).filter((c) => c.coverage_status !== 'uncovered'))

// SO 标准后果词表 → 中文标签与影响等级（VEP/snpEff 同款分级）
const SO_LABELS: Record<string, string> = {
  stop_gained: '无义突变',
  stop_lost: '终止丢失',
  start_lost: '起始丢失',
  frameshift_variant: '移码',
  inframe_insertion: '框内插入',
  inframe_deletion: '框内缺失',
  missense_variant: '错义',
  synonymous_variant: '同义',
}
const SO_HIGH = new Set(['stop_gained', 'stop_lost', 'start_lost', 'frameshift_variant'])
const SO_MID = new Set(['inframe_insertion', 'inframe_deletion', 'missense_variant'])
function soLevel(t: string): string {
  if (SO_HIGH.has(t)) return 'high'
  if (SO_MID.has(t)) return 'mid'
  return 'low'
}
</script>

<template>
  <!-- 结论总览 -->
  <div class="conclusion-card" :class="{ ok: analysis.variants.length === 0 }">
    <p class="conclusion-text">{{ conclusionParts.main }}</p>
    <button v-if="conclusionParts.low.length" class="lowconf-toggle" @click="showLowConf = !showLowConf">
      {{ showLowConf ? '▾ 收起低置信' : `▸ ${conclusionParts.low.filter((l) => !l.trimStart().startsWith('↳')).length} 处低置信已折叠（疑似测序噪声/混合峰，展开逐条核对）` }}
    </button>
    <p v-if="showLowConf && conclusionParts.low.length" class="conclusion-text lowconf-lines">{{ conclusionParts.low.join('\n') }}</p>
    <button v-if="conclusionParts.mixed.length" class="lowconf-toggle" @click="showMixedDetail = !showMixedDetail">
      {{ showMixedDetail ? '▾ 收起双峰位点范围' : `▸ ${conclusionParts.mixed.length} 条 read 的双峰位点范围已折叠（read 坐标，展开核对是否落在首尾）` }}
    </button>
    <p v-if="showMixedDetail && conclusionParts.mixed.length" class="conclusion-text lowconf-lines">{{ conclusionParts.mixed.join('\n') }}</p>
    <div class="conclusion-meta">
      <span>引擎: {{ analysis.engine }}</span>
      <span>共识覆盖率: {{ analysis.consensus.coverage_percent }}%</span>
      <span>差异: {{ analysis.variants.length }} 处</span>
    </div>
    <!-- 覆盖条带图 -->
    <div class="coverage-bar">
      <div
        v-for="(seg, i) in coverageSegments"
        :key="i"
        class="coverage-seg"
        :style="{ left: seg.left + '%', width: seg.width + '%' }"
      ></div>
    </div>
    <div class="coverage-labels"><span>1</span><span>{{ analysis.reference_length }} bp</span></div>
    <p class="coverage-gaps" v-if="analysis.coverage_gaps?.length">
      覆盖缺口 {{ analysis.coverage_gaps.length }} 段（按长度排序）：{{ gapSummary }}（未测区域按设计序列对待）
    </p>
  </div>

  <!-- CDS 编码区测序结论：整段编码序列是否与参考一致（未覆盖的不显示） -->
  <div class="conclusion-card cds-card" v-if="judgedCdsReports.length">
    <h4 class="section-title">编码区（CDS）测序结论</h4>
    <div v-for="c in judgedCdsReports" :key="c.name + c.start" class="cds-row">
      <span class="cds-dot" :class="c.protein_identical === null ? 'na' : (c.protein_identical ? 'pass' : 'fail')"></span>
      <div class="cds-main">
        <p class="cds-name">
          {{ c.name }}
          <span class="cds-coord">{{ c.start }}-{{ c.end }}（{{ c.strand === '-' ? '反向' : '正向' }}）</span>
          <span class="cds-cov" :class="c.coverage_status">{{ cdsCoverageLabel(c) }}</span>
          <span v-for="t in c.consequences ?? []" :key="t" class="cds-so" :class="soLevel(t)">{{ SO_LABELS[t] ?? t }}</span>
          <span v-if="c.pending_low_confidence" class="cds-so mid"
                title="低置信变异（疑似测序噪声）未计入本结论，见结论末尾待复核说明">待复核 {{ c.pending_low_confidence }} 处</span>
        </p>
        <p class="cds-verdict">{{ c.verdict }}</p>
        <p class="cds-detail" v-if="c.protein_identical === false">
          <span v-if="c.premature_stop_aa">无义突变：翻译提前终止于第 {{ c.premature_stop_aa }} aa</span>
          <span v-if="c.frameshift_count">移码 {{ c.frameshift_count }} 处</span>
          <span v-if="c.ref_protein_length != null && c.alt_protein_length != null && c.ref_protein_length !== c.alt_protein_length">蛋白长度 {{ c.ref_protein_length }} → {{ c.alt_protein_length }} aa</span>
          <span v-if="c.aa_changes?.length">氨基酸替换（{{ c.aa_changes.slice(0, 5).join('、') }}{{ c.aa_changes.length > 5 ? '…' : '' }}）</span>
        </p>
      </div>
    </div>
  </div>
</template>
