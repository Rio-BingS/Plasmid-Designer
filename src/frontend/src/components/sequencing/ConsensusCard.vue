<script setup lang="ts">
/**
 * 共识序列卡（拼接结果 + 导出）
 *
 * 从 SequencingPanel.vue 拆出的子组件：共识序列分段渲染（与参考差异
 * 位黄色高亮）与 FASTA/GenBank 导出。导出走父组件注入的回调——
 * exportConsensus 需要 analysis_id，下载动作与后端调用细节留在父组件，
 * 本组件只管「仅导出实测覆盖区」开关（coveredOnly 由父组件持有默认值）。
 */
import { computed } from 'vue'
import type { SequencingAnalysis } from '@/api'

const props = defineProps<{
  analysis: SequencingAnalysis
  /** 默认 true（部分测序是常规策略：只导出 read 实测覆盖区域） */
  coveredOnly?: boolean
}>()

const coveredOnly = defineModel<boolean>('coveredOnly', { default: true })

const emit = defineEmits<{
  (e: 'download', format: string): void
}>()

/** 分段渲染：与参考不同的位点高亮（cons_index 精确对应共识序列下标） */
const consensusSegments = computed(() => {
  const seq = props.analysis.consensus.sequence
  const diffAt = new Set<number>()
  for (const d of props.analysis.consensus.diffs || []) {
    if (d.cons_index != null) diffAt.add(d.cons_index)
  }
  const out: { text: string; diff: boolean }[] = []
  for (let i = 0; i < seq.length; i++) {
    const d = diffAt.has(i)
    const last = out[out.length - 1]
    if (last && last.diff === d) last.text += seq[i]
    else out.push({ text: seq[i], diff: d })
  }
  return out
})
</script>

<template>
  <div class="consensus-box">
    <div class="trace-toolbar">
      <h4 class="section-title">拼接结果（Consensus，{{ analysis.consensus.sequence.length }} bp）</h4>
      <div>
        <label class="cov-only-toggle" title="部分测序是常规策略：只导出 read 实测覆盖的区域（FASTA 按段、GenBank 未测位置 N 屏蔽）">
          <input type="checkbox" v-model="coveredOnly">仅导出实测覆盖区
        </label>
        <button class="mini-btn" @click="emit('download', 'fasta')">导出 FASTA</button>
        <button class="mini-btn" @click="emit('download', 'genbank')">导出 GenBank</button>
      </div>
    </div>
    <pre class="consensus-pre"><span v-for="(s, i) in consensusSegments" :key="i" :class="{ 'cons-diff': s.diff }">{{ s.text }}</span></pre>
    <p v-if="analysis.consensus.diffs?.length" class="hint cons-hint">
      黄色高亮 = 共识序列与参考不同的位点（由测序证据投票写入，点击上方差异明细可核对峰图与比对）
    </p>
  </div>
</template>

<style scoped>
/* 共识卡样式：随模板从 SequencingPanel.vue 迁入 */
.consensus-box { background: #fff; border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.trace-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.35rem; }
.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.hint { color: #999; font-size: 0.85rem; }
.mini-btn {
  border: 1px solid var(--border-color, #ddd); background: #fff; border-radius: 4px;
  font-size: 0.78rem; padding: 0.2rem 0.6rem; cursor: pointer; margin-left: 0.25rem;
}
.mini-btn:hover { background: var(--bg-secondary, #f5f5f5); }
.cov-only-toggle {
  font-size: 0.78rem; margin-right: 0.5rem; cursor: pointer;
  display: inline-flex; align-items: center; gap: 0.25rem;
  vertical-align: middle; color: var(--text-secondary, #555);
}
.consensus-pre {
  font-family: Consolas, monospace; font-size: 0.72rem; line-height: 1.5;
  background: var(--bg-secondary, #f9f9f9); padding: 0.75rem; border-radius: 6px;
  max-height: 260px; overflow: auto; white-space: pre-wrap; word-break: break-all;
}
.cons-diff { background: #FFF3B8; border-radius: 2px; padding: 0 1px; }
.cons-hint { margin-top: 0.4rem; }
</style>
