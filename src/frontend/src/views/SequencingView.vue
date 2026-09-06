<script setup lang="ts">
/**
 * Sanger 测序分析 — 独立模块
 * ① 选择参考序列（默认为设计结果；也可选载体库，支持 ?mode=&ref= 深链）→
 * ② 上传 .ab1 一键自动分析（SequencingPanel）→
 * ③ 历史分析（查看 / 删除）
 */
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getVectors, getDesign, listSequencingAnalyses, getSequencingAnalysis, deleteSequencingAnalysis,
  type SequencingAnalysis, type SequencingAnalysisSummary
} from '@/api'
import type { VectorInfo } from '@/types'
import { listRecentDesigns, recordRecentDesign, type RecentDesign } from '@/utils/recentDesigns'
import SequencingPanel from '@/components/SequencingPanel.vue'

const route = useRoute()
const router = useRouter()

// ==================== 参考序列选择 ====================
// 测序验证的主场景是核对「自己设计的构建体」，默认选中设计结果；载体库作为备选
const mode = ref<'design' | 'vector'>(route.query.mode === 'vector' ? 'vector' : 'design')
const vectorId = ref('')
const vectors = ref<VectorInfo[]>([])
const vectorsLoading = ref(false)
const search = ref('')

// 设计结果：输入框内容与已确认的选择分离，避免逐键触发面板重挂
const designInput = ref('')
const designId = ref('')
const designInfo = ref<{ id: string; name: string; length: number } | null>(null)
const designChecking = ref(false)
const designError = ref('')
const recentDesigns = ref<RecentDesign[]>([])

function designDisplayName(vectorName: string): string {
  return vectorName || '插入片段（无载体）'
}

const filteredVectors = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return vectors.value
  return vectors.value.filter((v) =>
    v.name.toLowerCase().includes(q) ||
    v.vector_type.toLowerCase().includes(q) ||
    (v.host || []).some((h) => h.toLowerCase().includes(q)) ||
    (v.antibiotic_resistance || []).some((a) => a.toLowerCase().includes(q))
  )
})

const vectorName = computed(() =>
  vectors.value.find((v) => v.id === vectorId.value)?.name || ''
)

const refLabel = computed(() => {
  if (mode.value === 'vector') return vectorName.value
  if (!designInfo.value) return ''
  return `${designInfo.value.name} · ${designInfo.value.length} bp`
})

async function loadVectors() {
  vectorsLoading.value = true
  try {
    vectors.value = await getVectors()
  } catch (e) {
    console.warn('载体列表加载失败', e)
  } finally {
    vectorsLoading.value = false
  }
}

function selectVector(id: string) {
  vectorId.value = vectorId.value === id ? '' : id
  if (vectorId.value) {
    router.replace({ query: { mode: 'vector', ref: vectorId.value } })
  }
}

/** 校验设计 ID 有效且已完成，成功后登记为当前参考 */
async function validateDesign(id: string): Promise<boolean> {
  designError.value = ''
  designChecking.value = true
  try {
    const d = await getDesign(id)
    if (d.status !== 'completed') {
      designInfo.value = null
      designError.value = '该设计尚未完成，无法作为测序参考'
      return false
    }
    const length = d.final_length ?? d.construct_sequence?.length ?? 0
    designInfo.value = { id, name: designDisplayName(d.vector_name || ''), length }
    recordRecentDesign({
      design_id: id,
      vector_name: d.vector_name || '',
      length,
      time: d.created_at,
    })
    recentDesigns.value = listRecentDesigns()
    return true
  } catch {
    designInfo.value = null
    designError.value = '未找到该设计 ID，请检查输入（设计记录可能已过期）'
    return false
  } finally {
    designChecking.value = false
  }
}

function selectRecent(d: RecentDesign) {
  if (designId.value === d.design_id) {
    // 再次点击取消选择
    designId.value = ''
    designInfo.value = null
    router.replace({ query: { mode: 'design' } })
    return
  }
  designId.value = d.design_id
  designInput.value = d.design_id
  designInfo.value = {
    id: d.design_id,
    name: designDisplayName(d.vector_name),
    length: d.length,
  }
  designError.value = ''
  router.replace({ query: { mode: 'design', ref: d.design_id } })
}

async function confirmDesign() {
  const id = designInput.value.trim()
  if (!id) return
  const ok = await validateDesign(id)
  if (ok) {
    designId.value = id
    router.replace({ query: { mode: 'design', ref: id } })
  } else {
    designId.value = ''
    router.replace({ query: { mode: 'design' } })
  }
}

// 深链预选：设计模式需校验有效性后才展示分析面板
onMounted(async () => {
  recentDesigns.value = listRecentDesigns()
  loadVectors()
  const refId = (route.query.ref as string) || ''
  if (refId) {
    if (mode.value === 'vector') {
      vectorId.value = refId
    } else {
      designInput.value = refId
      designId.value = refId
      await validateDesign(refId)
    }
  }
  refreshHistory()
})

// ==================== 分析面板 ====================
const panelKey = ref(0)
const preset = ref<SequencingAnalysis | null>(null)
const viewingHistory = ref(false)

const panelVisible = computed(
  () => (mode.value === 'vector' && !!vectorId.value) ||
    (mode.value === 'design' && !!designInfo.value) ||
    !!preset.value
)

// 切换参考序列时重挂面板，清掉旧结果
watch([mode, vectorId, designId], () => {
  panelKey.value++
  viewingHistory.value = false
  preset.value = null
})

function stopHistoryView() {
  viewingHistory.value = false
  preset.value = null
}

function onAnalyzed() {
  viewingHistory.value = false
  preset.value = null
  refreshHistory()
}

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

function formatTime(iso: string): string {
  return (iso || '').replace('T', ' ').slice(0, 16)
}
</script>

<template>
  <div class="sequencing-page">
    <div class="page-header">
      <h1>🔬 Sanger 测序分析</h1>
      <p class="subtitle">
        选择参考序列，上传 .ab1 测序文件，自动完成修剪、比对、拼接、突变注释与峰图查看
      </p>
    </div>

    <!-- ① 参考序列 -->
    <div class="ref-card">
      <div class="ref-header">
        <span class="step-no">①</span>
        <h2>选择参考序列</h2>
        <div class="mode-tabs">
          <button :class="{ active: mode === 'design' }" @click="mode = 'design'">设计结果</button>
          <button :class="{ active: mode === 'vector' }" @click="mode = 'vector'">载体库</button>
        </div>
      </div>

      <!-- 设计结果（默认）：验证自己设计的构建体 -->
      <template v-if="mode === 'design'">
        <template v-if="recentDesigns.length">
          <p class="picker-label">最近的设计结果</p>
          <div class="vector-grid">
            <button
              v-for="d in recentDesigns"
              :key="d.design_id"
              class="vector-card"
              :class="{ selected: designId === d.design_id }"
              @click="selectRecent(d)"
            >
              <span class="vector-name">{{ designDisplayName(d.vector_name) }}</span>
              <span class="vector-meta mono">{{ d.design_id }}</span>
              <span class="vector-meta">{{ d.length }} bp · {{ formatTime(d.time) }}</span>
            </button>
          </div>
          <p class="picker-label spaced">或输入设计任务 ID</p>
        </template>
        <div class="design-row">
          <input v-model="designInput" class="search-input" placeholder="输入设计任务 ID（如 design_xxxxxxxxxxxxxx）" @keyup.enter="confirmDesign" />
          <button class="btn-confirm" :disabled="designChecking || !designInput.trim()" @click="confirmDesign">
            {{ designChecking ? '校验中…' : '确定' }}
          </button>
        </div>
        <p v-if="designError" class="hint error-hint">{{ designError }}</p>
        <p class="hint">设计 ID 可在设计结果页 URL 中找到，或从设计结果页点击「测序验证」直接跳转</p>
      </template>

      <!-- 载体库（备选） -->
      <template v-else>
        <input v-model="search" class="search-input" placeholder="搜索载体名称 / 类型 / 宿主 / 抗性…" />
        <p v-if="vectorsLoading" class="hint">加载载体列表…</p>
        <p v-else-if="!filteredVectors.length" class="hint">未找到匹配的载体</p>
        <div v-else class="vector-grid">
          <button
            v-for="v in filteredVectors.slice(0, 24)"
            :key="v.id"
            class="vector-card"
            :class="{ selected: vectorId === v.id }"
            @click="selectVector(v.id)"
          >
            <span class="vector-name">{{ v.name }}</span>
            <span class="vector-meta">{{ v.vector_type }}</span>
            <span v-if="v.antibiotic_resistance?.length" class="vector-meta">
              {{ v.antibiotic_resistance.join(', ') }}
            </span>
          </button>
        </div>
        <p v-if="filteredVectors.length > 24" class="hint">仅显示前 24 个，请用搜索缩小范围</p>
      </template>
    </div>

    <!-- ② 分析 -->
    <div v-if="panelVisible" class="panel-card">
      <div class="ref-header">
        <span class="step-no">②</span>
        <h2>上传测序文件并分析</h2>
        <span v-if="refLabel" class="ref-chip" :title="`参考序列：${refLabel}`">参考：{{ refLabel }}</span>
        <button v-if="viewingHistory" class="back-analysis-btn" @click="stopHistoryView">返回新分析</button>
      </div>
      <SequencingPanel
        :key="panelKey"
        :reference-id="mode === 'vector' ? vectorId : designId"
        :mode="mode"
        :preset="preset"
        @analyzed="onAnalyzed"
      />
    </div>
    <div v-else class="panel-empty">
      <p>请先在上方{{ mode === 'vector' ? '选择一个载体' : '选择或输入一个已完成的设计' }}</p>
    </div>

    <!-- ③ 历史分析 -->
    <div class="history-card">
      <div class="ref-header">
        <span class="step-no">③</span>
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

.ref-card, .panel-card, .history-card {
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

.mode-tabs {
  margin-left: auto;
  display: flex;
  border: 1px solid var(--border-color, #DDD);
  border-radius: 8px;
  overflow: hidden;
}

.mode-tabs button {
  border: none;
  background: #fff;
  padding: 0.4rem 1rem;
  font-size: 0.85rem;
  cursor: pointer;
  color: var(--text-secondary, #666);
}

.mode-tabs button.active {
  background: var(--primary-color, #4E79C7);
  color: #fff;
}

.search-input {
  width: 100%;
  padding: 0.55rem 0.9rem;
  border: 1px solid var(--border-color, #DDD);
  border-radius: 8px;
  font-size: 0.9rem;
  margin-bottom: 0.75rem;
  box-sizing: border-box;
}

.picker-label { color: var(--text-secondary, #888); font-size: 0.82rem; margin: 0 0 0.5rem; }
.picker-label.spaced { margin-top: 1rem; }

.ref-chip {
  margin-left: 0.5rem;
  padding: 0.15rem 0.6rem;
  background: #F3F7FD;
  border: 1px solid #C7D8EF;
  color: #2B54C4;
  border-radius: 999px;
  font-size: 0.78rem;
  max-width: 340px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.error-hint { color: #C0392B; }

.vector-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.6rem;
}

.vector-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.15rem;
  padding: 0.7rem 0.9rem;
  border: 1.5px solid var(--border-color, #E5E7EB);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
}

.vector-card:hover { border-color: #A9C4E8; }
.vector-card.selected { border-color: var(--primary-color, #4E79C7); background: #F3F7FD; }
.vector-name { font-weight: 600; font-size: 0.9rem; }
.vector-meta { font-size: 0.75rem; color: var(--text-secondary, #888); }

.design-row { display: flex; gap: 0.5rem; }
.design-row .search-input { margin-bottom: 0; flex: 1; }
.btn-confirm {
  padding: 0.55rem 1.2rem;
  border: none;
  border-radius: 8px;
  background: var(--primary-color, #4E79C7);
  color: #fff;
  cursor: pointer;
}

.panel-empty {
  text-align: center;
  padding: 2rem;
  color: var(--text-secondary, #999);
  border: 1px dashed var(--border-color, #DDD);
  border-radius: 12px;
}

.hint { color: var(--text-secondary, #999); font-size: 0.85rem; }

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
