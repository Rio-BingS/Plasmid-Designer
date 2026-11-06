<script setup lang="ts">
/**
 * 突变表（差异明细）
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：逐变异一行的完整证据表
 * （类型/变化/所在特征/氨基酸/移码/酶切位点/支持 reads/置信度），
 * 点击行上报给父组件跳峰图。低置信行折叠态 showLowConf 用 defineModel
 * 与父组件共享——结论总览里展开 → 本表同步展开（拆分前是同一个 ref）。
 *
 * 互检排除的差异不进表体（2026-11-05 用户定稿）：判读已由其他引物完成，
 * 逐条列出是把已裁决项推回人工。按「源 read × 信号衰减区段」聚合为
 * read 级注记行（同后端结论口径），展开可看逐条位置（跳峰图核对用）。
 */
import { computed } from 'vue'
import type { SequencingVariant } from '@/api'

const props = defineProps<{
  variants: SequencingVariant[]
}>()

const showLowConf = defineModel<boolean>('showLowConf', { default: false })

const emit = defineEmits<{
  (e: 'jump', v: SequencingVariant): void
}>()

/** 互检排除的差异（不进表体，聚合为 read 级注记） */
const resolvedVariants = computed(() =>
  props.variants.filter((v) => v.cross_read_resolved))
/** 真·低置信（未被互检排除，仍需人工核对） */
const lowConfVariants = computed(() =>
  props.variants.filter((v) => v.confidence === 'low' && !v.cross_read_resolved))
const shownVariants = computed(() =>
  props.variants.filter((v) => !v.cross_read_resolved
    && (showLowConf.value || v.confidence !== 'low')))

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
  <div v-if="variants.length">
    <h4 class="section-title">差异明细（点击行查看峰图）
      <button v-if="lowConfVariants.length" class="lowconf-toggle" @click="showLowConf = !showLowConf">
        {{ showLowConf ? '▾ 收起低置信' : `▸ ${lowConfVariants.length} 处低置信已折叠（展开逐条查看）` }}
      </button>
    </h4>
    <!-- 互检排除注记：判读已完成，不占差异表行 -->
    <div v-if="resolvedVariants.length" class="resolved-notes">
      <div v-for="n in resolvedNotes" :key="n.read" class="resolved-row"
           :title="`逐条位置：${resolvedVariants.filter((v) => v.read === n.read).map((v) => v.ref_pos).join('、')}（点击行可跳峰图核对）`">
        ✓ {{ n.read }} 报出 {{ n.count }} 处差异（参考 {{ n.ranges }}）：均已被 {{ n.verifiers }} 干净覆盖排除，判为该 read 测序误差，无需人工核对
      </div>
    </div>
    <table class="seq-table clickable" v-if="shownVariants.length">
      <thead>
        <tr><th>位置</th><th>类型</th><th>变化</th><th>所在特征</th><th>氨基酸</th><th>移码</th><th>酶切位点</th><th>支持reads</th><th>Q</th><th>置信度</th></tr>
      </thead>
      <tbody>
        <tr v-for="v in shownVariants" :key="`${v.ref_pos}-${v.type}-${v.alt_base ?? ''}-${v.read ?? ''}`" @click="emit('jump', v)">
          <td>{{ v.ref_pos }}</td>
          <td>{{ v.type === 'substitution' ? '替换' : v.type === 'insertion' ? '插入' : '缺失' }}</td>
          <td class="mono">{{ v.ref_base }} → {{ v.alt_base }}</td>
          <td>{{ (v.features || []).map((f) => f.name).join(', ') || '非编码区' }}</td>
          <td>{{ v.aa_change || (v.type === 'substitution' ? '同义' : '-') }}</td>
          <td><span v-if="v.frameshift" class="badge bad">移码</span><span v-else>-</span></td>
          <td>
            <span v-if="v.enzyme_sites_lost?.length" class="badge bad">破坏: {{ v.enzyme_sites_lost.join(', ') }}</span>
            <span v-if="v.enzyme_sites_gained?.length" class="badge">新增: {{ v.enzyme_sites_gained.join(', ') }}</span>
            <span v-if="!v.enzyme_sites_lost?.length && !v.enzyme_sites_gained?.length">-</span>
          </td>
          <td>{{ v.support_reads || 1 }}</td>
          <td>{{ v.read_q ?? '-' }}</td>
          <td>
            <span v-if="v.confidence" class="conf-chip" :class="'conf-' + v.confidence"
                  :title="confTitle(v)">
              {{ v.confidence === 'high' ? '高' : v.confidence === 'medium' ? '中' : '低' }}
            </span>
            <span v-else>-</span>
            <!-- 机器可读原因码明示（goal T1/T3）：out-of-zone 证据、互检定案等 -->
            <span v-for="c in reasonBadges(v)" :key="c.label" class="badge reason" :class="{ warn: c.warn }"
                  :title="c.tip">{{ c.label }}</span>
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
.lowconf-toggle {
  display: inline-block; margin: 0.1rem 0 0.4rem; padding: 0.15rem 0.6rem;
  font-size: 0.78rem; color: #8a6a1f; background: #FBF3DF;
  border: 1px solid #E8D9A8; border-radius: 12px; cursor: pointer;
}
.lowconf-toggle:hover { background: #F5E9C8; }
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
