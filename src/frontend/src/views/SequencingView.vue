<script setup lang="ts">
/**
 * Sanger 测序分析 — 独立模块
 * ① 上传参考序列文件（.gb/.fasta/.dna）+ .ab1（同一文件夹一起导入）一键分析
 *    从设计结果页 / 载体页跳转（?mode=design|vector&ref=ID）时自动带入参考序列
 * ② 历史分析（查看 / 删除）
 */
import { ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchDesignGenbankFile, fetchVectorGenbankFile,
  listSequencingAnalyses, getSequencingAnalysis, deleteSequencingAnalysis,
  type SequencingAnalysis, type SequencingAnalysisSummary
} from '@/api'
import SequencingPanel from '@/components/SequencingPanel.vue'

const route = useRoute()

// ==================== 深链预填参考序列 ====================
// 仅 ?mode=design|vector&ref=ID 时带入；无参数时清空——普通 /sequencing
// 始终是空白状态（组件被路由复用时也不残留上一次的自动参考）
const initialReference = ref<File | null>(null)
const stageError = ref('')
const staging = ref(false)

// 请求序号：响应期间路由已变化时丢弃过期结果（否则迟到的响应会把参考
// 又塞回已清空的普通页——竞态残留）
let loadSeq = 0

async function loadReferenceFromRoute() {
  const seq = ++loadSeq
  const refId = route.query.ref as string
  const mode = route.query.mode as string
  if (!refId) {
    initialReference.value = null
    stageError.value = ''
    staging.value = false
    return
  }
  staging.value = true
  stageError.value = ''
  try {
    const file = mode === 'vector'
      ? await fetchVectorGenbankFile(refId)
      : await fetchDesignGenbankFile(refId)
    if (seq !== loadSeq) return
    initialReference.value = file
  } catch {
    if (seq !== loadSeq) return
    stageError.value = '未能自动带入参考序列（可能已过期），请手动上传参考文件'
  } finally {
    if (seq === loadSeq) staging.value = false
  }
}

onMounted(() => {
  loadReferenceFromRoute()
  refreshHistory()
})

// 组件被复用（如从深链 URL 点导航回普通 /sequencing）时同步清空
watch(() => route.query.ref, () => {
  if (route.path === '/sequencing') loadReferenceFromRoute()
})

// ==================== 历史分析 ====================
const history = ref<SequencingAnalysisSummary[]>([])
const historyLoading = ref(false)

async function refreshHistory() {
  historyLoading.value = true
  try {
    history.value = await listSequencingAnalyses()
  } catch {
    history.value = []
  } finally {
    historyLoading.value = false
  }
}

async function viewHistory(id: string) {
  try {
    preset.value = await getSequencingAnalysis(id)
    viewingHistory.value = true
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } catch (e: any) {
    alert(e.response?.data?.detail || '分析记录不存在（可能已过期）')
    refreshHistory()
  }
}

async function removeHistory(id: string) {
  if (!confirm('确定删除该分析记录？')) return
  try {
    await deleteSequencingAnalysis(id)
    if (preset.value?.analysis_id === id) stopHistoryView()
    refreshHistory()
  } catch { /* 忽略 */ }
}

// ==================== 面板状态（历史回看 / 新分析切换） ====================
const preset = ref<SequencingAnalysis | null>(null)
const viewingHistory = ref(false)

function stopHistoryView() {
  viewingHistory.value = false
  preset.value = null
}

function onAnalyzed() {
  viewingHistory.value = false
  preset.value = null
  refreshHistory()
}

function formatTime(iso: string): string {
  return (iso || '').replace('T', ' ').slice(0, 16)
}
</script>

<template>
  <div class="sequencing-page">
    <div class="page-header">
      <h1>🔬 Sanger 测序分析</h1>
      <p class="subtitle">
        参考序列文件与 .ab1 测序文件放进同一文件夹一起导入，自动完成比对、拼接、突变注释与峰图查看
      </p>
    </div>

    <!-- ① 上传并分析 -->
    <div class="panel-card">
      <div class="ref-header">
        <span class="step-no">①</span>
        <h2>导入参考序列与测序文件</h2>
        <button v-if="viewingHistory" class="back-analysis-btn" @click="stopHistoryView">返回新分析</button>
      </div>
      <p v-if="staging" class="hint staging-hint">正在带入参考序列…</p>
      <p v-else-if="stageError" class="hint error-hint">{{ stageError }}</p>
      <SequencingPanel
        :initial-reference="initialReference"
        :preset="preset"
        @analyzed="onAnalyzed"
      />
    </div>

    <!-- ② 历史分析 -->
    <div class="history-card">
      <div class="ref-header">
        <span class="step-no">②</span>
        <h2>历史分析</h2>
        <button class="refresh-btn" @click="refreshHistory">↻ 刷新</button>
      </div>
      <p v-if="historyLoading" class="hint">加载中…</p>
      <p v-else-if="!history.length" class="hint">暂无分析记录（分析结果在服务重启后清空）</p>
      <table v-else class="history-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>样品 / 参考</th>
            <th>reads</th>
            <th>覆盖</th>
            <th>差异数</th>
            <th>结论</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in history" :key="h.analysis_id">
            <td class="mono">{{ formatTime(h.created_at) }}</td>
            <td>{{ h.sample_name || h.analysis_id }}</td>
            <td>{{ h.read_count }}</td>
            <td>{{ h.coverage_percent }}%</td>
            <td>
              <span :class="h.variant_count ? 'var-badge bad' : 'var-badge ok'">{{ h.variant_count }}</span>
            </td>
            <td class="conclusion-cell">{{ h.conclusion }}</td>
            <td>
              <button class="mini-btn" @click="viewHistory(h.analysis_id)">查看</button>
              <button class="mini-btn danger" @click="removeHistory(h.analysis_id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.sequencing-page {
  max-width: 1100px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.page-header h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
.subtitle { color: var(--text-secondary, #888); font-size: 0.9rem; }

.panel-card, .history-card {
  background: #fff;
  border: 1px solid var(--border-color, #E5E7EB);
  border-radius: 12px;
  padding: 1.25rem 1.5rem;
}

.ref-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 1rem;
}

.ref-header h2 { font-size: 1.05rem; margin: 0; }
.step-no { color: var(--primary-color, #4E79C7); font-weight: 700; }

.hint { color: var(--text-secondary, #999); font-size: 0.85rem; }
.staging-hint { margin: 0 0 0.5rem; }
.error-hint { color: #C0392B; margin: 0 0 0.5rem; }

.history-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.history-table th, .history-table td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border-color, #EEE); text-align: left; }
.history-table th { background: var(--bg-secondary, #F7F7F7); }
.conclusion-cell { max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mono { font-family: Consolas, monospace; }

.var-badge { display: inline-block; min-width: 1.6rem; text-align: center; padding: 1px 6px; border-radius: 999px; font-size: 0.75rem; }
.var-badge.bad { background: #FDE8E8; color: #C0392B; }
.var-badge.ok { background: #E8F6EC; color: #2E9E44; }

.mini-btn {
  border: 1px solid var(--border-color, #DDD);
  background: #fff;
  border-radius: 4px;
  font-size: 0.78rem;
  padding: 0.2rem 0.6rem;
  cursor: pointer;
  margin-right: 0.3rem;
}

.mini-btn:hover { background: var(--bg-secondary, #F5F5F5); }
.mini-btn.danger { color: #C0392B; }
.refresh-btn, .back-analysis-btn {
  margin-left: auto;
  border: 1px solid var(--border-color, #DDD);
  background: #fff;
  border-radius: 6px;
  padding: 0.3rem 0.8rem;
  font-size: 0.8rem;
  cursor: pointer;
}
</style>
