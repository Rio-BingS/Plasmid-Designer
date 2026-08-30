<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import type { VectorInfo, CodonTable } from '@/types'
import { submitBatchDesign, getBatchProgress, downloadBatchResults, getBatchReport, getVectors, getCodonTables } from '@/api'

const router = useRouter()

const sequencesText = ref('')
const sequenceNames = ref<string[]>([])
const sequenceType = ref<'amino_acid' | 'dna'>('amino_acid')
const vectorId = ref('pET-28a')
const cloningMethod = ref<'gibson' | 'golden_gate' | 'restriction'>('gibson')
// 插入片段来源与克隆方法正交
const insertSource = ref<'pcr' | 'gene_synthesis'>('pcr')
const enzyme5 = ref('BamHI')
const enzyme3 = ref('EcoRI')
const protocolLanguage = ref<'zh' | 'en'>('zh')
const optimizeCodons = ref(true)
const targetSpecies = ref('ecoli')
const gcMin = ref(40)
const gcMax = ref(60)
const oligoLengthMin = ref(40)
const oligoLengthMax = ref(80)
const overlapLength = ref(20)
const isSubmitting = ref(false)
const availableVectors = ref<VectorInfo[]>([])
const codonTables = ref<CodonTable[]>([])
const batchId = ref('')
const progress = ref<any>(null)
const error = ref('')
const batchReport = ref<any>(null)

const parsedSequences = computed(() => {
  const lines = sequencesText.value.split(/\n---+\n|\n\s*\n/).filter(s => s.trim())
  return lines.map(s => s.trim())
})

const sequenceCount = computed(() => parsedSequences.value.length)

let pollInterval: number | null = null

async function startPolling(id: string) {
  batchId.value = id
  const poll = async () => {
    try {
      const data = await getBatchProgress(id)
      progress.value = data
      if (data.status === 'completed') {
        fetchBatchReport()
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null }
      }
    } catch (e: any) { console.error('Poll error:', e) }
  }
  poll()
  pollInterval = window.setInterval(poll, 2000)
}

async function handleSubmit() {
  if (parsedSequences.value.length === 0) { error.value = '请输入至少一个序列'; return }
  if (cloningMethod.value === 'restriction' && enzyme5.value === enzyme3.value) {
    error.value = '双酶切请选择两种不同的限制酶'; return
  }
  isSubmitting.value = true
  error.value = ''
  try {
    const result = await submitBatchDesign({
      sequences: parsedSequences.value,
      sequence_names: sequenceNames.value.length > 0 ? sequenceNames.value : undefined,
      sequence_type: sequenceType.value,
      vector_id: vectorId.value,
      cloning_method: cloningMethod.value,
      insert_source: insertSource.value,
      optimize_codons: optimizeCodons.value,
      target_species: targetSpecies.value,
      gc_min: gcMin.value,
      gc_max: gcMax.value,
      protocol_language: protocolLanguage.value,
      oligo_length_min: oligoLengthMin.value,
      oligo_length_max: oligoLengthMax.value,
      overlap_length: overlapLength.value,
      ...(cloningMethod.value === 'restriction' ? { enzyme_5: enzyme5.value, enzyme_3: enzyme3.value } : {})
    })
    startPolling(result.batch_id)
  } catch (e: any) { error.value = e.response?.data?.detail || e.message || '提交失败'; isSubmitting.value = false }
}

onMounted(async () => {
  try {
    availableVectors.value = await getVectors()
    if (availableVectors.value.length && !availableVectors.value.find(v => v.id === vectorId.value)) {
      vectorId.value = availableVectors.value[0].id
    }
  } catch (e) { console.warn('Failed to load vectors:', e) }
  try { codonTables.value = await getCodonTables() } catch (e) { console.warn('Failed to load codon tables:', e) }
})

function loadExamples() {
  sequencesText.value = [
    'MKVLWAALLTFLGCAATSGSQAPDRRNRLALASLLRLQGVSSVQIRCRDSDMNADADATIRR',
    'MGVSGAVPKLFVGKTLYFSVFCFVCCFQVVSLSYAYVTLPIAVMVVCTFQQSRQGAAKIF',
    'MNSLWNSTSSSFFRQIFQSIYLCLLGISSVLSQVYILSSQQNKVFQKAFYYSLLKTFQG',
  ].join('\n---\n')
  sequenceType.value = 'amino_acid'
}

function viewResult(designId: string) {
  router.push('/result/' + designId)
}

function getProgressColor(): string {
  if (!progress.value) return '#E0E0E0'
  if (progress.value.status === 'completed') return '#10B981'
  if (progress.value.failed > 0) return '#EF4444'
  return '#4F46E5'
}

async function handleDownloadBatch() {
  try { await downloadBatchResults(batchId.value) }
  catch (e: any) { error.value = '下载失败: ' + (e.message || '未知错误') }
}

async function fetchBatchReport() {
  try { batchReport.value = await getBatchReport(batchId.value) }
  catch (e) { console.warn('Failed to fetch batch report:', e) }
}

function resetBatch() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null }
  batchId.value = ''
  progress.value = null
  batchReport.value = null
  isSubmitting.value = false
  error.value = ''
}

onUnmounted(() => {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null }
})
</script>
<template>
  <div class="batch-design-page">
    <h1>批量设计</h1>
    <p class="subtitle">一次提交多个序列，批量生成质粒构建方案</p>
    
    <div class="batch-form">
      <!-- 序列输入 -->
      <div class="form-section">
        <h2>序列输入</h2>
        <div class="form-group">
          <label class="form-label">序列类型</label>
          <select v-model="sequenceType" class="form-select">
            <option value="amino_acid">氨基酸序列</option>
            <option value="dna">DNA序列</option>
          </select>
        </div>
        
        <div class="form-group">
          <label class="form-label">
            输入序列（用空行或 --- 分隔）
          </label>
          <textarea 
            v-model="sequencesText" 
            class="form-input sequence-input"
            placeholder="输入多个序列，每个序列用空行或 --- 分隔"
            rows="12"
          ></textarea>
          <div class="input-actions">
            <button @click="loadExamples" class="btn btn-secondary btn-small">
              加载示例
            </button>
            <span class="sequence-count">
              已输入 {{ sequenceCount }} 个序列
            </span>
          </div>
        </div>
      </div>
      
      <!-- 设计参数 -->
      <div class="form-section">
        <h2>公共参数</h2>
        <p class="hint">这些参数将应用于所有序列</p>
        
        <div class="params-grid">
          <div class="form-group">
            <label class="form-label">目标载体</label>
            <select v-model="vectorId" class="form-select">
              <option v-if="!availableVectors.length" value="pET-28a">pET-28a(+)</option>
              <option
                v-for="v in availableVectors"
                :key="v.id"
                :value="v.id"
              >{{ v.name }}</option>
            </select>
          </div>
          
          <div class="form-group">
            <label class="form-label">插入片段来源</label>
            <select v-model="insertSource" class="form-select">
              <option value="pcr">PCR 引物设计</option>
              <option value="gene_synthesis">全基因合成</option>
            </select>
          </div>

          <div class="form-group">
            <label class="form-label">克隆方法</label>
            <select v-model="cloningMethod" class="form-select">
              <option value="gibson">Gibson Assembly</option>
              <option value="golden_gate">Golden Gate</option>
              <option value="restriction">限制性酶切</option>
            </select>
          </div>

          <div class="form-group" v-if="cloningMethod === 'restriction'">
            <label class="form-label">5' 端限制酶</label>
            <select v-model="enzyme5" class="form-select">
              <option value="EcoRI">EcoRI</option>
              <option value="BamHI">BamHI</option>
              <option value="HindIII">HindIII</option>
              <option value="XhoI">XhoI</option>
              <option value="NcoI">NcoI</option>
              <option value="SalI">SalI</option>
            </select>
          </div>

          <div class="form-group" v-if="cloningMethod === 'restriction'">
            <label class="form-label">3' 端限制酶</label>
            <select v-model="enzyme3" class="form-select">
              <option value="EcoRI">EcoRI</option>
              <option value="BamHI">BamHI</option>
              <option value="HindIII">HindIII</option>
              <option value="XhoI">XhoI</option>
              <option value="NcoI">NcoI</option>
              <option value="SalI">SalI</option>
            </select>
          </div>

      <div class="form-group">
        <label class="form-label">实验方案语言</label>
        <div class="language-toggle">
          <button
            class="toggle-btn"
            :class="{ active: protocolLanguage === 'zh' }"
            @click="protocolLanguage = 'zh'"
          >中文</button>
          <button
            class="toggle-btn"
            :class="{ active: protocolLanguage === 'en' }"
            @click="protocolLanguage = 'en'"
          >English</button>
        </div>
      </div>

          <div class="form-group" v-if="sequenceType === 'amino_acid'">
            <label class="form-label">目标物种</label>
            <select v-model="targetSpecies" class="form-select">
              <option value="ecoli">E. coli</option>
              <option value="human">Human</option>
              <option value="yeast">Yeast</option>
              <option value="cho">CHO</option>
            </select>
          </div>

          <div class="form-group" v-if="sequenceType === 'amino_acid'">
            <label class="checkbox-label">
              <input type="checkbox" v-model="optimizeCodons" />
              <span>启用密码子优化</span>
            </label>
          </div>
        </div>

      <!-- 全基因合成参数 -->
      <div v-if="insertSource === 'gene_synthesis'" class="synth-params">
        <div class="form-group">
          <label class="form-label">寡核苷酸长度范围 (bp)</label>
          <div class="range-inputs">
            <input v-model.number="oligoLengthMin" type="number" class="form-input" min="20" max="100" placeholder="最短" />
            <span>—</span>
            <input v-model.number="oligoLengthMax" type="number" class="form-input" min="30" max="120" placeholder="最长" />
          </div>
          <p class="hint-text">算法会在该范围内均衡分片，每片同时给出正链与反义链</p>
        </div>
        <div class="form-group">
          <label class="form-label">重叠区域长度 (bp)</label>
          <input v-model.number="overlapLength" type="number" class="form-input" min="10" max="30" />
        </div>
      </div>
      </div>
      
      <!-- 提交 -->
      <div v-if="!batchId" class="submit-section">
        <div v-if="error" class="error-message">{{ error }}</div>
        <button 
          @click="handleSubmit" 
          class="btn btn-primary submit-btn"
          :disabled="isSubmitting || sequenceCount === 0"
        >
          {{ isSubmitting ? '提交中...' : `开始批量设计 (${sequenceCount} 个序列)` }}
        </button>
      </div>
      
      <!-- 进度显示 -->
      <div v-if="progress" class="progress-section">
        <h2>设计进度</h2>
        
        <div class="progress-bar-container">
          <div class="progress-info">
            <span>已完成: {{ progress.completed }} / {{ progress.total }}</span>
            <span v-if="progress.failed > 0" class="failed-count">
              失败: {{ progress.failed }}
            </span>
          </div>
          <div class="progress-bar">
            <div 
              class="progress-fill" 
              :style="{ 
                width: progress.progress_percent + '%',
                backgroundColor: getProgressColor()
              }"
            ></div>
          </div>
          <div class="progress-percent">
            {{ progress.progress_percent.toFixed(1) }}%
          </div>
        </div>
        
        <!-- 结果列表 -->
        <div v-if="progress.results?.length" class="results-list">
          <h3>已完成</h3>
          <div class="result-cards">
            <div 
              v-for="result in progress.results" 
              :key="result.design_id"
              class="result-card"
              @click="viewResult(result.design_id)"
            >
              <div class="result-header">
                <span class="result-id">{{ result.design_id }}</span>
                <span class="result-status" :class="result.status">
                  {{ result.status }}
                </span>
              </div>
              <div class="result-metrics">
                <span v-if="result.cai">CAI: {{ result.cai.toFixed(3) }}</span>
                <span v-if="result.gc_content">GC: {{ result.gc_content.toFixed(1) }}%</span>
              </div>
            </div>
          </div>
        </div>
        
        <!-- 错误列表 -->
        <div v-if="progress.errors?.length" class="errors-list">
          <h3>错误</h3>
          <div v-for="(err, i) in progress.errors" :key="i" class="error-item">
            <strong>{{ err.sequence_name }}:</strong> {{ err.error }}
          </div>
        </div>
        
        <!-- 汇总报告 -->
          <div v-if="batchReport" class="report-section">
            <h3>汇总报告</h3>
            <div class="report-stats">
              <span>成功率: {{ batchReport.summary.success_rate.toFixed(1) }}%</span>
              <span>完成: {{ batchReport.summary.completed }} / {{ batchReport.summary.total }}</span>
              <span v-if="batchReport.summary.failed > 0">失败: {{ batchReport.summary.failed }}</span>
            </div>
          </div>

          <!-- 完成操作 -->
        <div v-if="progress.status === 'completed'" class="completion-actions">
          <button class="btn btn-primary" @click="router.push('/')">
            返回首页
          </button>
          <button class="btn btn-secondary" @click="handleDownloadBatch">
            下载所有结果 (ZIP)
          </button>
          <button class="btn btn-secondary" @click="resetBatch">
            新建批量任务
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.batch-design-page {
  max-width: 900px;
  margin: 0 auto;
}

h1 {
  font-size: 2rem;
  margin-bottom: 0.5rem;
}

.subtitle {
  color: var(--text-secondary);
  margin-bottom: 2rem;
}

.batch-form {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.form-section {
  background: white;
  padding: 1.5rem;
  border-radius: 0.75rem;
  box-shadow: var(--shadow);
}

.form-section h2 {
  font-size: 1.125rem;
  margin-bottom: 1rem;
  color: var(--primary-color);
}

.sequence-input {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.875rem;
  resize: vertical;
}

.input-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-top: 0.5rem;
}

.sequence-count {
  color: var(--text-secondary);
  font-size: 0.875rem;
}

.hint {
  font-size: 0.875rem;
  color: var(--text-secondary);
  margin-bottom: 1rem;
}

.params-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}

.submit-section {
  text-align: center;
  padding: 1rem 0;
}

.submit-btn {
  min-width: 250px;
  font-size: 1rem;
}

.error-message {
  color: var(--error-color);
  padding: 0.75rem;
  background: rgba(239, 68, 68, 0.1);
  border-radius: 0.5rem;
  margin-bottom: 1rem;
}

.progress-section {
  background: white;
  padding: 1.5rem;
  border-radius: 0.75rem;
  box-shadow: var(--shadow);
}

.progress-section h2 {
  font-size: 1.125rem;
  margin-bottom: 1.5rem;
}

.progress-bar-container {
  margin-bottom: 2rem;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
  font-size: 0.875rem;
}

.failed-count {
  color: var(--error-color);
}

.progress-bar {
  height: 12px;
  background: #E5E7EB;
  border-radius: 6px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  transition: width 0.3s ease;
}

.progress-percent {
  text-align: center;
  margin-top: 0.5rem;
  font-weight: 600;
  color: var(--primary-color);
}

.results-list h3,
.errors-list h3 {
  font-size: 1rem;
  margin-bottom: 1rem;
}

.result-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1rem;
}

.result-card {
  padding: 1rem;
  background: var(--bg-secondary);
  border-radius: 0.5rem;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
}

.result-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
}

.result-id {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.result-status {
  font-size: 0.75rem;
  padding: 2px 6px;
  border-radius: 4px;
  background: #10B981;
  color: white;
}

.result-metrics {
  display: flex;
  gap: 1rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.error-item {
  padding: 0.75rem;
  background: rgba(239, 68, 68, 0.1);
  border-radius: 0.5rem;
  margin-bottom: 0.5rem;
  font-size: 0.875rem;
}

.report-section {
  margin-bottom: 1.5rem;
}

.report-section h3 {
  font-size: 1rem;
  margin-bottom: 0.75rem;
}

.report-stats {
  display: flex;
  gap: 1.5rem;
  font-size: 0.875rem;
  color: var(--text-secondary);
}

.completion-actions {
  display: flex;
  gap: 1rem;
  justify-content: center;
  margin-top: 1.5rem;
}

.language-toggle {
  display: inline-flex;
  border: 2px solid var(--border-color);
  border-radius: 0.5rem;
  overflow: hidden;
}

.toggle-btn {
  padding: 0.4rem 1rem;
  border: none;
  background: white;
  cursor: pointer;
  font-size: 0.875rem;
  transition: all 0.2s;
}

.toggle-btn:not(:last-child) {
  border-right: 1px solid var(--border-color);
}

.toggle-btn.active {
  background: var(--primary-color);
  color: white;
}

.toggle-btn:hover:not(.active) {
  background: var(--bg-secondary);
}
</style>
