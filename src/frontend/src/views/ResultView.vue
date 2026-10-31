<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, defineAsyncComponent } from 'vue'
import { useRouter } from 'vue-router'
import type { DesignResult } from '@/types'
import { getDesign, downloadGenbank, downloadPrimers, formatApiError } from '@/api'
import { splitWarning } from '@/utils/warning'
import type { MapDocument } from '@/map/types'
import { getDesignMap } from '@/map/api'
// 图谱查看器只在点"查看质粒图谱"后才需要：单独拆包
const MapViewer = defineAsyncComponent(() => import('@/map/components/MapViewer.vue'))

const props = defineProps<{
  designId: string
}>()

const router = useRouter()
const result = ref<DesignResult | null>(null)
const mapDoc = ref<MapDocument | null>(null)
const showMap = ref(false)
const mapLoading = ref(false)
const mapError = ref('')
const loading = ref(true)
const error = ref('')

/**
 * 插入片段落在哪条链：后端按酶切粘端算出来的物理结果，不是排布偏好。
 * 反向插入（负链）在图上就是箭头朝左；用户填反 5'/3' 两端酶时这里会显示正链。
 */
const insertStrand = computed(() => {
  const r = result.value
  if (!r) return ''
  // 覆盖式匹配而非坐标全等：位点 ATG 充当起始密码子时后端会把 CDS 注释
  // 扩展几 bp（insert_start/end 仍是克隆坐标），全等匹配会漏掉这条特征，
  // 链向卡片静默消失。扩展只朝外扩一端，覆盖判断两种方向都成立
  const hit = (r.construct_features ?? []).find(
    (f) => (f.type || '').toUpperCase() === 'CDS'
      && f.start <= (r.insert_start ?? NaN) && f.end >= (r.insert_end ?? NaN),
  )
  if (hit?.strand === '-') return '负链（-）'
  if (hit?.strand === '+') return '正链（+）'
  return ''
})

/** 「插入方向和载体启动子相反」这类告警要顶到结果最上面，别埋在页尾 */
const orientationAlert = computed(
  () => (result.value?.warnings ?? []).find((w) => w.includes('插入方向和载体启动子相反')) ?? '',
)

function goToSequencing() {
  router.push({ path: '/sequencing', query: { mode: 'design', ref: props.designId } })
}

let pollInterval: number | null = null

function apiError(e: any): string {
  return formatApiError(e, '请求失败')
}

async function fetchResult() {
  try {
    const data = await getDesign(props.designId)
    result.value = data

    if (data.status === 'pending' || data.status === 'running') {
      if (!pollInterval) {
        pollInterval = window.setInterval(fetchResult, 2000)
      }
    } else if (pollInterval) {
      clearInterval(pollInterval)
      pollInterval = null
    }

    loading.value = false
  } catch (e: any) {
    error.value = apiError(e)
    loading.value = false
    if (pollInterval) {
      clearInterval(pollInterval)
      pollInterval = null
    }
  }
}

async function handleDownloadGenbank() {
  try {
    await downloadGenbank(props.designId)
  } catch (e: any) {
    alert('下载失败: ' + apiError(e))
  }
}

async function handleDownloadPrimers() {
  try {
    await downloadPrimers(props.designId)
  } catch (e: any) {
    alert('下载失败: ' + apiError(e))
  }
}

// ===== 引物复制 =====
const copiedName = ref('')

function copyText(text: string) {
  const fallback = () => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy') } catch { /* 忽略 */ }
    document.body.removeChild(ta)
  }
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(fallback)
  } else {
    fallback()
  }
}

function primerRowText(p: any): string {
  return `${p.name}\t${p.full_sequence || p.sequence}`
}

function copyPrimer(p: any) {
  copyText(primerRowText(p))
  copiedName.value = p.name
  setTimeout(() => { if (copiedName.value === p.name) copiedName.value = '' }, 1200)
}

function copyAllPrimers() {
  const primers = result.value?.primers || []
  copyText(primers.map(primerRowText).join('\n'))
  copiedName.value = '__all__'
  setTimeout(() => { if (copiedName.value === '__all__') copiedName.value = '' }, 1200)
}

function formatSequence(seq: string): string {
  if (!seq) return ''
  const lines = []
  for (let i = 0; i < seq.length; i += 60) {
    const chunk = seq.substring(i, i + 60)
    const groups = chunk.match(/.{1,10}/g)
    lines.push((groups || []).join(' '))
  }
  return lines.join('\n')
}

async function copySequence() {
  if (!result.value?.optimized_sequence) return
  try {
    await navigator.clipboard.writeText(result.value.optimized_sequence)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = result.value.optimized_sequence
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
}

async function fetchMapData() {
  mapLoading.value = true
  mapError.value = ''
  try {
    mapDoc.value = await getDesignMap(props.designId)
    showMap.value = true
  } catch (e: any) {
    mapError.value = `图谱加载失败：${apiError(e)}`
  } finally {
    mapLoading.value = false
  }
}

function goAnalyze() {
  const seq = result.value?.optimized_sequence || result.value?.input_sequence || ''
  router.push({ path: '/analysis', query: seq ? { sequence: seq } : {} })
}

function goRedesign() {
  router.push('/design')
}

// 路由参数变化时组件会被复用（如批量结果页内跳转另一 design），必须重载
watch(() => props.designId, () => {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null }
  error.value = ''
  loading.value = true
  fetchResult()
})

onMounted(() => {
  fetchResult()
})

onUnmounted(() => {
  if (pollInterval) {
    clearInterval(pollInterval)
    pollInterval = null
  }
})
</script>

<template>
  <div class="result-page">
    <div v-if="loading" class="loading">
      <div class="spinner"></div>
      <p>加载中...</p>
    </div>
    
    <div v-else-if="error" class="error-state">
      <p>{{ error }}</p>
    </div>
    
    <div v-else-if="result" class="result-content">
      <!-- 状态指示 -->
      <div class="status-card" :class="result.status">
        <div class="status-icon">
          <span v-if="result.status === 'pending'">⏳</span>
          <span v-else-if="result.status === 'running'">⚙️</span>
          <span v-else-if="result.status === 'completed'">✅</span>
          <span v-else-if="result.status === 'failed'">❌</span>
        </div>
        <div class="status-text">
          <h2>设计{{ result.status === 'completed' ? '完成' : result.status === 'failed' ? '失败' : '中' }}</h2>
          <p v-if="result.status === 'running'">正在处理序列，请稍候...</p>
          <p v-else-if="result.status === 'completed'">
            <template v-if="result.plasmid_name">质粒名称: {{ result.plasmid_name }}　</template>
            设计 ID: {{ result.design_id }}
          </p>
        </div>
      </div>
      
      <!-- 加载中状态 -->
      <div v-if="result.status === 'pending' || result.status === 'running'" class="loading-state">
        <div class="spinner"></div>
        <p>正在处理设计任务...</p>
      </div>
      
      <!-- 失败状态 -->
      <div v-else-if="result.status === 'failed'" class="failed-state">
        <h3>错误信息</h3>
        <ul>
          <li v-for="err in result.errors" :key="err">{{ err }}</li>
        </ul>
      </div>
      
      <!-- 完成状态 -->
      <div v-else class="completed-state">
        <!-- 插入方向告警：5'/3' 两端酶填反时只有这里能看出来 -->
        <div v-if="orientationAlert" class="orientation-alert">
          <strong>⚠️ 插入方向需要核对</strong>
          <p>{{ orientationAlert }}</p>
        </div>

        <!-- 优化结果 -->
        <div class="result-section">
          <h2>优化结果</h2>
          <div class="metrics-grid">
            <div class="metric-card">
              <span class="metric-label">CAI 值</span>
              <span class="metric-value">{{ result.cai?.toFixed(3) || 'N/A' }}</span>
            </div>
            <div class="metric-card">
              <span class="metric-label">GC 含量</span>
              <span class="metric-value">{{ result.gc_content?.toFixed(1) || 'N/A' }}%</span>
            </div>
            <div class="metric-card">
              <span class="metric-label">构建体长度</span>
              <span class="metric-value">{{ result.final_length?.toLocaleString() || 'N/A' }} bp</span>
            </div>
            <div v-if="result.insert_start && result.insert_end" class="metric-card">
              <span class="metric-label">插入区间</span>
              <span class="metric-value">{{ result.insert_start }}-{{ result.insert_end }}</span>
            </div>
            <div v-if="insertStrand" class="metric-card">
              <span class="metric-label">插入方向</span>
              <span class="metric-value">{{ insertStrand }}</span>
            </div>
          </div>
        </div>
        
        <!-- 查看图谱按钮 -->
            <div v-if="!showMap" class="result-section map-toggle-section">
              <button class="btn btn-secondary" :disabled="mapLoading" @click="fetchMapData">
                {{ mapLoading ? '加载中…' : '🗺️ 查看质粒图谱' }}
              </button>
              <p v-if="mapError" class="map-error">{{ mapError }}</p>
            </div>

            <!-- 序列显示 -->
        <div v-if="result.optimized_sequence || result.input_sequence" class="result-section">
          <h2>优化后序列</h2>
      <div class="sequence-toolbar">
        <span class="seq-length">{{ (result.optimized_sequence || result.input_sequence).length }} bp</span>
        <button class="btn btn-secondary btn-small" @click="copySequence">复制序列</button>
      </div>
      <p v-if="result.start_codon_removed" class="seq-trim-note">
        ℹ️ 此处是<b>完整 CDS</b> 的优化序列（与输入蛋白一一对应）。构建体的起始密码子由
        载体位点提供（如 NdeI 的 CATATG），克隆用的工作序列（引物退火区、组装片段）
        已去掉开头的 ATG，比这条序列短 3 bp 属正常现象，蛋白不变。
      </p>
      <div class="sequence-display">
        {{ formatSequence(result.optimized_sequence || result.input_sequence) }}
      </div>
        </div>
        
        <!-- 质粒图谱 -->
      <div v-if="showMap && mapDoc" class="result-section">
        <h2>质粒图谱</h2>
        <MapViewer :doc="mapDoc" />
      </div>

      <!-- Sanger 测序验证（独立模块） -->
      <div v-if="result.status === 'completed'" class="result-section sequencing-entry">
        <div>
          <h2>Sanger 测序验证</h2>
          <p class="seq-entry-hint">上传 .ab1 测序文件，验证构建体序列是否与设计一致（比对、拼接、突变注释、峰图）</p>
        </div>
        <button class="btn btn-secondary" @click="goToSequencing">前往测序分析 →</button>
      </div>

      <!-- 引物信息 -->
      <div class="result-section">
        <h2 v-if="result.cloning_method === 'gene_synthesis'">寡核苷酸设计</h2>
        <h2 v-else>引物设计</h2>

        <div class="primers-toolbar">
          <span v-if="result.cloning_method === 'gene_synthesis'" class="oligo-summary">
            共 {{ result.primers.length }} 条寡核苷酸（正反链交替重叠组装）
          </span>
          <span v-else class="oligo-summary">共 {{ result.primers.length }} 条引物</span>
          <button class="btn btn-secondary copy-all-btn" @click="copyAllPrimers">
            {{ copiedName === '__all__' ? '✓ 已复制' : '📋 复制全部（名称+序列）' }}
          </button>
        </div>

        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>序列</th>
              <th>长度</th>
              <th>Tm</th>
              <th>GC%</th>
              <th>备注</th>
              <th>复制</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(primer, i) in result.primers" :key="`${primer.name}-${i}`">
              <td class="name-cell">{{ primer.name }}</td>
              <td class="sequence-cell">{{ primer.full_sequence || primer.sequence }}</td>
              <td>{{ primer.length }} bp</td>
              <td>{{ primer.tm?.toFixed(1) ?? '—' }}°C</td>
              <td>{{ primer.gc_content?.toFixed(1) ?? '—' }}%</td>
              <td class="notes-cell">{{ primer.notes || '' }}</td>
              <td>
                <button
                  class="copy-row-btn"
                  :title="`复制 ${primer.name} 的名称和序列`"
                  @click="copyPrimer(primer)"
                >{{ copiedName === primer.name ? '✓' : '📋' }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
        <!-- 验证结果 -->
        <div class="result-section">
          <h2>验证结果</h2>
          <div class="validation-status" :class="{ passed: result.validation_passed }">
            <span v-if="result.validation_passed">✅ 验证通过</span>
            <span v-else>⚠️ 存在警告</span>
          </div>
          
          <div v-if="result.warnings.length" class="warnings">
            <h4>警告</h4>
            <ul>
              <!-- 摘要平铺、详细解释折叠：全句平铺过于冗长（用户反馈） -->
              <li v-for="(warn, wi) in result.warnings" :key="wi">
                <details class="warning-item">
                  <summary>⚠️ {{ splitWarning(warn).summary }}</summary>
                  <p v-if="splitWarning(warn).detail" class="warning-detail">
                    {{ splitWarning(warn).detail }}
                  </p>
                </details>
              </li>
            </ul>
          </div>
        </div>
        
        <!-- 克隆实验方案 -->
      <div v-if="result.clone_protocol" class="result-section">
        <h2>克隆实验方案</h2>
        <div class="protocol-content">
          <pre>{{ result.clone_protocol }}</pre>
        </div>
      </div>

      <!-- 下载按钮 -->
        <div class="download-section">
          <h2>下载结果</h2>
          <div class="download-buttons">
            <button @click="handleDownloadGenbank" class="btn btn-primary">
              📄 下载 GenBank 文件
            </button>
            <button @click="handleDownloadPrimers" class="btn btn-secondary">
              📋 下载引物订单表
            </button>
            <button @click="goAnalyze" class="btn btn-secondary">
              🔍 分析插入序列
            </button>
            <button @click="goRedesign" class="btn btn-secondary">
              🔄 新建设计
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.result-page {
  max-width: 900px;
  margin: 0 auto;
}

.loading, .loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  gap: 1rem;
}

.status-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1.5rem;
  background: white;
  border-radius: 0.75rem;
  margin-bottom: 2rem;
  box-shadow: var(--shadow);
}

.status-card.completed {
  border-left: 4px solid var(--success-color);
}

.status-card.running {
  border-left: 4px solid var(--secondary-color);
}

.status-card.failed {
  border-left: 4px solid var(--error-color);
}

.status-icon {
  font-size: 2rem;
}

.status-text h2 {
  font-size: 1.25rem;
  margin-bottom: 0.25rem;
}

.status-text p {
  color: var(--text-secondary);
  font-size: 0.875rem;
}

.orientation-alert {
  background: #fff4e5;
  border: 1px solid #ffb74d;
  border-left: 4px solid #f57c00;
  border-radius: 0.5rem;
  padding: 0.9rem 1.1rem;
  margin-bottom: 1.5rem;
  color: #6b3d00;
}

.orientation-alert strong {
  display: block;
  margin-bottom: 0.35rem;
  font-size: 1rem;
}

.orientation-alert p {
  margin: 0;
  font-size: 0.9rem;
  line-height: 1.55;
}

.result-section {
  background: white;
  padding: 1.5rem;
  border-radius: 0.75rem;
  margin-bottom: 1.5rem;
  box-shadow: var(--shadow);
}

.result-section h2 {
  font-size: 1.125rem;
  margin-bottom: 1rem;
  color: var(--primary-color);
}

.sequencing-entry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.seq-entry-hint {
  color: var(--text-secondary, #888);
  font-size: 0.85rem;
  margin: 0;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
}

.metric-card {
  display: flex;
  flex-direction: column;
  padding: 1rem;
  background: var(--bg-secondary);
  border-radius: 0.5rem;
}

.metric-label {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-bottom: 0.25rem;
}

.metric-value {
  font-size: 1.5rem;
  font-weight: 600;
}

.primers-table {
  overflow-x: auto;
}

.primers-table table {
  width: 100%;
  border-collapse: collapse;
}

.primers-table th,
.primers-table td {
  padding: 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}

.primers-table th {
  font-weight: 600;
  background: var(--bg-secondary);
}

.sequence-cell {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  word-break: break-all;
  max-width: 320px;
}

.primers-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.oligo-summary {
  font-size: 0.875rem;
  color: var(--text-secondary);
}

.copy-all-btn {
  padding: 0.35rem 0.75rem;
  font-size: 0.8rem;
}

.copy-row-btn {
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--border-color);
  background: white;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.8rem;
}

.copy-row-btn:hover {
  background: var(--bg-secondary);
}

.name-cell {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  white-space: nowrap;
}

.sequence-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
}

.seq-length {
  font-size: 0.875rem;
  color: var(--text-secondary);
}

.seq-trim-note {
  margin: 0 0 0.75rem;
  font-size: 0.8rem;
  line-height: 1.5;
  color: var(--text-secondary);
  background: var(--bg-secondary, #f7fafc);
  border-left: 3px solid #4299e1;
  padding: 0.5rem 0.75rem;
  border-radius: 0.25rem;
}

.btn-small {
  padding: 0.25rem 0.75rem;
  font-size: 0.75rem;
}

.sequence-display {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--bg-secondary);
  padding: 1rem;
  border-radius: 0.5rem;
  max-height: 300px;
  overflow-y: auto;
  color: var(--text-secondary);
}

.validation-status {
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  background: rgba(239, 68, 68, 0.1);
  color: var(--error-color);
}

.validation-status.passed {
  background: rgba(16, 185, 129, 0.1);
  color: var(--success-color);
}

.warnings {
  margin-top: 1rem;
}

.warnings h4 {
  font-size: 0.875rem;
  margin-bottom: 0.5rem;
}

.warnings ul {
  list-style: disc;
  padding-left: 1.5rem;
}

.warnings li {
  font-size: 0.875rem;
  color: var(--text-secondary);
  margin-bottom: 0.25rem;
}

.warning-item summary {
  cursor: pointer;
  list-style-position: outside;
}

.warning-item[open] summary {
  margin-bottom: 0.25rem;
}

.warning-detail {
  margin: 0.25rem 0 0.5rem;
  padding-left: 0.75rem;
  border-left: 2px solid var(--border-color, #e0e0e0);
  color: var(--text-secondary);
}

.download-section {
  background: white;
  padding: 1.5rem;
  border-radius: 0.75rem;
  box-shadow: var(--shadow);
}

.download-section h2 {
  font-size: 1.125rem;
  margin-bottom: 1rem;
}

.download-buttons {
  display: flex;
  gap: 1rem;
}

.protocol-content {
  max-height: 500px;
  overflow-y: auto;
}

.protocol-content pre {
  white-space: pre-wrap;
  word-wrap: break-word;
  font-size: 0.875rem;
  line-height: 1.6;
  color: var(--text-secondary);
  margin: 0;
}
.notes-cell {
  font-size: 0.75rem;
  color: var(--text-secondary);
  max-width: 200px;
}

.map-toggle-section {
  text-align: center;
  padding: 1rem;
}

.oligo-summary {
  margin-bottom: 0.75rem;
  font-size: 0.875rem;
  color: var(--text-secondary);
}
.map-error {
  margin-top: 0.5rem;
  color: var(--error-color);
  font-size: 0.875rem;
}
</style>
