<script setup lang="ts">
/**
 * Read 摘要表
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：每条 read 的方向/落点/修剪/
 * Q/质量评级/一致性一行，与读取失败错误行。查看按钮只上报事件，
 * 峰图联动逻辑（自动显示、选中、滚动）留在父组件。
 */
import type { SequencingAnalysis } from '@/api'

type ReadRow = SequencingAnalysis['reads'][number]

defineProps<{
  reads: ReadRow[]
  /** 后端逐 read 解析/分析失败的错误列表 */
  errors: { filename: string; error: string }[]
}>()

const emit = defineEmits<{
  (e: 'view', index: number): void
}>()
</script>

<template>
  <div>
    <table class="seq-table" v-if="reads.length">
      <thead>
        <tr><th>文件</th><th>方向</th><th>比对区间</th><th>修剪后</th><th>平均Q</th><th>质量</th><th>一致性</th><th>证据查看</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in reads" :key="r.index">
          <td>{{ r.filename }}</td>
          <td>{{ r.direction === '+' ? '正向' : '反向' }}</td>
          <td>{{ r.ref_start }} - {{ r.ref_end }}</td>
          <td>{{ r.trimmed_length }} bp</td>
          <td>{{ r.mean_q }}</td>
          <td>
            <span v-if="r.grade" class="grade-chip" :class="'grade-' + r.grade"
                  :title="r.q20_ratio != null ? `Q20 比例 ${(r.q20_ratio * 100).toFixed(0)}%` : ''">
              {{ r.grade }}
            </span>
            <span v-else>-</span>
          </td>
          <td>{{ (r.identity * 100).toFixed(1) }}%</td>
          <td>
            <button class="mini-btn" @click="emit('view', r.index)">查看</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="errors.length" class="error-msg">
      {{ errors.map((e) => `${e.filename}: ${e.error}`).join('；') }}
    </p>
  </div>
</template>

<style scoped>
/* read 摘要表样式：随模板从 SequencingPanel.vue 迁入 */
.seq-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.seq-table th, .seq-table td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border-color, #eee); text-align: left; }
.seq-table th { background: var(--bg-secondary, #f7f7f7); }
.grade-chip { display: inline-block; min-width: 20px; text-align: center; font-weight: 700; border-radius: 6px; padding: 1px 7px; font-size: 0.8rem; }
.grade-A { background: #E5F5E9; color: #227A36; }
.grade-B { background: #FCF3DC; color: #9A6D00; }
.grade-C { background: #FBEAE8; color: #A03227; }
.mini-btn {
  border: 1px solid var(--border-color, #ddd); background: #fff; border-radius: 4px;
  font-size: 0.78rem; padding: 0.2rem 0.6rem; cursor: pointer; margin-left: 0.25rem;
}
.mini-btn:hover { background: var(--bg-secondary, #f5f5f5); }
.error-msg { color: #c0392b; font-size: 0.85rem; margin-top: 0.5rem; }
</style>
