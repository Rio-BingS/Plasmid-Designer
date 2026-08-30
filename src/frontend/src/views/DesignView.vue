<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import type { DesignRequest, VectorInfo, CodonTable } from '@/types'
import { submitDesign, getVectors, getCodonTables, getEnzymes } from '@/api'

const router = useRouter()

// 表单状态
const sequenceType = ref<'amino_acid' | 'dna'>('amino_acid')
const sequence = ref('')
const sequenceName = ref('insert')
const vectorId = ref('pET-28a')
const cloningMethod = ref<'gibson' | 'golden_gate' | 'restriction'>('gibson')
// 插入片段来源与克隆方法正交
const insertSource = ref<'pcr' | 'gene_synthesis'>('pcr')
const protocolLanguage = ref<'zh' | 'en'>('zh')

// 密码子优化
const optimizeCodons = ref(true)
const targetSpecies = ref('ecoli')
const gcMin = ref(40)
const gcMax = ref(60)

// 克隆参数
const homologyArm = ref(20)
const enzyme = ref('BsaI')
// 双酶切：5'/3' 端分别选择限制酶
const enzyme5 = ref('BamHI')
const enzyme3 = ref('EcoRI')
const oligoLength = ref(60)
const overlapLength = ref(20)

// 提交状态
// 动态选项
const availableVectors = ref<VectorInfo[]>([])
const codonTables = ref<CodonTable[]>([])
const availableEnzymes = ref<string[]>([])

const isSubmitting = ref(false)
const error = ref('')

// 提交设计
async function handleSubmit() {
  if (!sequence.value.trim()) {
    error.value = '请输入序列'
    return
  }
  if (cloningMethod.value === 'restriction' && enzyme5.value === enzyme3.value) {
    error.value = '双酶切请选择两种不同的限制酶'
    return
  }

  isSubmitting.value = true
  error.value = ''

  try {
    const request: DesignRequest = {
      sequence: sequence.value,
      sequence_type: sequenceType.value,
      sequence_name: sequenceName.value,
      vector_id: vectorId.value,
      cloning_method: cloningMethod.value,
      insert_source: insertSource.value,
      optimize_codons: optimizeCodons.value,
      target_species: targetSpecies.value,
      gc_min: gcMin.value,
      gc_max: gcMax.value,
      homology_arm: homologyArm.value,
      enzyme: enzyme.value,
      oligo_length: oligoLength.value,
      overlap_length: overlapLength.value,
      include_report: true,
      protocol_language: protocolLanguage.value
    }
    if (cloningMethod.value === 'restriction') {
      request.enzyme_5 = enzyme5.value
      request.enzyme_3 = enzyme3.value
    }

    const result = await submitDesign(request)
    router.push(`/result/${result.design_id}`)

  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message || '提交失败，请重试'
  } finally {
    isSubmitting.value = false
  }
}

// 示例序列
onMounted(async () => {
  try {
    availableVectors.value = await getVectors()
    if (availableVectors.value.length && !availableVectors.value.find(v => v.id === vectorId.value)) {
      vectorId.value = availableVectors.value[0].id
    }
  } catch (e) { console.warn('Failed to load vectors:', e) }

  try {
    codonTables.value = await getCodonTables()
    if (codonTables.value.length) {
      const match = codonTables.value.find(t =>
        (t.species || t.id || '').toLowerCase().includes(targetSpecies.value)
      )
      if (!match && codonTables.value[0]) {
        const s = (codonTables.value[0].species || codonTables.value[0].id || 'ecoli').toLowerCase()
        if (s.includes('human') || s.includes('homo')) targetSpecies.value = 'human'
        else if (s.includes('yeast') || s.includes('cerevisiae')) targetSpecies.value = 'yeast'
        else if (s.includes('cho')) targetSpecies.value = 'cho'
        else targetSpecies.value = 'ecoli'
      }
    }
  } catch (e) { console.warn('Failed to load codon tables:', e) }

  try {
    const enzymeData = await getEnzymes()
    availableEnzymes.value = Object.keys(enzymeData.enzymes || {})
    if (availableEnzymes.value.length && !availableEnzymes.value.includes(enzyme.value)) {
      enzyme.value = availableEnzymes.value[0]
    }
  } catch (e) { console.warn('Failed to load enzymes:', e) }
})

function loadExample() {
  sequenceType.value = 'amino_acid'
  sequence.value = 'MKVLWAALLTFLGCAATSGSQAPDRRNRLALASLLRLQGVSSVQIRCRDSDMNADADATIRR'
  sequenceName.value = 'example_protein'
}
</script>

<template>
  <div class="design-page">
    <h1>质粒设计</h1>
    <p class="subtitle">输入序列，自动生成质粒构建方案</p>
    
    <div class="design-form">
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
            {{ sequenceType === 'amino_acid' ? '氨基酸序列' : 'DNA序列' }}
          </label>
          <textarea 
            v-model="sequence"
            class="form-input sequence-input"
            :placeholder="sequenceType === 'amino_acid' ? '请输入氨基酸序列（单字母代码）' : '请输入DNA序列'"
            rows="6"
          ></textarea>
          <button @click="loadExample" class="btn btn-secondary example-btn">
            加载示例序列
          </button>
        </div>
        
        <div class="form-group">
          <label class="form-label">序列名称</label>
          <input v-model="sequenceName" class="form-input" placeholder="例如: GFP" />
        </div>
      </div>
      
      <!-- 载体选择 -->
      <div class="form-section">
        <h2>载体选择</h2>
        
        <div class="form-group">
          <label class="form-label">目标载体</label>
          <select v-model="vectorId" class="form-select">
            <option v-if="!availableVectors.length" value="pET-28a">pET-28a(+) - E.coli 表达载体</option>
            <option
              v-for="v in availableVectors"
              :key="v.id"
              :value="v.id"
            >
              {{ v.name }}{{ v.host?.length ? ` - ${v.host.join('/')}` : '' }}
            </option>
          </select>
        </div>
      </div>
      
      <!-- 插入片段来源 -->
      <div class="form-section">
        <h2>插入片段来源</h2>

        <div class="method-cards">
          <div
            class="method-card"
            :class="{ active: insertSource === 'pcr' }"
            @click="insertSource = 'pcr'"
          >
            <h3>PCR 引物设计</h3>
            <p>从模板扩增插入片段，引物带克隆末端</p>
          </div>

          <div
            class="method-card"
            :class="{ active: insertSource === 'gene_synthesis' }"
            @click="insertSource = 'gene_synthesis'"
          >
            <h3>全基因合成</h3>
            <p>重叠寡核苷酸拼装目标基因</p>
          </div>
        </div>

        <!-- 全基因合成参数 -->
        <div v-if="insertSource === 'gene_synthesis'" class="method-params">
          <div class="form-group">
            <label class="form-label">寡核苷酸长度 (bp)</label>
            <input v-model.number="oligoLength" type="number" class="form-input" min="40" max="100" />
          </div>
          <div class="form-group">
            <label class="form-label">重叠区域长度 (bp)</label>
            <input v-model.number="overlapLength" type="number" class="form-input" min="10" max="30" />
          </div>
        </div>
      </div>

      <!-- 克隆方法 -->
      <div class="form-section">
        <h2>克隆方法</h2>

    <div class="form-group" style="margin-bottom: 1rem;">
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

    <div class="method-cards">
          <div
            class="method-card"
            :class="{ active: cloningMethod === 'gibson' }"
            @click="cloningMethod = 'gibson'"
          >
            <h3>Gibson Assembly</h3>
            <p>无疤痕连接，适合多片段组装</p>
          </div>

          <div
            class="method-card"
            :class="{ active: cloningMethod === 'golden_gate' }"
            @click="cloningMethod = 'golden_gate'"
          >
            <h3>Golden Gate</h3>
            <p>标准化克隆，适合高通量</p>
          </div>

          <div
            class="method-card"
            :class="{ active: cloningMethod === 'restriction' }"
            @click="cloningMethod = 'restriction'"
          >
            <h3>限制性酶切</h3>
            <p>传统双酶切，定向克隆</p>
          </div>
        </div>

        <!-- Gibson 参数 -->
        <div v-if="cloningMethod === 'gibson'" class="method-params">
          <div class="form-group">
            <label class="form-label">同源臂长度 (bp)</label>
            <input v-model.number="homologyArm" type="number" class="form-input" min="15" max="40" />
          </div>
        </div>

        <!-- Golden Gate 参数 -->
        <div v-if="cloningMethod === 'golden_gate'" class="method-params">
          <div class="form-group">
            <label class="form-label">Type IIS 酶</label>
            <select v-model="enzyme" class="form-select">
              <option value="BsaI">BsaI</option>
              <option value="BsmBI">BsmBI</option>
              <option value="BbsI">BbsI</option>
            </select>
          </div>
        </div>

        <!-- 限制性酶切参数（双酶切） -->
        <div v-if="cloningMethod === 'restriction'" class="method-params">
          <div class="form-group">
            <label class="form-label">5' 端限制酶</label>
            <select v-model="enzyme5" class="form-select">
              <option
                v-for="enz in (availableEnzymes.length ? availableEnzymes : ['EcoRI', 'BamHI', 'HindIII', 'XhoI', 'NcoI', 'SalI'])"
                :key="'e5-' + enz"
                :value="enz"
              >{{ enz }}</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">3' 端限制酶</label>
            <select v-model="enzyme3" class="form-select">
              <option
                v-for="enz in (availableEnzymes.length ? availableEnzymes : ['EcoRI', 'BamHI', 'HindIII', 'XhoI', 'NcoI', 'SalI'])"
                :key="'e3-' + enz"
                :value="enz"
              >{{ enz }}</option>
            </select>
          </div>
          <p v-if="enzyme5 === enzyme3" class="hint-text warning-text">
            ⚠️ 双酶切请选择两种不同的限制酶
          </p>
        </div>
      </div>
      
      <!-- 密码子优化 -->
      <div v-if="sequenceType === 'amino_acid'" class="form-section">
        <h2>密码子优化</h2>
        
        <div class="form-group checkbox-group">
          <label class="checkbox-label">
            <input type="checkbox" v-model="optimizeCodons" />
            <span>启用密码子优化</span>
          </label>
        </div>
        
        <div v-if="optimizeCodons" class="optimize-params">
          <div class="form-group">
            <label class="form-label">目标物种</label>
            <select v-model="targetSpecies" class="form-select">
              <option value="ecoli">E. coli</option>
              <option value="human">Human</option>
              <option value="yeast">Yeast</option>
              <option value="cho">CHO</option>
            </select>
            <p v-if="codonTables.length" class="hint-text">
              可用密码子表: {{ codonTables.map(t => t.name || t.id).join(', ') }}
            </p>
          </div>
          
          <div class="form-group">
            <label class="form-label">GC含量范围: {{ gcMin }}% - {{ gcMax }}%</label>
            <div class="range-inputs">
              <input v-model.number="gcMin" type="range" min="20" max="50" />
              <input v-model.number="gcMax" type="range" min="50" max="80" />
            </div>
          </div>
        </div>
      </div>
      
      <!-- 错误提示 -->
      <div v-if="error" class="error-message">
        {{ error }}
      </div>
      
      <!-- 提交按钮 -->
      <div class="submit-section">
        <button 
          @click="handleSubmit" 
          class="btn btn-primary submit-btn"
          :disabled="isSubmitting"
        >
          {{ isSubmitting ? '处理中...' : '开始设计' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.design-page {
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

.design-form {
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
}

.example-btn {
  margin-top: 0.5rem;
  font-size: 0.875rem;
  padding: 0.5rem 1rem;
}

.method-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1rem;
  margin-bottom: 1rem;
}

.method-card {
  padding: 1rem;
  border: 2px solid var(--border-color);
  border-radius: 0.5rem;
  cursor: pointer;
  transition: all 0.2s;
}

.method-card:hover {
  border-color: var(--primary-color);
}

.method-card.active {
  border-color: var(--primary-color);
  background-color: rgba(79, 70, 229, 0.05);
}

.method-card h3 {
  font-size: 1rem;
  margin-bottom: 0.25rem;
}

.method-card p {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.method-params {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border-color);
}

.checkbox-group {
  margin-bottom: 0.5rem;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}

.optimize-params {
  margin-top: 1rem;
}

.range-inputs {
  display: flex;
  gap: 1rem;
}

.range-inputs input {
  flex: 1;
}

.error-message {
  color: var(--error-color);
  padding: 0.75rem;
  background-color: rgba(239, 68, 68, 0.1);
  border-radius: 0.5rem;
}

.submit-section {
  text-align: center;
  padding-top: 1rem;
}

.submit-btn {
  min-width: 200px;
  font-size: 1.125rem;
}

.language-toggle {
  display: inline-flex;
  border: 2px solid var(--border-color);
  border-radius: 0.5rem;
  overflow: hidden;
}

.hint-text {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-top: 0.35rem;
}

.warning-text {
  color: #b45309;
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
