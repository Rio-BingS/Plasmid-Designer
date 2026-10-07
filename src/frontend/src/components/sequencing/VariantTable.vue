<script setup lang="ts">
/**
 * 突变表（差异明细）
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：逐变异一行的完整证据表
 * （类型/变化/所在特征/氨基酸/移码/酶切位点/支持 reads/置信度），
 * 点击行上报给父组件跳峰图。低置信行折叠态 showLowConf 用 defineModel
 * 与父组件共享——结论总览里展开 → 本表同步展开（拆分前是同一个 ref）。
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

const lowConfVariants = computed(() =>
  props.variants.filter((v) => v.confidence === 'low'))
const shownVariants = computed(() =>
  showLowConf.value ? props.variants : props.variants.filter((v) => v.confidence !== 'low'))

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
  if (v.confidence === 'low') return `低置信：疑似混合峰或 Q 值偏低${peak ? '；' + peak : ''}，务必人工核对峰图`
  if (v.confidence === 'medium') return `中置信：建议核对峰图${peak ? '；' + peak : ''}`
  return `高置信${peak ? '：' + peak : ''}`
}
</script>

<template>
  <div v-if="variants.length">
    <h4 class="section-title">差异明细（点击行查看峰图）
      <button v-if="lowConfVariants.length" class="lowconf-toggle" @click="showLowConf = !showLowConf">
        {{ showLowConf ? '▾ 收起低置信' : `▸ ${lowConfVariants.length} 处低置信已折叠（疑似测序噪声/混合峰，展开逐条核对）` }}
      </button>
    </h4>
    <table class="seq-table clickable">
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
.badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 0.72rem; background: #EEE; }
.badge.bad { background: #FDE8E8; color: #C0392B; }
.conf-chip { display: inline-block; border-radius: 10px; padding: 1px 9px; font-size: 0.78rem; }
.conf-high { background: #E5F5E9; color: #227A36; }
.conf-medium { background: #FCF3DC; color: #9A6D00; }
.conf-low { background: #FBEAE8; color: #A03227; }
.mono { font-family: Consolas, monospace; }
</style>
