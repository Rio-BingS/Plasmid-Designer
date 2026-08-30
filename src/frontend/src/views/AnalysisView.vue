<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { analyzeSequence, findRestrictionSites, predictORFs, analyzeGC, exportSequence, getExportFormats, exportAllFormats, checkCompatibility, getEnzymes, simulateDigest } from '@/api'
import EnzymeAutocomplete from '@/components/EnzymeAutocomplete.vue'

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
const availableAnalysisEnzymeMap = ref<Record<string, { recognition_sequence?: string }>>({})
// 限制性位点：酶筛选（空 = 全部酶）
const selectedRestrictionEnzymes = ref<string[]>([])
// 酶切消化模拟
const digestEnzymes = ref<string[]>([])
const digestResult = ref<any>(null)
const digestLoading = ref(false)
// ORF
const orfMinLength = ref(100)

const isAminoAcid = computed(() => sequenceType.value === 'amino_acid')

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
    availableAnalysisEnzymeMap.value = enzymeData.enzymes || {}
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
  if (!sequence.value.trim() || isAminoAcid.value) return
  try {
    loading.value = true
    restrictionResult.value = await findRestrictionSites(
      sequence.value,
      selectedRestrictionEnzymes.value.length ? selectedRestrictionEnzymes.value : undefined
    )
    activeSection.value = 'restriction'
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function runORFPrediction() {
  if (!sequence.value.trim() || isAminoAcid.value) return
  try {
    loading.value = true
    orfResult.value = await predictORFs(sequence.value, orfMinLength.value)
    activeSection.value = 'orf'
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function runDigest() {
  if (!sequence.value.trim() || isAminoAcid.value || !digestEnzymes.value.length) return
  try {
    digestLoading.value = true
    digestResult.value = await simulateDigest(sequence.value, digestEnzymes.value)
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    digestLoading.value = false
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

// 回文识别位点正反链各记一条，按 (酶, 位置) 去重避免表格重复行
function dedupeSites(sites: any[]): any[] {
  const seen = new Set<string>()
  return (sites || []).filter(s => {
    const key = `${s.enzyme}|${s.position}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
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
        <button class="btn btn-secondary" :disabled="!sequence.trim() || isAminoAcid" title="仅对 DNA 序列可用" @click="runRestrictionAnalysis">✂️ 限制性位点</button>
        <button class="btn btn-secondary" :disabled="!sequence.trim() || isAminoAcid" title="仅对 DNA 序列可用" @click="runORFPrediction">🧬 ORF 预测</button>
        <button class="btn btn-secondary" :disabled="!sequence.trim()" @click="runGCAnalysis">📊 GC 分析</button>
      </div>
      <p v-if="isAminoAcid" class="hint-text warning-text">
        ⚠️ 限制性位点与 ORF 预测仅对 DNA 序列有意义，请将序列类型切换为 DNA
      </p>
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
        <!-- 后端返回的 gc_content 已是百分数（如 47.71），不要乘 100 -->
        <div class="stat-card">
          <span class="stat-value">{{ result.gc_content != null ? Number(result.gc_content).toFixed(1) + '%' : '-' }}</span>
          <span class="stat-label">GC 含量</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ result.coding_potential != null ? Number(result.coding_potential).toFixed(1) : '-' }}</span>
          <span class="stat-label">编码潜力 (%)</span>
        </div>
        <!-- 后端无 orf_count 字段，取 orfs 列表长度 -->
        <div class="stat-card">
          <span class="stat-value">{{ result.orfs?.length ?? '-' }}</span>
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

      <div class="digest-filter">
        <label class="digest-label">筛选酶（留空 = 全部常用酶）</label>
        <EnzymeAutocomplete
          v-model="selectedRestrictionEnzymes"
          :enzymes="availableAnalysisEnzymeMap"
          multiple
          placeholder="输入以搜索要分析的酶…"
        />
      </div>

      <!-- 单一位点酶（适合克隆） -->
      <div v-if="restrictionResult.unique_sites?.length" class="unique-sites">
        <span class="unique-label">单一位点酶（适合克隆）：</span>
        <span v-for="u in restrictionResult.unique_sites" :key="u" class="unique-chip">{{ u }}</span>
      </div>

      <div v-if="restrictionResult.sites?.length">
        <table class="data-table">
          <thead>
            <tr><th>酶</th><th>识别序列</th><th>位置</th><th>末端</th></tr>
          </thead>
          <tbody>
            <!-- 后端字段：recognition_sequence / position / overhang_type -->
            <tr v-for="site in dedupeSites(restrictionResult.sites)" :key="site.enzyme + site.position">
              <td>{{ site.enzyme }}</td>
              <td class="mono">{{ site.recognition_sequence }}</td>
              <td>{{ site.position }} - {{ site.end }}</td>
              <td>{{ { '5': "5' 粘性末端", '3': "3' 粘性末端", 'b': '平末端' }[site.overhang_type] || '未知' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty-text">未检测到限制性位点</p>

      <!-- 酶切消化模拟 -->
      <div class="digest-sim">
        <h3>酶切消化模拟</h3>
        <div class="digest-filter">
          <label class="digest-label">消化酶（1-6 个）</label>
          <EnzymeAutocomplete
            v-model="digestEnzymes"
            :enzymes="availableAnalysisEnzymeMap"
            multiple
            placeholder="输入以搜索消化用酶…"
          />
        </div>
        <button
          class="btn btn-secondary"
          :disabled="digestLoading || !digestEnzymes.length || isAminoAcid"
          @click="runDigest"
        >{{ digestLoading ? '模拟中...' : '🧪 模拟完全消化' }}</button>

        <div v-if="digestResult?.fragments?.length" style="margin-top: 0.75rem; overflow-x: auto;">
          <table class="data-table">
            <thead>
              <tr><th>片段</th><th>起</th><th>止</th><th>大小 (bp)</th><th>切割酶</th></tr>
            </thead>
            <tbody>
              <tr v-for="(f, i) in digestResult.fragments" :key="i">
                <td>#{{ i + 1 }}</td>
                <td>{{ f.start }}</td>
                <td>{{ f.end }}</td>
                <td><strong>{{ f.size }}</strong></td>
                <td>{{ f.cut_by?.join(', ') || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p class="hint-text">片段按线性完全消化近似（粘性末端差异未计入）；各片段大小可用于预测电泳条带</p>
        </div>
        <p v-else-if="digestResult" class="empty-text">{{ digestResult.message || '未产生片段' }}</p>
      </div>
    </div>

    <!-- ORF 结果 -->
    <div v-if="orfResult" class="results-section">
      <h2>ORF 预测</h2>
      <p class="hint-text" style="margin-bottom: 0.75rem;">
        提示：ORF 数为 0 时可尝试调低最小长度（当前 {{ orfMinLength }} bp）
      </p>
      <div v-if="orfResult.orfs?.length">
        <table class="data-table">
          <thead>
            <tr><th>起始</th><th>终止</th><th>长度</th><th>起始密码子</th><th>终止密码子</th><th>方向</th><th>完整</th><th>蛋白序列</th></tr>
          </thead>
          <tbody>
            <tr v-for="orf in orfResult.orfs" :key="orf.start + '-' + orf.end">
              <td>{{ orf.start }}</td>
              <td>{{ orf.end }}</td>
              <td>{{ orf.length }} bp</td>
              <td>{{ orf.start_codon }}</td>
              <td>{{ orf.stop_codon || '—' }}</td>
              <td>{{ orf.strand === '-' ? '反向链' : '正向链' }}</td>
              <td>{{ orf.is_complete ? '✓' : '—' }}</td>
              <td class="mono orf-protein" :title="orf.protein_sequence">
                {{ orf.protein_sequence?.slice(0, 40) }}{{ (orf.protein_sequence?.length || 0) > 40 ? '…' : '' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty-text">未预测到 ORF</p>
    </div>

    <!-- GC 结果 -->
    <div v-if="gcResult" class="results-section">
      <h2>GC 分析</h2>
      <!-- 后端结构：total_gc_content(百分数) / total_regions / extreme_regions / distribution[] -->
      <div class="result-cards">
        <div class="stat-card">
          <span class="stat-value">{{ gcResult.total_gc_content != null ? Number(gcResult.total_gc_content).toFixed(1) + '%' : '-' }}</span>
          <span class="stat-label">总体 GC</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ gcResult.total_regions ?? '-' }}</span>
          <span class="stat-label">分析窗口</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ gcResult.extreme_regions ?? '-' }}</span>
          <span class="stat-label">GC 异常区域</span>
        </div>
      </div>
      <div v-if="gcResult.distribution?.length" style="margin-top: 1rem; overflow-x: auto;">
        <table class="data-table">
          <thead>
            <tr><th>窗口位置</th><th>GC 含量</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="w in gcResult.distribution" :key="w.start">
              <td>{{ w.start }} - {{ w.end }}</td>
              <td>{{ Number(w.gc_content).toFixed(1) }}%</td>
              <td>{{ w.is_extreme ? '⚠️ 偏离' : '正常' }}</td>
            </tr>
          </tbody>
        </table>
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
.orf-protein { max-width: 260px; word-break: break-all; }
.digest-filter { margin-bottom: 0.6rem; }
.digest-label { display: block; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.3rem; }
.digest-sim { margin-top: 1.25rem; padding-top: 1rem; border-top: 1px dashed var(--border-color); }
.digest-sim h3 { font-size: 1rem; margin-bottom: 0.6rem; }
.unique-sites { margin: 0.5rem 0 0.75rem; display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; }
.unique-label { font-size: 0.8rem; color: var(--text-secondary); }
.unique-chip {
  padding: 0.12rem 0.5rem;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #065f46;
  border-radius: 9999px;
  font-size: 0.75rem;
}
.warning-text { color: #b45309; }
.empty-text { color: var(--text-secondary); font-size: 0.875rem; }
.export-section { background: white; border-radius: 12px; padding: 1.5rem; box-shadow: var(--shadow); }
.export-row { display: flex; gap: 0.75rem; align-items: center; }
.error-msg { padding: 0.75rem 1rem; background: #fee2e2; color: #991b1b; border-radius: 8px; margin-bottom: 1rem; font-size: 0.875rem; }
.compatibility-section { background: white; border-radius: 12px; padding: 1.5rem; box-shadow: var(--shadow); margin-bottom: 1.5rem; }
.compat-form { margin-top: 1rem; }
.compat-result { margin-top: 1rem; padding: 1rem; background: var(--bg-secondary); border-radius: 8px; overflow-x: auto; }
.compat-result pre { font-size: 0.8rem; margin: 0; white-space: pre-wrap; }
</style>
