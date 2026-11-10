<script setup lang="ts">
/**
 * 结论总览卡 + 编码区（CDS）结论卡
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：
 * - 结论文本按组折叠（低置信行 / 逐 read 双峰位点范围行，各带展开钮）；
 * - 覆盖条带图与缺口摘要；
 * - CDS 逐条结论（覆盖状态/SO 后果标签/移码·无义·蛋白长度明细）。
 *
 * showLowConf / showMixedDetail 用 defineModel 与父组件共享：showLowConf
 * 同时控制突变表的低置信行折叠（总览里展开 → 突变表同步展开）；退出历史
 * 回看时父组件 preset watch 会把两者一起复位（行为与拆分前一致）。
 */
import { computed, ref } from 'vue'
import type { CdsReport, SequencingAnalysis, VerdictItem } from '@/api'

const props = defineProps<{
  analysis: SequencingAnalysis
}>()

// 低置信/双峰折叠态与父组件共享：拆分前是同一个 ref
const showLowConf = defineModel<boolean>('showLowConf', { default: false })
const showMixedDetail = defineModel<boolean>('showMixedDetail', { default: false })

// ==================== 覆盖条带 ====================
const coverageSegments = computed(() => {
  const a = props.analysis
  const L = a.reference_length
  return a.coverage_ranges.map(([s, e]) => ({ left: ((s - 1) / L) * 100, width: ((e - s + 1) / L) * 100 }))
})

/** CDS 坐标：多段（join/跨原点）逐段列出，单段写起止 */
const cdsSpan = (c: CdsReport) =>
  c.segments?.length ? c.segments.map(([s, e]) => `${s}-${e}`).join('、') : `${c.start}-${c.end}`

/** 覆盖缺口摘要（取最长 3 段展示） */
const gapSummary = computed(() => {
  const gs = props.analysis.coverage_gaps ?? []
  // 跨原点缺口按两段写位置（与 read 落点同口径「x-L、1-y」）
  const span = (g: (typeof gs)[number]) =>
    g.ref_segments?.length ? g.ref_segments.map(([s, e]) => `${s}-${e}`).join('、') : `${g.start}-${g.end}`
  const parts = gs.slice(0, 3).map((g) => `${span(g)}（${g.length}bp）`)
  return parts.join('、') + (gs.length > 3 ? ' 等' : '')
})

// 结论文本按组折叠（2026-11-08 复审 R6；W12 按 RV 遗留清除字符串分组）：
// 只消费后端下发的 conclusion_groups 结构化分组（构建端显式标记，判定
// 不依赖文案）；旧记录无该键 → 不折叠、正文整段常驻
const conclusionParts = computed(() => {
  const allLines = (props.analysis.conclusion ?? '').split('\n')
  // 头行由 headLine 单独渲染，正文不再包含（2026-11-05 头行去重）
  const lines = allLines.slice(1)
  const g = props.analysis.conclusion_groups
  // W12（RV 遗留清除）：只消费后端下发的结构化分组，不再按「低置信（」
  // 等文案猜测分组——R6 之后构建端始终下发该键，旧记录无键时正文整段
  // 常驻（不折叠），分组判定彻底不依赖文案
  if (!g) {
    return { main: lines.filter((l) => l.trim() !== '').join('\n'), low: [], mixed: [], resolved: [] }
  }
  const grouped = new Set([...(g.low ?? []), ...(g.mixed_detail ?? []), ...(g.resolved ?? [])])
  const main = lines.filter((l) => l.trim() !== '' && !grouped.has(l)).join('\n')
  return { main, low: g.low ?? [], mixed: g.mixed_detail ?? [], resolved: g.resolved ?? [] }
})

/** 结论头判读：优先 verdict.status（goal T2；A 5.2 不做文案匹配），无
 * verdict（旧记录）退回 ✓/✗ 符号推断 */
const headLine = computed(() => {
  const first = (props.analysis.conclusion ?? '').split('\n')[0] ?? ''
  const bySymbol = first.startsWith('✓') ? 'ok' : first.startsWith('✗') ? 'fail' : 'warn'
  const st = props.analysis.verdict?.status
  const kind = st === 'pass' ? 'ok' : st === 'fail' ? 'fail' : st ? 'warn' : bySymbol
  return { text: first, kind }
})

// ==================== 元件状态表（goal v3 W13，PL §E5） ====================
// verdict.element_verdicts（W5 下发）：范围内元件逐行列出；范围外（
// not_assessed）折叠成一行——结论只对声明范围负责，范围外如实说未评估
const showOutOfScope = ref(false)
const elementRows = computed(() => props.analysis.verdict?.element_verdicts ?? [])
const inScopeElements = computed(() => elementRows.value.filter((e) => e.in_scope))
const outScopeCount = computed(() =>
  elementRows.value.filter((e) => !e.in_scope).length)

const ELEMENT_STATUS_LABEL: Record<string, string> = {
  ok: '一致', issue: '有问题', not_assessed: '未评估',
}

/** verdict 徽标文案（T2：状态机可读化；旧记录无 verdict 不显示） */
const statusBadge = computed(() => {
  const labels: Record<string, string> = {
    pass: '通过', fail: '失败', pending: '待定', mixed: '疑似混合',
    partial: '部分验证', insufficient: '证据不足', failed_reads: '读取失败',
  }
  const st = props.analysis.verdict?.status
  return st ? { status: st, label: labels[st] ?? st } : null
})

/** 阻断项按 verdict.blocking 结构化列出（T2；每项给位置/元件与建议动作）。
 * 2026-11-10（cf77c3a 复审 P0-1）：结论卡只列前 CARD_BLOCKING_MAX 项（确证
 * 差异优先），其余交给复核队列——真实子集曾在卡片上铺开 379 行黄条 */
const CARD_BLOCKING_MAX = 5
const allBlocking = computed(() => props.analysis.verdict?.blocking ?? [])
const blockingItems = computed(() =>
  [...allBlocking.value]
    .sort((a, b) => Number(!!b.fail_impact) - Number(!!a.fail_impact))
    .slice(0, CARD_BLOCKING_MAX))
const blockingOverflow = computed(() => allBlocking.value.length - blockingItems.value.length)
const queueCount = computed(() => props.analysis.review_queue?.length ?? 0)

const KIND_LABELS: Record<string, string> = {
  confirmed_variant: '确证差异',
  unresolved_variant: '未决差异',
  unresolved_site: '未决位点',
  n_in_cds: 'CDS 内 N/模糊调用',
  mixed_variant: '混合等位',
  mixed_sample: '疑似混合样品',
  unaligned_hq_segment: '端部高质量未比对',
  structural_breakpoint: '结构断点',
  critical_element_variant: '关键元件差异',
  junction_variant: '接合区差异',
  roi_uncovered: '目标未覆盖',
  roi_unverified: '目标未验证',
  poly_unreliable: 'poly 计数不可信',
  dropout: '信号骤降',
  partial_alignment: '部分比对',
  read_failed: 'read 解析失败',
  primer_mismatch: '引物不符',
  orf_annotation_suspect: 'ORF 校正存疑',
  overlap_discordance: '重叠区分歧',
  allele_mismatch: '等位基因不符',
}

const blockingPos = (b: VerdictItem) =>
  b.ref_start ? (b.ref_end && b.ref_end !== b.ref_start ? `${b.ref_start}–${b.ref_end}` : `${b.ref_start}`) : ''

/** 区间裁决行拆分：裁决词（高质量覆盖排除…无需核对）绿色强调，其余正常色 */
const resolvedParts = (l: string) => {
  const i = l.indexOf('高质量覆盖排除')
  return i >= 0 ? { head: l.slice(0, i), tail: l.slice(i) } : { head: l, tail: '' }
}

/** 结构化裁决行（T2）：resolved_regions 存在时优先结构化渲染，
 *  文本行匹配只作为旧记录的回退 */
const resolvedRegionList = computed(() => props.analysis.resolved_regions ?? [])

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
  <!-- 结论总览：卡片默认中性白底，✓ 只绿在头行文字；红底仅用于失败结论 -->
  <div class="conclusion-card" :class="{ fail: headLine.kind === 'fail' }">
    <p class="conclusion-head" :class="headLine.kind">
      {{ headLine.text }}
      <span v-if="statusBadge" class="verdict-badge" :class="statusBadge.status">{{ statusBadge.label }}</span>
    </p>
    <!-- 解析层告警（PLOC 截短 / 质量值估算 / ab1 结构损坏已自动修复，2026-11-11）：
         后端早已下发 warnings，前端此前从未渲染——文件修复等事实必须让用户看到 -->
    <ul v-if="analysis.warnings?.length" class="parse-warnings">
      <li v-for="(w, i) in analysis.warnings" :key="i" class="parse-warning">⚠ {{ w }}</li>
    </ul>
    <!-- 阻断项按 verdict.blocking 结构化列出（goal T2；M13）——文案改写不影响；
         旧记录无 verdict 时退回纯文本正文 -->
    <ul v-if="blockingItems.length" class="verdict-blocking">
      <li v-for="(b, i) in blockingItems" :key="i" class="verdict-blocking-item" :class="b.kind">
        <span class="vb-kind">{{ KIND_LABELS[b.kind] ?? b.kind }}</span>
        <span v-if="blockingPos(b)" class="vb-pos">{{ blockingPos(b) }}</span>
        <span v-if="b.element" class="vb-element">{{ b.element }}</span>
        <span class="vb-detail">{{ b.detail }}</span>
        <span class="vb-action">{{ b.action }}</span>
      </li>
      <li v-if="blockingOverflow > 0" class="verdict-blocking-more">
        另 {{ blockingOverflow }} 项未展开<template v-if="queueCount">，复核队列已合并为 {{ queueCount }} 项</template>
      </li>
    </ul>
    <!-- 区间裁决行：优先 resolved_regions 结构化渲染（T2），旧记录退回文本行匹配 -->
    <div v-if="resolvedRegionList.length || conclusionParts.resolved.length" class="resolved-regions">
      <div v-for="(r, i) in resolvedRegionList" :key="'sr' + i" class="resolved-region-line"
           :title="`源 read ${r.source_read}（${r.direction === '-' ? '反向' : '正向'}）报出 ${r.variant_count} 处差异，由 ${r.verifier_reads.join('、') || '其他引物'} 高质量覆盖排除${r.verifier_quality ? '（' + r.verifier_quality + '）' : ''}`">
        差异区间 {{ r.ref_range }}：{{ r.source_read }}（{{ r.direction === '-' ? '反向' : '正向' }}<template v-if="r.primer_range"> | 引物内坐标 {{ r.primer_range }}</template>）报出 {{ r.variant_count }} 处差异 →
        已被 {{ r.verifier_reads.join('、') || '其他引物' }} <span class="resolved-verdict">高质量覆盖排除{{ r.verifier_quality ? '，' + r.verifier_quality : '' }}，无需核对</span>
      </div>
      <template v-if="!resolvedRegionList.length">
        <div v-for="(l, i) in conclusionParts.resolved" :key="i" class="resolved-region-line"
             :title="`旧记录无结构化裁决行，位置与互检证据见变体清单（cross_read_resolved 标注）`">
          {{ resolvedParts(l).head }}<span class="resolved-verdict">{{ resolvedParts(l).tail }}</span>
        </div>
      </template>
    </div>
    <p class="conclusion-text">{{ conclusionParts.main }}</p>
    <button v-if="conclusionParts.low.length" class="lowconf-toggle" @click="showLowConf = !showLowConf">
      {{ showLowConf ? '▾ 收起未决差异' : `▸ ${conclusionParts.low.filter((l) => !l.trimStart().startsWith('↳')).length} 处未决差异已折叠（需人工核对峰图，展开逐条核对）` }}
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
          <span class="cds-coord">{{ cdsSpan(c) }}（{{ c.strand === '-' ? '反向' : '正向' }}）</span>
          <span class="cds-cov" :class="c.coverage_status">{{ cdsCoverageLabel(c) }}</span>
          <span v-for="t in c.consequences ?? []" :key="t" class="cds-so" :class="soLevel(t)">{{ SO_LABELS[t] ?? t }}</span>
          <span v-if="c.pending_low_confidence" class="cds-so mid"
                title="未决变异（伪影与低频真实变异均无法排除）未计入本结论，见结论末尾待复核说明">待复核 {{ c.pending_low_confidence }} 处</span>
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

  <!-- 元件状态表（goal v3 W13）：范围内元件逐行；范围外折叠成一行 -->
  <div class="conclusion-card elements-card" v-if="inScopeElements.length || outScopeCount">
    <h4 class="section-title">元件状态<span class="map-sub">（只评估需测范围内的元件；点击图谱元件可加入/移出范围）</span></h4>
    <div v-for="e in inScopeElements" :key="e.name + e.start" class="cds-row">
      <span class="cds-dot" :class="e.status === 'ok' ? 'pass' : 'fail'"></span>
      <div class="cds-main">
        <p class="cds-name">
          {{ e.name }}
          <span class="cds-coord">{{ e.start }}-{{ e.end }}（{{ e.type === 'junction' ? '接合区' : e.type }}）</span>
          <span class="cds-cov" :class="e.status === 'ok' ? 'full' : 'partial'">
            {{ ELEMENT_STATUS_LABEL[e.status] ?? e.status }} · 覆盖 {{ e.coverage ?? 0 }}%
          </span>
          <span v-if="e.issues" class="cds-so mid">{{ e.issues }} 处问题</span>
        </p>
      </div>
    </div>
    <button v-if="outScopeCount" class="lowconf-toggle" @click="showOutOfScope = !showOutOfScope">
      {{ showOutOfScope ? '▾ 收起范围外元件' : `▸ ${outScopeCount} 个范围外元件未评估（声明包含它们后重算即可评估）` }}
    </button>
    <div v-if="showOutOfScope && outScopeCount" class="cds-row">
      <span class="cds-dot na"></span>
      <div class="cds-main">
        <p class="cds-name">
          <span class="cds-cov">{{ elementRows.filter((e) => !e.in_scope).map((e) => e.name).join('、') }}</span>
          <span class="cds-cov">未评估</span>
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 结论卡/覆盖条带/CDS 卡样式：随模板从 SequencingPanel.vue 迁入
   （Vue scoped 样式不会穿透到子组件内部节点，父级规则管不到这里） */
.conclusion-card {
  background: #FFFFFF; border: 1px solid #E8E8E8; border-radius: 10px; padding: 1rem 1.25rem;
}
.conclusion-card.fail { background: #FDF3F3; border-color: #F2C6C6; }
.conclusion-text { font-weight: 600; white-space: pre-wrap; margin-bottom: 0.5rem; }
.conclusion-head { font-weight: 700; font-size: 1.02rem; white-space: pre-wrap; margin: 0 0 0.5rem; }
.conclusion-head.ok { color: #1F7A33; }
.conclusion-head.fail { color: #C0392B; }
.conclusion-head.warn { color: #9A6D00; }
.parse-warnings { list-style: none; margin: 0 0 0.5rem; padding: 0; }
.parse-warning {
  font-size: 0.84rem; color: #7A4F00; background: #FFF8E6;
  border: 1px solid #F0D9A0; border-left: 3px solid #D99A00;
  border-radius: 6px; padding: 0.35rem 0.7rem; margin-bottom: 0.3rem;
}
.resolved-regions { margin: 0.35rem 0 0.6rem; }
.resolved-region-line {
  font-size: 0.84rem; color: #555; background: #F7FBF8;
  border: 1px solid #D8EEDD; border-left: 3px solid #2E9E44;
  border-radius: 6px; padding: 0.35rem 0.7rem; margin-bottom: 0.3rem;
}
.resolved-verdict { color: #1F7A33; font-weight: 600; }
.lowconf-toggle {
  display: inline-block; margin: 0.1rem 0 0.4rem; padding: 0.15rem 0.6rem;
  font-size: 0.78rem; color: #8a6a1f; background: #FBF3DF;
  border: 1px solid #E8D9A8; border-radius: 12px; cursor: pointer;
}
.lowconf-toggle:hover { background: #F5E9C8; }
.lowconf-lines { font-weight: 400; font-size: 0.85rem; color: #8a6a1f; }
.cds-card { margin-top: 0.75rem; }
.cds-row { display: flex; gap: 0.6rem; padding: 0.5rem 0; border-top: 1px dashed #E8E8E8; }
.cds-dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 5px; flex: none; }
.cds-dot.pass { background: #2E9E44; }
.cds-dot.fail { background: #C0392B; }
.cds-dot.na { background: #BBB; }
.cds-dot.mid { background: #E6A700; }
.cds-name { font-weight: 600; margin: 0; }
.cds-coord { font-weight: 400; color: #888; font-size: 0.78rem; font-family: Consolas, monospace; }
.cds-cov { font-size: 0.72rem; font-weight: 400; padding: 1px 8px; border-radius: 10px; margin-left: 8px; vertical-align: 1px; }
.cds-cov.full { background: #E5F5E9; color: #227A36; }
.cds-cov.partial { background: #FCF3DC; color: #9A6D00; }
.cds-cov.uncovered { background: #EEE; color: #777; }
.cds-so { font-size: 0.7rem; padding: 1px 7px; border-radius: 10px; margin-left: 5px; vertical-align: 1px; }
.cds-so.high { background: #FBEAE8; color: #A03227; }
.cds-so.mid { background: #FDF2E3; color: #A8641A; }
.cds-so.low { background: #EDF2EE; color: #5E7A64; }
.cds-verdict { margin: 0.2rem 0 0; font-size: 0.86rem; }
.cds-detail { margin: 0.3rem 0 0; font-size: 0.78rem; color: #A03A2E; display: flex; flex-wrap: wrap; gap: 0.35rem 0.9rem; }
.poly-read-detail { color: #777; }
.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.conclusion-meta { display: flex; gap: 1.5rem; font-size: 0.8rem; color: #777; margin-bottom: 0.5rem; }
.coverage-bar {
  position: relative; height: 14px; background: #EEE; border-radius: 7px; overflow: hidden;
}
.coverage-seg { position: absolute; top: 0; bottom: 0; background: #2E9E44; }
.coverage-labels { display: flex; justify-content: space-between; font-size: 0.7rem; color: #999; margin-top: 2px; }
.coverage-gaps { margin: 0.4rem 0 0; font-size: 0.78rem; color: #9A6D00; }
/* verdict 徽标与阻断项清单（goal T2）：状态机可读化，阻断项逐条给位置/元件/动作 */
.verdict-badge { font-size: 0.72rem; font-weight: 400; padding: 1px 8px; border-radius: 10px; margin-left: 8px; vertical-align: 1px; background: #EEE; color: #777; }
.verdict-badge.pass { background: #E5F5E9; color: #227A36; }
.verdict-badge.fail { background: #FBEAE8; color: #A03227; }
.verdict-badge.pending, .verdict-badge.mixed, .verdict-badge.partial,
.verdict-badge.insufficient, .verdict-badge.failed_reads { background: #FCF3DC; color: #9A6D00; }
.verdict-blocking { list-style: none; margin: 0.4rem 0; padding: 0; font-size: 0.8rem; }
.verdict-blocking-item { padding: 3px 0; border-bottom: 1px dashed #EEE; display: flex; flex-wrap: wrap; gap: 0.3rem 0.6rem; align-items: baseline; }
.vb-kind { font-weight: 600; color: #555; }
.verdict-blocking-item .vb-kind { color: #9A6D00; }
.verdict-blocking-more { padding: 3px 0; color: #666; font-size: 0.78rem; }
.vb-pos { font-family: monospace; color: #555; }
.vb-element { color: #2277A0; }
.vb-detail { color: #444; }
.vb-action { color: #999; }
.vb-action::before { content: '→ '; color: #BBB; }
</style>
