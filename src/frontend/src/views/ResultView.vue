<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import type { DesignResult } from '@/types'
import { getDesign, downloadGenbank, downloadPrimers, getDesignMapData } from '@/api'
import PlasmidMap from '@/components/PlasmidMap.vue'
import SequenceView from '@/components/SequenceView.vue'
import SequencingPanel from '@/components/SequencingPanel.vue'

const props = defineProps<{
  designId: string
}>()

const router = useRouter()
const result = ref<DesignResult | null>(null)
const mapData = ref<any>(null)
const showMap = ref(false)
const loading = ref(true)
const error = ref('')

let pollInterval: number | null = null

function apiError(e: any): string {
  const d = e.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: any) => x.msg || JSON.stringify(x)).join('; ')
  return e.message || '请求失败'
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
  try {
    const data = await getDesignMapData(props.designId)
    mapData.value = data
    showMap.value = true
  } catch (e: any) {
    console.warn('Failed to load map data:', e)
  }
}

function goAnalyze() {
  const seq = result.value?.optimized_sequence || result.value?.input_sequence || ''
  router.push({ path: '/analysis', query: seq ? { sequence: seq } : {} })
}

function goRedesign() {
  router.push('/design')
}

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
          <p v-else-if="result.status === 'completed'">设计 ID: {{ result.design_id }}</p>
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
          </div>
        </div>
        
        <!-- 查看图谱按钮 -->
            <div v-if="!showMap" class="result-section map-toggle-section">
              <button class="btn btn-secondary" @click="fetchMapData">
                🗺️ 查看质粒图谱
              </button>
            </div>

            <!-- 序列显示 -->
        <div v-if="result.optimized_sequence || result.input_sequence" class="result-section">
          <h2>优化后序列</h2>
      <div class="sequence-toolbar">
        <span class="seq-length">{{ (result.optimized_sequence || result.input_sequence).length }} bp</span>
        <button class="btn btn-secondary btn-small" @click="copySequence">复制序列</button>
      </div>
      <div class="sequence-display">
        {{ formatSequence(result.optimized_sequence || result.input_sequence) }}
      </div>
        </div>
        
        <!-- 质粒图谱 -->
      <div v-if="showMap && mapData" class="result-section">
        <h2>质粒图谱</h2>
        <div class="map-container">
          <PlasmidMap
            :name="mapData.name"
            :length="mapData.length"
            :sequence="mapData.sequence"
            :features="mapData.features"
            :enzyme-sites="mapData.enzyme_sites || []"
            :width="520"
            :height="520"
          />
        </div>
        <div v-if="mapData.sequence" class="seq-view-container">
          <SequenceView
            :sequence="mapData.sequence"
            :features="mapData.features"
            :enzyme-sites="mapData.enzyme_sites || []"
            :height="280"
          />
        </div>
      </div>

      <!-- Sanger 测序验证 -->
      <div v-if="showMap && result.status === 'completed'" class="result-section">
        <h2>Sanger 测序验证</h2>
        <SequencingPanel :reference-id="result.design_id" mode="design" />
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
            <tr v-for="primer in result.primers" :key="primer.name">
              <td class="name-cell">{{ primer.name }}</td>
              <td class="sequence-cell">{{ primer.full_sequence || primer.sequence }}</td>
              <td>{{ primer.length }} bp</td>
              <td>{{ primer.tm.toFixed(1) }}°C</td>
              <td>{{ primer.gc_content.toFixed(1) }}%</td>
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
              <li v-for="warn in result.warnings" :key="warn">{{ warn }}</li>
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

.map-container {
  display: flex;
  justify-content: center;
  padding: 1rem 0;
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
.seq-view-container {
  margin-top: 1rem;
  border: 1px solid var(--border-color, #eee);
  border-radius: 10px;
  padding: 0.5rem;
  background: #fff;
}
</style>
