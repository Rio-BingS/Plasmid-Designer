<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import type { VectorInfo, CodonTable, DesignVectorPayload } from '@/types'
import { submitBatchDesign, getBatchProgress, downloadBatchResults, downloadBatchPrimers, getBatchReport, getVectors, getCodonTables, getEnzymes, formatApiError } from '@/api'
import EnzymeAutocomplete from '@/components/EnzymeAutocomplete.vue'
import DesignVectorPicker from '@/components/DesignVectorPicker.vue'
import { parseBatchInput, validateParsed, type ParsedSequence } from '@/utils/fastaParse'
import { loadBatchHistory, recordBatch, type BatchHistoryEntry } from '@/utils/batchHistory'

const router = useRouter()

// ==================== 序列输入 ====================
const sequencesText = ref('')
const sequenceType = ref<'amino_acid' | 'dna'>('amino_acid')
const fileInput = ref<HTMLInputElement | null>(null)
/** 预览表里用户改过的名称（index → name），覆盖解析结果 */
const nameOverrides = ref<Record<number, string>>({})

const parsedSequences = computed<ParsedSequence[]>(() => {
  const rows = parseBatchInput(sequencesText.value)
  return rows.map((r, i) => ({
    ...r,
    name: nameOverrides.value[i]?.trim() || r.name,
  }))
})
const sequenceCount = computed(() => parsedSequences.value.length)
const invalidRows = computed(() => parsedSequences.value
  .map((r, i) => (validateParsed(r.sequence, sequenceType.value) ? -1 : i))
  .filter((i) => i >= 0))

function onNameEdit(i: number, ev: Event) {
  nameOverrides.value[i] = (ev.target as HTMLInputElement).value
}

function pickFile() { fileInput.value?.click() }

async function onFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    // 与粘贴内容合并（文件内容在前），用户可在预览表里删改
    sequencesText.value = (sequencesText.value ? sequencesText.value.trimEnd() + '\n\n' : '') + text
  } catch (e) {
    error.value = '文件读取失败：' + (e instanceof Error ? e.message : '未知错误')
  }
  input.value = ''
}

function removeRow(i: number) {
  // 从源文本删掉第 i 条记录：按解析结果重拼不现实（会丢失原始格式），
  // 直接把该行标记为无效太绕——最直接的做法是重置 override 并提示用户
  // 在文本框里删除该段。为保持简单，这里从解析结果层面剔除：
  // 用 override 表做不到（只改名字），所以改为重写文本框：
  const rows = parseBatchInput(sequencesText.value)
  const target = rows[i]
  if (!target) return
  // 把第 i 条记录的文本从源里删掉：定位原始块很脆弱，退而求其次——
  // 用剩余记录重建文本（FASTA 形式，名称保留）
  const remaining = rows.filter((_, idx) => idx !== i)
  sequencesText.value = remaining
    .map((r) => `>${r.name}\n${r.sequence}`)
    .join('\n\n')
  nameOverrides.value = {}
}

function loadExamples() {
  sequencesText.value = [
    '>A01-1\nMKVLWAALLTFLGCAATSGSQAPDRRNRLALASLLRLQGVSSVQIRCRDSDMNADADATIRR',
    '>A01-2\nMGVSGAVPKLFVGKTLYFSVFCFVCCFQVVSLSYAYVTLPIAVMVVCTFQQSRQGAAKIF',
    '>A01-3\nMNSLWNSTSSSFFRQIFQSIYLCLLGISSVLSQVYILSSQQNKVFQKAFYYSLLKTFQG',
  ].join('\n\n')
  sequenceType.value = 'amino_acid'
}

// ==================== 设计参数（整批统一） ====================
const vectorMode = ref<'library' | 'upload' | 'online'>('library')
const adhocVector = ref<DesignVectorPayload | null>(null)
const adhocMode = computed<'upload' | 'online'>(() =>
  vectorMode.value === 'online' ? 'online' : 'upload'
)
const vectorId = ref('pET-28a')
const cloningMethod = ref<'gibson' | 'golden_gate' | 'restriction'>('gibson')
// 插入片段来源与克隆方法正交
const insertSource = ref<'pcr' | 'gene_synthesis'>('pcr')
const enzyme5 = ref('BamHI')
const enzyme3 = ref('EcoRI')
// Golden Gate 的 Type IIS 酶：后端必填（缺酶整批 422），默认 BsaI
const enzyme = ref('BsaI')
// Gibson 线性化方式与自定义臂（对齐单条设计页）
const gibsonLinearization = ref<'double_digest' | 'single_site' | 'custom_arms'>('double_digest')
const homologyArm5 = ref('')
const homologyArm3 = ref('')
const keepRestrictionSites = ref(true)
const insertDirection = ref<'auto' | 'reverse' | 'plus' | 'minus'>('auto')
const trimRedundantStart = ref(true)
const vectorNamePrefix = ref(false)
const excludeEnzymesText = ref('')

const availableEnzymeMap = ref<Record<string, { recognition_sequence?: string; is_type_iis?: boolean }>>({})
const enzymeMap = computed(() =>
  Object.keys(availableEnzymeMap.value).length ? availableEnzymeMap.value : {
    EcoRI: { recognition_sequence: 'GAATTC' }, BamHI: { recognition_sequence: 'GGATCC' },
    HindIII: { recognition_sequence: 'AAGCTT' }, XhoI: { recognition_sequence: 'CTCGAG' },
    NcoI: { recognition_sequence: 'CCATGG' }, SalI: { recognition_sequence: 'GTCGAC' },
  }
)
// Golden Gate 候选酶（REBASE 数据驱动，BsaI/BsmBI/BbsI 在前）；
// 旧后端没有该字段时回退到酶表里标了 is_type_iis 的酶
const goldenGateEnzymes = ref<{ name: string; recognition_sequence: string }[]>([])
const ggEnzymeMap = computed(() => {
  const out: Record<string, { recognition_sequence: string }> = {}
  for (const e of goldenGateEnzymes.value) out[e.name] = { recognition_sequence: e.recognition_sequence }
  if (Object.keys(out).length) return out
  for (const [name, meta] of Object.entries(enzymeMap.value)) {
    if (meta.is_type_iis) out[name] = { recognition_sequence: meta.recognition_sequence || '' }
  }
  return out
})

const protocolLanguage = ref<'zh' | 'en'>('zh')
const optimizeCodons = ref(true)
const targetSpecies = ref('ecoli')
const gcMin = ref(40)
const gcMax = ref(60)
const homologyArm = ref(20)
const oligoLengthMin = ref(40)
const oligoLengthMax = ref(80)
const overlapLength = ref(20)
const isSubmitting = ref(false)
const availableVectors = ref<VectorInfo[]>([])
const codonTables = ref<CodonTable[]>([])
const batchId = ref('')
const progress = ref<import('@/types').BatchProgress | null>(null)
const error = ref('')
const batchReport = ref<any>(null)

// 本地任务历史：刷新/重开页面可回到进度页
const recentBatches = ref<BatchHistoryEntry[]>([])

// Gibson 自定义臂模式下公共参数与克隆方法合并语义（与单条设计页一致）
const isArmsMode = computed(() =>
  cloningMethod.value === 'gibson' && gibsonLinearization.value === 'custom_arms')

const excludeEnzymes = computed(() =>
  excludeEnzymesText.value.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean))

// ==================== 提交与轮询 ====================
let pollInterval: number | null = null
let pollFailures = 0
const MAX_POLL_FAILURES = 5  // 后端持续报错时停止无限轮询

async function startPolling(id: string) {
  batchId.value = id
  pollFailures = 0
  const poll = async () => {
    try {
      const data = await getBatchProgress(id)
      pollFailures = 0
      progress.value = data
      if (data.status === 'completed') {
        fetchBatchReport()
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null }
      }
    } catch (e: any) {
      console.error('Poll error:', e)
      if (++pollFailures >= MAX_POLL_FAILURES) {
        stopPolling()
        error.value = '进度查询连续失败，请刷新页面重试'
      }
    }
  }
  poll()
  pollInterval = window.setInterval(poll, 2000)
}

function stopPolling() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null }
}

async function handleSubmit() {
  if (sequenceCount.value === 0) { error.value = '请输入至少一个序列'; return }
  if (invalidRows.value.length) {
    error.value = `第 ${invalidRows.value.map((i) => i + 1).join('、')} 行序列含所选类型的非法字符`
    return
  }
  if (cloningMethod.value === 'restriction' && enzyme5.value === enzyme3.value) {
    error.value = '双酶切请选择两种不同的限制酶'; return
  }
  if (cloningMethod.value === 'golden_gate' && !enzyme.value.trim()) {
    error.value = '请选择 Golden Gate 使用的 Type IIS 酶'; return
  }
  if (isArmsMode.value && (!homologyArm5.value.trim() || !homologyArm3.value.trim())) {
    error.value = '自定义同源臂模式需填写两条重组臂序列'; return
  }
  isSubmitting.value = true
  error.value = ''
  try {
    const result = await submitBatchDesign({
      sequences: parsedSequences.value.map((r) => r.sequence),
      sequence_names: parsedSequences.value.map((r) => r.name),
      sequence_type: sequenceType.value,
      cloning_method: cloningMethod.value,
      insert_source: insertSource.value,
      optimize_codons: optimizeCodons.value,
      target_species: targetSpecies.value,
      gc_min: gcMin.value,
      gc_max: gcMax.value,
      homology_arm: homologyArm.value,
      protocol_language: protocolLanguage.value,
      oligo_length_min: oligoLengthMin.value,
      oligo_length_max: oligoLengthMax.value,
      overlap_length: overlapLength.value,
      insert_direction: insertDirection.value,
      trim_redundant_start: trimRedundantStart.value,
      vector_name_prefix: vectorNamePrefix.value,
      ...(excludeEnzymes.value.length ? { exclude_enzymes: excludeEnzymes.value } : {}),
      ...(cloningMethod.value === 'gibson' ? {
        gibson_linearization: gibsonLinearization.value,
        keep_restriction_sites: keepRestrictionSites.value,
        ...(gibsonLinearization.value === 'custom_arms' ? {
          homology_arm_5: homologyArm5.value, homology_arm_3: homologyArm3.value,
        } : {}),
      } : {}),
      ...(cloningMethod.value === 'restriction' ? { enzyme_5: enzyme5.value, enzyme_3: enzyme3.value } : {}),
      ...(cloningMethod.value === 'golden_gate' ? { enzyme: enzyme.value } : {}),
      ...(vectorMode.value !== 'library' && adhocVector.value
        ? { vector: {
            id: adhocVector.value.id,
            name: adhocVector.value.name,
            sequence: adhocVector.value.sequence,
            topology: adhocVector.value.topology,
            features: adhocVector.value.features,
            source: adhocVector.value.source,
          }, vector_id: adhocVector.value.id }
        : { vector_id: vectorId.value }),
    })
    recordBatch({ batchId: result.batch_id, submittedAt: Date.now(), total: result.total })
    recentBatches.value = loadBatchHistory()
    isSubmitting.value = false
    startPolling(result.batch_id)
  } catch (e: any) { error.value = formatApiError(e, '提交失败'); isSubmitting.value = false }
}

async function resumeBatch(entry: BatchHistoryEntry) {
  error.value = ''
  batchReport.value = null
  progress.value = null
  await startPolling(entry.batchId)
}

// ==================== 初始化 ====================
onMounted(async () => {
  recentBatches.value = loadBatchHistory()
  try {
    try {
      const enzymeData = await getEnzymes()
      availableEnzymeMap.value = enzymeData.enzymes || {}
      goldenGateEnzymes.value = Array.isArray(enzymeData.golden_gate_enzymes) ? enzymeData.golden_gate_enzymes : []
      // 后端候选里没有默认的 BsaI 时（数据源差异）退到候选首项，避免提交即 422
      const ggNames = Object.keys(ggEnzymeMap.value)
      if (ggNames.length && !ggNames.includes(enzyme.value)) enzyme.value = ggNames[0]
    } catch { /* 兜底表 */ }
    availableVectors.value = await getVectors()
    if (availableVectors.value.length && !availableVectors.value.find(v => v.id === vectorId.value)) {
      vectorId.value = availableVectors.value[0].id
    }
  } catch (e) { console.warn('Failed to load vectors:', e) }
  try { codonTables.value = await getCodonTables() } catch (e) { console.warn('Failed to load codon tables:', e) }
})

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

async function handleDownloadPrimers() {
  try { await downloadBatchPrimers(batchId.value) }
  catch (e: any) { error.value = '下载失败: ' + (e.message || '未知错误') }
}

async function fetchBatchReport() {
  try { batchReport.value = await getBatchReport(batchId.value) }
  catch (e) { console.warn('Failed to fetch batch report:', e) }
}

function resetBatch() {
  stopPolling()
  batchId.value = ''
  progress.value = null
  batchReport.value = null
  isSubmitting.value = false
  error.value = ''
  recentBatches.value = loadBatchHistory()
}

onUnmounted(() => {
  stopPolling()
})
</script><template>
  <div class="batch-design-page">
    <h1>批量设计</h1>
    <p class="subtitle">一次提交多个序列，批量生成质粒构建方案与引物订购表</p>

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
            输入序列（FASTA（&gt;名称 行）或用空行 / --- 分隔多个序列）
          </label>
          <textarea
            v-model="sequencesText"
            class="form-input sequence-input"
            placeholder=">A01-1 + 换行 + 序列，空行分隔下一条；或不用 FASTA 头，直接用空行/---分隔裸序列"
            rows="12"
          ></textarea>
          <div class="input-actions">
            <button @click="pickFile" class="btn btn-secondary btn-small">
              上传 FASTA 文件
            </button>
            <input
              ref="fileInput" type="file" accept=".fasta,.fa,.fna,.txt"
              style="display: none" @change="onFileChange"
            />
            <button @click="loadExamples" class="btn btn-secondary btn-small">
              加载示例
            </button>
            <span class="sequence-count">
              已识别 {{ sequenceCount }} 个序列
            </span>
          </div>
        </div>

        <!-- 解析预览表：名称可编辑、长度、合法性 -->
        <div v-if="sequenceCount" class="preview-table-wrap">
          <table class="preview-table">
            <thead>
              <tr><th>#</th><th>名称</th><th>长度</th><th>状态</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in parsedSequences" :key="i"
                  :class="{ 'row-invalid': !validateParsed(row.sequence, sequenceType) }">
                <td>{{ i + 1 }}</td>
                <td>
                  <input
                    class="form-input name-input" :value="row.name"
                    :title="row.named ? '来自 FASTA 头行' : '推断名，可修改'"
                    @input="onNameEdit(i, $event)"
                  />
                </td>
                <td>{{ row.sequence.length }}</td>
                <td>
                  <span v-if="validateParsed(row.sequence, sequenceType)" class="ok">✓</span>
                  <span v-else class="bad">字符不合法</span>
                </td>
                <td><button class="btn btn-small btn-secondary" @click="removeRow(i)">移除</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 设计参数 -->
      <div class="form-section">
        <h2>公共参数</h2>
        <p class="hint">这些参数将应用于所有序列</p>

        <div class="form-group">
          <label class="form-label">目标载体</label>
          <div class="vector-mode-row">
            <select v-model="vectorMode" class="form-select mode-select">
              <option value="library">载体库</option>
              <option value="upload">上传载体文件</option>
              <option value="online">在线导入</option>
            </select>
            <select v-if="vectorMode === 'library'" v-model="vectorId" class="form-select">
              <option v-if="!availableVectors.length" value="pET-28a">pET-28a(+)</option>
              <option v-for="v in availableVectors" :key="v.id" :value="v.id">{{ v.name }}</option>
            </select>
          </div>
          <DesignVectorPicker
            v-if="vectorMode !== 'library'"
            :mode="adhocMode" :model-value="adhocVector"
            @update:model-value="adhocVector = $event"
          />
          <p v-if="vectorMode !== 'library'" class="hint-text">
            一次性载体整批共用，不写入共享载体库
          </p>
        </div>

        <div class="params-grid">
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

          <div class="form-group" v-if="cloningMethod === 'golden_gate'">
            <label class="form-label">Type IIS 酶</label>
            <EnzymeAutocomplete v-model="enzyme" :enzymes="ggEnzymeMap" placeholder="搜索 Type IIS 酶…" />
          </div>

          <div class="form-group" v-if="cloningMethod === 'gibson'">
            <label class="form-label">载体线性化方式</label>
            <select v-model="gibsonLinearization" class="form-select">
              <option value="double_digest">双酶切线性化（推荐）</option>
              <option value="single_site">单位点线性化</option>
              <option value="custom_arms">自定义同源臂</option>
            </select>
          </div>

          <div class="form-group" v-if="cloningMethod === 'gibson' && gibsonLinearization === 'double_digest'">
            <label class="form-label">同源臂长度 (bp)</label>
            <input v-model.number="homologyArm" type="number" class="form-input" min="15" max="40" />
          </div>

          <template v-if="cloningMethod === 'gibson' && gibsonLinearization === 'custom_arms'">
            <div class="form-group">
              <label class="form-label">5′ 重组臂序列</label>
              <input v-model="homologyArm5" type="text" class="form-input" placeholder="载体左连接点外侧序列" />
            </div>
            <div class="form-group">
              <label class="form-label">3′ 重组臂序列</label>
              <input v-model="homologyArm3" type="text" class="form-input" placeholder="载体右连接点外侧序列" />
            </div>
            <div class="form-group">
              <label class="checkbox-label">
                <input type="checkbox" v-model="keepRestrictionSites" />
                <span>保留连接处酶切位点（关闭为无缝连接）</span>
              </label>
            </div>
          </template>

          <div class="form-group" v-if="cloningMethod === 'restriction'">
            <label class="form-label">酶切位点一</label>
            <EnzymeAutocomplete v-model="enzyme5" :enzymes="enzymeMap" placeholder="如 NdeI" />
          </div>

          <div class="form-group" v-if="cloningMethod === 'restriction'">
            <label class="form-label">酶切位点二</label>
            <EnzymeAutocomplete v-model="enzyme3" :enzymes="enzymeMap" placeholder="如 XhoI" />
          </div>

          <div class="form-group">
            <label class="form-label">插入方向</label>
            <select v-model="insertDirection" class="form-select">
              <option value="auto">自动判定（按载体启动子/RBS）</option>
              <option value="reverse">反向（反义构建）</option>
              <option value="plus">正链</option>
              <option value="minus">负链</option>
            </select>
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
            <label class="form-label">GC 含量目标 (%)</label>
            <div class="range-inputs">
              <input v-model.number="gcMin" type="number" class="form-input" min="20" max="50" />
              <span>—</span>
              <input v-model.number="gcMax" type="number" class="form-input" min="50" max="80" />
            </div>
          </div>

          <div class="form-group" v-if="sequenceType === 'amino_acid'">
            <label class="checkbox-label">
              <input type="checkbox" v-model="optimizeCodons" />
              <span>启用密码子优化</span>
            </label>
          </div>

          <div class="form-group" v-if="sequenceType === 'amino_acid'">
            <label class="form-label">排除酶切位点（逗号分隔，可选）</label>
            <input v-model="excludeEnzymesText" type="text" class="form-input" placeholder="如 EcoRI, BamHI" />
          </div>

          <div class="form-group">
            <label class="checkbox-label">
              <input type="checkbox" v-model="trimRedundantStart" />
              <span>位点自带起始密码子时去除冗余 ATG</span>
            </label>
          </div>

          <div class="form-group">
            <label class="checkbox-label">
              <input type="checkbox" v-model="vectorNamePrefix" />
              <span>质粒名加载体名前缀（载体名-序列名）</span>
            </label>
          </div>

          <div class="form-group">
            <label class="form-label">实验方案语言</label>
            <div class="language-toggle">
              <button class="toggle-btn" :class="{ active: protocolLanguage === 'zh' }" @click="protocolLanguage = 'zh'">中文</button>
              <button class="toggle-btn" :class="{ active: protocolLanguage === 'en' }" @click="protocolLanguage = 'en'">English</button>
            </div>
          </div>
        </div>

        <p v-if="cloningMethod === 'restriction'" class="hint-text">
          两把酶的先后顺序不影响结果：基因接哪一端由载体自己的启动子/RBS 判定。
        </p>

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
            <input v-model.number="overlapLength" type="number" class="form-input" min="10" max="40" />
          </div>
        </div>
      </div>

      <!-- 提交 -->
      <div v-if="!batchId" class="submit-section">
        <div v-if="error" class="error-message">{{ error }}</div>
        <button
          @click="handleSubmit"
          class="btn btn-primary submit-btn"
          :disabled="isSubmitting || sequenceCount === 0 || invalidRows.length > 0"
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
            {{ Number(progress.progress_percent ?? 0).toFixed(1) }}%
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
                <span class="result-name">{{ result.sequence_name || result.design_id }}</span>
                <span class="result-status" :class="result.status">{{ result.status }}</span>
              </div>
              <div class="result-meta">
                <span class="result-id">{{ result.design_id }}</span>
                <span v-if="(result.warnings_count ?? 0) > 0" class="warn-badge"
                      title="有需要人工核对的警告">⚠ {{ result.warnings_count }}</span>
              </div>
              <div class="result-metrics">
                <span v-if="result.cai">CAI: {{ result.cai.toFixed(3) }}</span>
                <span v-if="result.gc_content">GC: {{ result.gc_content.toFixed(1) }}%</span>
                <span v-if="result.primer_count">引物: {{ result.primer_count }}</span>
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
          <button class="btn btn-secondary" @click="handleDownloadPrimers">
            下载引物订购表 (TSV)
          </button>
          <button class="btn btn-primary" @click="handleDownloadBatch">
            下载所有结果 (ZIP)
          </button>
          <button class="btn btn-secondary" @click="resetBatch">
            新建批量任务
          </button>
          <button class="btn btn-secondary" @click="router.push('/')">
            返回首页
          </button>
        </div>
      </div>

      <!-- 最近任务（本地历史：刷新/重开页面可回看） -->
      <div v-if="!batchId && recentBatches.length" class="form-section">
        <h2>最近任务</h2>
        <p class="hint">保存在本浏览器，换设备不可见</p>
        <div class="recent-list">
          <div v-for="entry in recentBatches" :key="entry.batchId" class="recent-item">
            <span class="recent-id">{{ entry.batchId }}</span>
            <span class="recent-meta">{{ entry.total }} 条 · {{ new Date(entry.submittedAt).toLocaleString() }}</span>
            <button class="btn btn-small btn-secondary" @click="resumeBatch(entry)">查看进度</button>
          </div>
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
  min-width: 280px;
}

.progress-section h2 {
  font-size: 1.125rem;
  margin-bottom: 1rem;
  color: var(--primary-color);
}

.progress-bar-container {
  background: white;
  padding: 1.25rem;
  border-radius: 0.75rem;
  box-shadow: var(--shadow);
}

.progress-info {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
  font-size: 0.875rem;
}

.failed-count {
  color: #ef4444;
}

.progress-bar {
  height: 0.625rem;
  background: #e5e7eb;
  border-radius: 999px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  transition: width 0.4s ease;
}

.progress-percent {
  margin-top: 0.5rem;
  text-align: right;
  font-size: 0.875rem;
  color: var(--text-secondary);
}

.results-list {
  margin-top: 1.5rem;
}

.results-list h3 {
  font-size: 1rem;
  margin-bottom: 0.75rem;
}

.result-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 0.75rem;
}

.result-card {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
  padding: 0.75rem;
  cursor: pointer;
  transition: border-color 0.2s;
}

.result-card:hover {
  border-color: var(--primary-color);
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.result-status {
  font-size: 0.75rem;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  background: #d1fae5;
  color: #059669;
}

.result-status.failed {
  background: #fee2e2;
  color: #dc2626;
}

.result-metrics {
  display: flex;
  gap: 0.75rem;
  margin-top: 0.5rem;
  font-size: 0.8rem;
  color: var(--text-secondary);
}

.errors-list {
  margin-top: 1.5rem;
}

.error-item {
  background: #fef2f2;
  border-left: 3px solid #ef4444;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.5rem;
  font-size: 0.875rem;
  border-radius: 0 0.25rem 0.25rem 0;
}

.report-section {
  margin-top: 1.5rem;
  background: white;
  padding: 1.25rem;
  border-radius: 0.75rem;
  box-shadow: var(--shadow);
}

.report-stats {
  display: flex;
  gap: 1.5rem;
  font-size: 0.875rem;
}

.completion-actions {
  margin-top: 1.5rem;
  display: flex;
  gap: 0.75rem;
  justify-content: center;
  flex-wrap: wrap;
}

.error-message {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #dc2626;
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  margin-bottom: 1rem;
  font-size: 0.875rem;
}

.hint-text {
  font-size: 0.8rem;
  color: var(--text-secondary);
  margin-top: 0.25rem;
}

.language-toggle {
  display: flex;
  gap: 0;
  border: 1px solid #d1d5db;
  border-radius: 0.5rem;
  overflow: hidden;
  width: fit-content;
}

.toggle-btn {
  padding: 0.4rem 1rem;
  border: none;
  background: white;
  cursor: pointer;
  font-size: 0.85rem;
}

.toggle-btn.active {
  background: var(--primary-color);
  color: white;
}

.range-inputs {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.range-inputs .form-input {
  width: 90px;
}

.synth-params {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px dashed #e5e7eb;
}

/* v2.27.0 批量页新增 */
.preview-table-wrap {
  margin-top: 1rem;
  overflow-x: auto;
}

.preview-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}

.preview-table th,
.preview-table td {
  border-bottom: 1px solid var(--border-color, #e5e7eb);
  padding: 0.4rem 0.6rem;
  text-align: left;
}

.preview-table .row-invalid td {
  background: #fef2f2;
}

.preview-table .name-input {
  padding: 0.25rem 0.5rem;
  font-size: 0.85rem;
}

.preview-table .ok {
  color: #10b981;
}

.preview-table .bad {
  color: #ef4444;
  font-size: 0.8rem;
}

.vector-mode-row {
  display: flex;
  gap: 0.75rem;
}

.vector-mode-row .mode-select {
  max-width: 180px;
}

.result-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.25rem;
}

.result-name {
  font-weight: 600;
}

.result-id {
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-family: 'JetBrains Mono', monospace;
}

.warn-badge {
  color: #b45309;
  background: #fef3c7;
  border-radius: 999px;
  padding: 0 0.5rem;
  font-size: 0.75rem;
}

.recent-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.recent-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 0.5rem;
}

.recent-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
}

.recent-meta {
  color: var(--text-secondary);
  font-size: 0.8rem;
  flex: 1;
}
</style>
