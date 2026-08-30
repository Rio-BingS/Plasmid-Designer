<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { analyzeSequence, findRestrictionSites, predictORFs, analyzeGC, exportSequence, getExportFormats, exportAllFormats, checkCompatibility, getEnzymes } from '@/api'

const route = useRoute()
const sequence = ref('')
const sequenceType = ref('dna')
const loading = ref(false)
const result = ref<any>(null)
const error = ref('')
const activeSection = ref<'overview' | 'restriction' | 'orf' | 'gc'>('overview')

// 限制性位点
const restrictionResult = ref<any>(null)

// ORF
const orfResult = ref<any>(null)

// GC
const gcResult = ref<any>(null)

// 导出格式
const exportFormats = ref<any[]>([])
const compatibilitySeq1 = ref('')
const compatibilitySeq2 = ref('')
const compatibilityEnzymes = ref('')
const compatibilityResult = ref<any>(null)
const compatibilityLoading = ref(false)
const availableAnalysisEnzymes = ref<string[]>([])

const exportFormat = ref('genbank')
const exportLoading = ref(false)

onMounted(async () => {
  const q = route.query.sequence
  if (typeof q === 'string' && q.trim()) {
    sequence.value = q.trim()
    sequenceType.value = 'dna'
  }
  try { exportFormats.value = await getExportFormats() } catch (e) { /* fallback to hardcoded */ }
  try {
    const enzymeData = await getEnzymes()
    availableAnalysisEnzymes.value = Object.keys(enzymeData.enzymes || {})
  } catch (e) { /* fallback */ }
})

async function runAnalysis() {
  if (!sequence.value.trim()) return
  try {
    loading.value = true
    error.value = ''
    result.value = await analyzeSequence(sequence.value, sequenceType.value)
    activeSection.value = 'overview'
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function runRestrictionAnalysis() {
  if (!sequence.value.trim()) return
  try {
    loading.value = true
    restrictionResult.value = await findRestrictionSites(sequence.value)
    activeSection.value = 'restriction'
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function runORFPrediction() {
  if (!sequence.value.trim()) return
  try {
    loading.value = true
    orfResult.value = await predictORFs(sequence.value)
    activeSection.value = 'orf'
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function runGCAnalysis() {
  if (!sequence.value.trim()) return
  try {
    loading.value = true
    gcResult.value = await analyzeGC(sequence.value)
    activeSection.value = 'gc'
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function handleCompatibilityCheck() {
  if (!compatibilitySeq1.value.trim() || !compatibilitySeq2.value.trim()) return
  try {
    compatibilityLoading.value = true
    const enzymes = compatibilityEnzymes.value.split(',').map(s => s.trim()).filter(Boolean)
    compatibilityResult.value = await checkCompatibility(compatibilitySeq1.value, compatibilitySeq2.value, enzymes)
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    compatibilityLoading.value = false
  }
}

async function handleExport() {
  if (!sequence.value.trim()) return
  try {
    exportLoading.value = true
    await exportSequence(sequence.value, [], exportFormat.value, 'analysis_result')
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    exportLoading.value = false
  }
}
</script>

<template>
  <div class="analysis-page">
    <h1>序列分析</h1>
    <p class="subtitle">限制性位点、ORF 预测、GC 分析和多格式导出</p>

    <div class="input-section">
      <div class="input-header">
        <select v-model="sequenceType" class="form-select small">
          <option value="dna">DNA</option>
          <option value="amino_acid">氨基酸</option>
        </select>
        <span class="seq-length" v-if="sequence">{{ sequence.replace(/\s/g, '').length }} bp</span>
      </div>
      <textarea
        v-model="sequence"
        placeholder="粘贴 DNA 或氨基酸序列..."
        rows="6"
        class="form-textarea"
      ></textarea>

      <div class="action-buttons">
        <button class="btn btn-primary" :disabled="!sequence.trim() || loading" @click="runAnalysis">
          {{ loading ? '分析中...' : '🔍 综合分析' }}
        </button>
        <button class="btn btn-secondary" :disabled="!sequence.trim()" @click="runRestrictionAnalysis">✂️ 限制性位点</button>
        <button class="btn btn-secondary" :disabled="!sequence.trim()" @click="runORFPrediction">🧬 ORF 预测</button>
        <button class="btn btn-secondary" :disabled="!sequence.trim()" @click="runGCAnalysis">📊 GC 分析</button>
      </div>
    </div>

    <div v-if="error" class="error-msg">{{ error }}</div>

    <!-- 综合分析结果 -->
    <div v-if="result" class="results-section">
      <h2>分析结果</h2>
      <div class="result-cards">
        <div class="stat-card">
          <span class="stat-value">{{ result.sequence_length?.toLocaleString() || '-' }}</span>
          <span class="stat-label">序列长度 (bp)</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ result.gc_content != null ? (result.gc_content * 100).toFixed(1) + '%' : '-' }}</span>
          <span class="stat-label">GC 含量</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ result.orf_count ?? '-' }}</span>
          <span class="stat-label">ORF 数量</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ result.restriction_sites?.length ?? '-' }}</span>
          <span class="stat-label">限制性位点</span>
        </div>
      </div>

      <div v-if="result.warnings?.length" class="warnings">
        <p v-for="w in result.warnings" :key="w" class="warning-item">⚠️ {{ w }}</p>
      </div>
    </div>

    <!-- 限制性位点结果 -->
    <div v-if="restrictionResult" class="results-section">
      <h2>限制性位点</h2>
      <div v-if="restrictionResult.sites?.length">
        <table class="data-table">
          <thead>
            <tr><th>酶</th><th>序列</th><th>位置</th></tr>
          </thead>
          <tbody>
            <tr v-for="site in restrictionResult.sites" :key="site.enzyme + site.position">
              <td>{{ site.enzyme }}</td>
              <td class="mono">{{ site.sequence }}</td>
              <td>{{ site.position }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty-text">未检测到限制性位点</p>
    </div>

    <!-- ORF 结果 -->
    <div v-if="orfResult" class="results-section">
      <h2>ORF 预测</h2>
      <div v-if="orfResult.orfs?.length">
        <table class="data-table">
          <thead>
            <tr><th>起始</th><th>终止</th><th>长度</th><th>起始密码子</th><th>终止密码子</th><th>框</th></tr>
          </thead>
          <tbody>
            <tr v-for="orf in orfResult.orfs" :key="orf.start + '-' + orf.end">
              <td>{{ orf.start }}</td>
              <td>{{ orf.end }}</td>
              <td>{{ orf.length }} bp</td>
              <td>{{ orf.start_codon }}</td>
              <td>{{ orf.stop_codon }}</td>
              <td>{{ orf.frame }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty-text">未预测到 ORF</p>
    </div>

    <!-- GC 结果 -->
    <div v-if="gcResult" class="results-section">
      <h2>GC 分析</h2>
      <div class="result-cards">
        <div class="stat-card">
          <span class="stat-value">{{ gcResult.gc_content != null ? (gcResult.gc_content * 100).toFixed(1) + '%' : '-' }}</span>
          <span class="stat-label">总体 GC</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ gcResult.gc_min != null ? (gcResult.gc_min * 100).toFixed(1) + '%' : '-' }}</span>
          <span class="stat-label">最低 GC</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ gcResult.gc_max != null ? (gcResult.gc_max * 100).toFixed(1) + '%' : '-' }}</span>
          <span class="stat-label">最高 GC</span>
        </div>
      </div>
    </div>

    <!-- 导出 -->
    <!-- 兼容性检查 -->
<div v-if="sequence.trim()" class="compatibility-section">
  <h2>克隆兼容性检查</h2>
  <div class="compat-form">
    <div class="form-group">
      <label>插入片段序列</label>
      <textarea v-model="compatibilitySeq1" class="form-textarea" rows="2" placeholder="插入片段 DNA 序列..."></textarea>
    </div>
    <div class="form-group">
      <label>载体序列</label>
      <textarea v-model="compatibilitySeq2" class="form-textarea" rows="2" placeholder="载体 DNA 序列..."></textarea>
    </div>
    <div class="form-group">
      <label>酶列表 (逗号分隔)</label>
      <input v-model="compatibilityEnzymes" class="form-input" placeholder="例如: EcoRI, BamHI, HindIII" />
    </div>
    <button class="btn btn-secondary" :disabled="compatibilityLoading" @click="handleCompatibilityCheck">
      {{ compatibilityLoading ? '检查中...' : '🔬 检查兼容性' }}
    </button>
  </div>
  <div v-if="compatibilityResult" class="compat-result">
    <pre>{{ JSON.stringify(compatibilityResult, null, 2) }}</pre>
  </div>
</div>

<div v-if="sequence.trim()" class="export-section">
      <h2>导出序列</h2>
      <div class="export-row">
        <select v-model="exportFormat" class="form-select small">
          <option value="genbank">GenBank (.gb)</option>
          <option value="fasta">FASTA (.fasta)</option>
          <option value="snapgene">SnapGene (.dna)</option>
          <option value="benchling">Benchling (.json)</option>
          <option value="sbol">SBOL (.json)</option>
        </select>
        <button class="btn btn-primary" :disabled="exportLoading" @click="handleExport">
          {{ exportLoading ? '导出中...' : '📥 导出' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.analysis-page {
  max-width: 900px;
  margin: 0 auto;
}
h1 { font-size: 2rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.25rem; margin-bottom: 1rem; }
.subtitle { color: var(--text-secondary); margin-bottom: 2rem; }
.input-section { margin-bottom: 2rem; }
.input-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }
.form-textarea {
  width: 100%; padding: 0.75rem; border: 1px solid var(--border-color);
  border-radius: 8px; font-family: monospace; font-size: 0.875rem; resize: vertical;
}
.form-select.small { width: auto; min-width: 120px; }
.seq-length { font-size: 0.875rem; color: var(--text-secondary); }
.action-buttons { display: flex; gap: 0.75rem; margin-top: 1rem; flex-wrap: wrap; }
.results-section { background: white; border-radius: 12px; padding: 1.5rem; box-shadow: var(--shadow); margin-bottom: 1.5rem; }
.result-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 1rem; }
.stat-card {
  text-align: center; padding: 1rem; background: var(--bg-secondary);
  border-radius: 8px;
}
.stat-value { display: block; font-size: 1.5rem; font-weight: 700; color: var(--primary-color); }
.stat-label { display: block; font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem; }
.warnings { margin-top: 1rem; }
.warning-item { padding: 0.5rem 0.75rem; background: #fef3c7; border-radius: 6px; font-size: 0.875rem; margin-bottom: 0.5rem; }
.data-table { width: 100%; border-collapse: collapse; }
.data-table th, .data-table td { padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border-color); font-size: 0.875rem; }
.data-table th { background: var(--bg-secondary); font-weight: 600; }
.mono { font-family: monospace; }
.empty-text { color: var(--text-secondary); font-size: 0.875rem; }
.export-section { background: white; border-radius: 12px; padding: 1.5rem; box-shadow: var(--shadow); }
.export-row { display: flex; gap: 0.75rem; align-items: center; }
.error-msg { padding: 0.75rem 1rem; background: #fee2e2; color: #991b1b; border-radius: 8px; margin-bottom: 1rem; font-size: 0.875rem; }
.compatibility-section { background: white; border-radius: 12px; padding: 1.5rem; box-shadow: var(--shadow); margin-bottom: 1.5rem; }
.compat-form { margin-top: 1rem; }
.compat-result { margin-top: 1rem; padding: 1rem; background: var(--bg-secondary); border-radius: 8px; overflow-x: auto; }
.compat-result pre { font-size: 0.8rem; margin: 0; white-space: pre-wrap; }
</style>
