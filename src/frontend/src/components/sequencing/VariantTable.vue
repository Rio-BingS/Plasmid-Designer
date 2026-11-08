<script setup lang="ts">
/**
 * 突变表（差异明细）
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：逐变异一行的完整证据表
 * （类型/变化/所在特征/氨基酸/移码/酶切位点/支持 reads/置信度/评分/行动），
 * 点击行上报给父组件跳峰图。
 *
 * T15（goal A 5.3）：按待处理优先级排序（建议重测 > 核对峰图 > 采信，
 * 同档内评分升序——最可疑的排最前），显示 p_real 与 action；
 * 只折叠 exclude（互检定案项，此时折叠才合理）——低置信未决项是待办
 * 工作，不再折叠、逐行可见。互检排除的差异不进表体（2026-11-05 用户
 * 定稿）：按「源 read × 信号衰减区段」聚合为 read 级注记行。
 *
 * T22（goal A 5.4）：unresolved_sites 与 MixedVariant 也进列表可跳转
 * （rowKind='site'/'mixed' 行，证据包面板同位出图）。
 */
import { computed } from 'vue'
import type { SequencingVariant } from '@/api'

export interface SequencingUnresolvedSite {
  ref_pos?: number
  kind?: string
  detail?: string
  reads?: string[]
}

/** 表行：常规变体 / 未决位点 / 混合位点（T22 起三类同列表） */
export interface VariantTableRow {
  rowKind: 'variant' | 'site' | 'mixed'
  ref_pos: number
  variant?: SequencingVariant
  kind?: string
  detail?: string
  called?: string
  secondary?: string
  af?: number
  reads?: string[]
  p_real?: number
}

/** 单点双峰次等位（goal T7 MixedVariant；参考链口径） */
export interface SequencingMixedVariant {
  ref_pos: number
  ref_base?: string
  called_base?: string
  secondary_base?: string
  af?: number
  reads?: string[]
}

const props = defineProps<{
  variants: SequencingVariant[]
  unresolvedSites?: SequencingUnresolvedSite[]
  mixedVariants?: SequencingMixedVariant[]
}>()

const emit = defineEmits<{
  (e: 'jump', v: SequencingVariant | VariantTableRow): void
}>()

/** 互检排除的差异（不进表体，聚合为 read 级注记；T15 起唯一被折叠的档） */
const resolvedVariants = computed(() =>
  props.variants.filter((v) => v.cross_read_resolved || v.status === 'rejected'))
/** 待处理优先级：建议重测 > 核对峰图 > 采信；旧记录无 action 时按置信度
 * 低>中>高折算；同档内评分升序（最可疑在前），无 p_real 按位置 */
const ACTION_RANK: Record<string, number> = { resequence: 0, review_trace: 1, accept: 2 }
function pendingKey(v: SequencingVariant): (number | number)[] {
  const rank = v.action ? ACTION_RANK[v.action] ?? 1
    : v.confidence === 'low' ? 0.5 : v.confidence === 'medium' ? 1 : 2
  return [rank, v.p_real ?? 0, v.ref_pos]
}
const shownVariants = computed(() =>
  props.variants.filter((v) => !resolvedVariants.value.includes(v))
    .sort((a, b) => {
      const ka = pendingKey(a)
      const kb = pendingKey(b)
      return (ka[0] - kb[0]) || (ka[1] - kb[1]) || (ka[2] - kb[2])
    }))

/** T22：未决位点/混合位点转表行（排在所有变体之前——未经判定的工作） */
const extraRows = computed<VariantTableRow[]>(() => [
  ...(props.unresolvedSites ?? [])
    .filter((s) => s.ref_pos)
    .map((s) => ({ rowKind: 'site' as const, ref_pos: s.ref_pos!,
                   kind: s.kind || 'unresolved_site', detail: s.detail,
                   reads: s.reads })),
  ...(props.mixedVariants ?? []).map((m) => ({
    rowKind: 'mixed' as const, ref_pos: m.ref_pos,
    called: m.called_base, secondary: m.secondary_base, af: m.af,
    reads: m.reads,
  })),
])
const SITE_KIND_LABELS: Record<string, string> = {
  n_call: 'N/模糊调用', iupac: '模糊调用', poly_undetermined: 'poly 不可判定',
  dropout: '信号骤降', overlap_discordance: '重叠区分歧',
  orf_annotation_suspect: 'ORF 标注疑点',
}
const allRows = computed<VariantTableRow[]>(() => [
  ...extraRows.value,
  ...shownVariants.value.map((v) => ({ rowKind: 'variant' as const,
                                      ref_pos: v.ref_pos, variant: v })),
])

/** 行动徽标文案（goal T15 四档） */
const ACTION_LABELS: Record<string, { label: string; cls: string }> = {
  accept: { label: '采信', cls: 'act-accept' },
  exclude: { label: '排除', cls: 'act-exclude' },
  review_trace: { label: '核对峰图', cls: 'act-review' },
  resequence: { label: '建议重测', cls: 'act-reseq' },
}
function actionText(v: SequencingVariant): string {
  const a = v.action ?? (v.confidence === 'low' ? 'review_trace' : 'accept')
  return ACTION_LABELS[a]?.label ?? a
}
/** 2026-11-08 复审 R5：p_real 是 T15 规则评分（锚点+层内修正，与旧
 * if 树逐位等价），不是校准概率——展示为 0-100 评分，不再伪作百分比 */
function pRealText(v: SequencingVariant): string {
  return v.p_real != null ? `评分 ${Math.round(v.p_real * 100)}` : ''
}

/** 互检排除项按「源 read × 区段」聚合（间隔 ≤50bp 视为同一衰减区段，与后端结论同口径） */
const resolvedNotes = computed(() => {
  const byRead = new Map<string, SequencingVariant[]>()
  for (const v of resolvedVariants.value) {
    const key = v.read || '?'
    if (!byRead.has(key)) byRead.set(key, [])
    byRead.get(key)!.push(v)
  }
  return Array.from(byRead.entries()).map(([read, vs]) => {
    const pos = [...new Set(vs.map((v) => v.ref_pos))].sort((a, b) => a - b)
    const ranges: string[] = []
    let rs = pos[0], re = pos[0]
    for (const p of pos.slice(1)) {
      if (p - re <= 50) re = p
      else { ranges.push(rs === re ? `${rs}` : `${rs}-${re}`); rs = p; re = p }
    }
    ranges.push(rs === re ? `${rs}` : `${rs}-${re}`)
    const verifiers = [...new Set(vs.flatMap((v) => v.cross_read_conflict?.reads || []))]
    return { read, count: vs.length, ranges: ranges.join('、'),
             verifiers: verifiers.join('、') || '其他引物' }
  })
})

/** 置信度徽章的悬停说明：附峰级证据（突变峰占比 / 信噪比 / 插入峰强度比） */
function confTitle(v: SequencingVariant): string {
  const ev = v.peak_evidence
  let peak = ''
  if (ev && ev.mutant_pct != null) {
    peak = `峰级证据：突变峰占比 ${ev.mutant_pct}%，信噪比 ${ev.snr ?? '—'}`
  } else if (ev && ev.insertion_peak_ratio != null) {
    peak = `峰级证据：插入峰强度为邻峰的 ${Math.round(ev.insertion_peak_ratio * 100)}%（≥60% 说明插入峰真实存在，Q 值在峰压缩区偏低属正常）`
  }
  if (v.corroborated_by_basecall) {
    peak += (peak ? '；' : '') + 'tracy 重 basecall 也报出此变异（同一测序信号的两个读出，仅供参考）'
  }
  if (v.confidence === 'low' && v.low_reason) return `低置信：${v.low_reason}${peak ? '；' + peak : ''}，务必人工核对峰图`
  if (v.confidence === 'low') return `低置信：疑似混合峰或 Q 值偏低${peak ? '；' + peak : ''}，务必人工核对峰图`
  if (v.confidence === 'medium') return `中置信：建议核对峰图${peak ? '；' + peak : ''}`
  return `高置信${peak ? '：' + peak : ''}`
}

/** 机器可读原因码 → 徽标（goal T1/T3；S17）：证据在可信区外等关键码明示，
 *  其余码进悬停 title，不在表内铺满 */
const REASON_LABELS: Record<string, { label: string; tip: string; warn?: boolean }> = {
  all_evidence_out_of_zone: {
    label: '区外证据', warn: true,
    tip: '该差异的支持证据全部来自 read 可信区之外（首端硬区/灰区或尾端）——判定成立但请核对峰图确认',
  },
  cross_check_clean: { label: '互检排除', tip: '其他引物在可靠区干净读出设计碱基，判为测序误差' },
  n_call: { label: 'N/模糊', tip: '碱基调用为 N/模糊码，未解析' },
  poly_compressed: { label: '峰压缩', tip: '落在同聚物峰合并压缩区，该 read 此处无可分辨峰' },
  mixed_bg_high: { label: '双峰背景', tip: '位点有双峰背景，峰级证据可能被干扰' },
}

function reasonBadges(v: SequencingVariant) {
  const codes = v.reason_codes ?? []
  const out = codes
    .filter((c) => REASON_LABELS[c])
    .map((c) => ({ ...REASON_LABELS[c], warn: !!REASON_LABELS[c].warn }))
  const extra = codes.filter((c) => !REASON_LABELS[c])
  if (extra.length) {
    out.push({ label: `${extra.length} 项`, tip: `其他原因码：${extra.join('、')}`, warn: false })
  }
  return out
}
</script>

<template>
  <div v-if="variants.length || extraRows.length">
    <h4 class="section-title">差异明细（点击行查看峰图；按待处理优先级排序）</h4>
    <!-- 互检排除注记：判读已完成，不占差异表行 -->
    <div v-if="resolvedVariants.length" class="resolved-notes">
      <div v-for="n in resolvedNotes" :key="n.read" class="resolved-row"
           :title="`逐条位置：${resolvedVariants.filter((v) => v.read === n.read).map((v) => v.ref_pos).join('、')}（点击行可跳峰图核对）`">
        ✓ {{ n.read }} 报出 {{ n.count }} 处差异（参考 {{ n.ranges }}）：均已被 {{ n.verifiers }} 干净覆盖排除，判为该 read 测序误差，无需人工核对
      </div>
    </div>
    <table class="seq-table clickable" v-if="allRows.length">
      <thead>
        <tr><th>位置</th><th>类型</th><th>变化</th><th>所在特征</th><th>氨基酸</th><th>移码</th><th>酶切位点</th><th>支持reads</th><th>Q</th><th>置信度</th><th>评分 / 行动</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in allRows" :key="`${row.rowKind}-${row.ref_pos}-${row.kind ?? row.variant?.alt_base ?? ''}`" @click="emit('jump', row)">
          <td>{{ row.ref_pos }}</td>
          <td>
            <template v-if="row.rowKind === 'variant'">{{ row.variant!.type === 'substitution' ? '替换' : row.variant!.type === 'insertion' ? '插入' : '缺失' }}</template>
            <template v-else-if="row.rowKind === 'site'"><span class="badge reason warn">{{ SITE_KIND_LABELS[row.kind!] ?? '未决位点' }}</span></template>
            <template v-else><span class="badge reason">混合位点</span></template>
          </td>
          <td class="mono">
            <template v-if="row.rowKind === 'variant'">
              <!-- MNV（goal T25.1）：同密码子多替换联合注释，成员位合并呈现 -->
              <template v-if="row.variant!.mnv">{{ row.variant!.mnv.nt_change }}
                <span class="badge reason" :title="'同密码子 ' + row.variant!.mnv.members.length + ' 处替换联合注释（密码子 ' + row.variant!.mnv.codon + '）'">MNV @{{ row.variant!.mnv.members.join('+') }}</span>
              </template>
              <template v-else>{{ row.variant!.ref_base }} → {{ row.variant!.alt_base }}</template>
            </template>
            <template v-else-if="row.rowKind === 'mixed'">{{ row.called || '?' }} / {{ row.secondary || '?' }}（AF {{ row.af ?? '-' }}）</template>
            <template v-else>-</template>
          </td>
          <td>
            <template v-if="row.rowKind === 'variant'">{{ (row.variant!.features || []).map((f) => f.name).join(', ') || '非编码区' }}</template>
            <template v-else>{{ row.detail || '-' }}</template>
          </td>
          <td>{{ row.variant?.aa_change || (row.rowKind === 'variant' && row.variant!.type === 'substitution' ? '同义' : '-') }}</td>
          <td><span v-if="row.variant?.frameshift" class="badge bad">移码</span><span v-else>-</span></td>
          <td>
            <template v-if="row.rowKind === 'variant'">
              <span v-if="row.variant!.enzyme_sites_lost?.length" class="badge bad">破坏: {{ row.variant!.enzyme_sites_lost.join(', ') }}</span>
              <span v-if="row.variant!.enzyme_sites_gained?.length" class="badge">新增: {{ row.variant!.enzyme_sites_gained.join(', ') }}</span>
              <!-- 酶位 ±10bp 内有未决 indel（goal T25.4）：结论带提示，表内挂徽标 -->
              <span v-if="row.variant!.enzyme_sites_pending?.length" class="badge reason warn"
                    title="该 indel 归属未定，±10bp 内酶切位点的有无同样待定">待定: {{ row.variant!.enzyme_sites_pending.join(', ') }}</span>
              <span v-if="!row.variant!.enzyme_sites_lost?.length && !row.variant!.enzyme_sites_gained?.length && !row.variant!.enzyme_sites_pending?.length">-</span>
            </template>
            <template v-else>-</template>
          </td>
          <td>{{ row.rowKind === 'variant' ? (row.variant!.support_reads || 1) : (row.reads?.length || '-') }}</td>
          <td>{{ row.variant?.read_q ?? '-' }}</td>
          <td>
            <template v-if="row.rowKind === 'variant'">
              <span v-if="row.variant!.confidence" class="conf-chip" :class="'conf-' + row.variant!.confidence"
                    :title="confTitle(row.variant!)">
                {{ row.variant!.confidence === 'high' ? '高' : row.variant!.confidence === 'medium' ? '中' : '低' }}
              </span>
              <span v-else>-</span>
              <!-- 机器可读原因码明示（goal T1/T3）：out-of-zone 证据、互检定案等 -->
              <span v-for="c in reasonBadges(row.variant!)" :key="c.label" class="badge reason" :class="{ warn: c.warn }"
                    :title="c.tip">{{ c.label }}</span>
            </template>
            <template v-else>-</template>
          </td>
          <td>
            <template v-if="row.rowKind === 'variant'">
              <span v-if="row.variant!.p_real != null" class="p-real"
                    :title="'T15 规则评分（0-100，非校准概率），详见 SANGER_CALIBRATION §2/§3'">{{ pRealText(row.variant!) }}</span>
              <span class="badge action" :class="row.variant!.action ? ACTION_LABELS[row.variant!.action]?.cls : 'act-review'">{{ actionText(row.variant!) }}</span>
            </template>
            <template v-else>
              <span class="badge action act-review">证据包</span>
            </template>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
/* 突变表样式：随模板从 SequencingPanel.vue 迁入 */
.seq-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.seq-table th, .seq-table td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border-color, #eee); text-align: left; }
.seq-table th { background: var(--bg-secondary, #f7f7f7); }
.seq-table.clickable tr { cursor: pointer; }
.seq-table.clickable tr:hover { background: var(--bg-secondary, #f7f7f7); }
.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.p-real { font-family: Consolas, monospace; font-size: 0.78rem; margin-right: 4px; }
.badge.action { margin-left: 0; }
.badge.act-accept { background: #E5F5E9; color: #227A36; }
.badge.act-exclude { background: #EDF2EE; color: #5E7A64; }
.badge.act-review { background: #FCF3DC; color: #9A6D00; }
.badge.act-reseq { background: #FBEAE8; color: #A03227; }
.resolved-notes { margin: 0 0 0.5rem; }
.resolved-row {
  font-size: 0.82rem; color: #227A36; background: #F0FAF2;
  border: 1px solid #BFE5C8; border-radius: 6px; padding: 0.3rem 0.6rem; margin-bottom: 0.25rem;
}
.badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 0.72rem; background: #EEE; }
.badge.reason { margin-left: 4px; background: #EDF2EE; color: #5E7A64; }
.badge.reason.warn { background: #FCF3DC; color: #9A6D00; }
.badge.bad { background: #FDE8E8; color: #C0392B; }
.conf-chip { display: inline-block; border-radius: 10px; padding: 1px 9px; font-size: 0.78rem; }
.conf-high { background: #E5F5E9; color: #227A36; }
.conf-medium { background: #FCF3DC; color: #9A6D00; }
.conf-low { background: #FBEAE8; color: #A03227; }
.mono { font-family: Consolas, monospace; }
</style>
