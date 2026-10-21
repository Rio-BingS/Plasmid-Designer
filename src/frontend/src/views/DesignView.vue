<script setup lang="ts">
import { ref, computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { DesignRequest, DesignVectorPayload, VectorInfo, CodonTable } from '@/types'
import { submitDesign, getVectors, getCodonTables, getEnzymes, formatApiError } from '@/api'
import EnzymeAutocomplete from '@/components/EnzymeAutocomplete.vue'
import DesignVectorPicker from '@/components/DesignVectorPicker.vue'
import {
  AMINO_ACID_CHARS, describeUnsupportedChars, findUnsupportedChars, normalizeSequenceInput
} from '@/utils/sequence'
import { clearDesignDraft, loadDesignDraft, saveDesignDraft } from '@/utils/designDraft'

const router = useRouter()

// 表单状态
const sequenceType = ref<'amino_acid' | 'dna'>('amino_acid')
const sequence = ref('')
const sequenceName = ref('insert')
const vectorId = ref('pET-28a')
// 载体来源：载体库（共享库）/ 本地上传 / 在线导入。后两者的一次性载体只随
// 本次设计提交，不写载体库（见 components/DesignVectorPicker.vue）
const vectorMode = ref<'library' | 'upload' | 'online'>('library')
const adhocVector = ref<DesignVectorPayload | null>(null)
const adhocMode = computed<'upload' | 'online'>(() =>
  vectorMode.value === 'online' ? 'online' : 'upload'
)
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
// Gibson：定位同源重组位置的酶切位点（缺省 MCS 起点）
const gibsonSite = ref('')
// 全基因合成：需从优化序列排除的限制酶（多选）
const excludeEnzymes = ref<string[]>([])
// 排除位点只能靠同义密码子实现：只对「氨基酸输入 + 启用密码子优化」有意义。
// DNA 输入无法沉默突变，关闭优化时只是反翻译——两种情况都不展示、不提交该参数。
const excludeEnzymesApplicable = computed(() =>
  sequenceType.value === 'amino_acid'
  && optimizeCodons.value
  && insertSource.value === 'gene_synthesis'
)
const oligoLengthMin = ref(40)
const oligoLengthMax = ref(80)
const overlapLength = ref(20)

// 当前载体可供 Gibson 定位的酶：
// - 载体库载体 = 其 MCS 上的唯一酶位点
// - 一次性载体 = 全序列的唯一位点（后端按扫描结果给出；没有 MCS 注释时也能定位）
const currentVectorMcsEnzymes = computed(() =>
  adhocVector.value
    ? adhocVector.value.unique_enzymes || []
    : availableVectors.value.find(v => v.id === vectorId.value)?.mcs_enzymes || []
)

function setVectorMode(mode: 'library' | 'upload' | 'online') {
  if (mode === vectorMode.value) return
  vectorMode.value = mode
  // 换来源一律重选：否则切回载体库后仍会带着上一次的一次性载体提交
  adhocVector.value = null
}


// ==================== 表单草稿（切页/刷新不丢） ====================
// 用户报障：切到别的页面再回来，整张表单被清空。草稿存 sessionStorage
// （同标签页有效、关标签页即清除），读写都做形状校验，旧草稿不会把页面带坏。
function currentDraft() {
  return {
    sequenceType: sequenceType.value,
    sequence: sequence.value,
    sequenceName: sequenceName.value,
    vectorId: vectorId.value,
    vectorMode: vectorMode.value,
    adhocVector: adhocVector.value,
    cloningMethod: cloningMethod.value,
    insertSource: insertSource.value,
    protocolLanguage: protocolLanguage.value,
    optimizeCodons: optimizeCodons.value,
    targetSpecies: targetSpecies.value,
    gcMin: gcMin.value,
    gcMax: gcMax.value,
    homologyArm: homologyArm.value,
    enzyme: enzyme.value,
    enzyme5: enzyme5.value,
    enzyme3: enzyme3.value,
    gibsonSite: gibsonSite.value,
    excludeEnzymes: [...excludeEnzymes.value],
    oligoLengthMin: oligoLengthMin.value,
    oligoLengthMax: oligoLengthMax.value,
    overlapLength: overlapLength.value
  }
}

function isAdhocVectorDraft(v: any): v is DesignVectorPayload {
  return !!v && typeof v === 'object'
    && typeof v.id === 'string' && typeof v.name === 'string'
    && typeof v.sequence === 'string' && v.sequence.length > 0
    && Array.isArray(v.features)
    && (v.topology === 'circular' || v.topology === 'linear')
}

function applyDraft(d: Record<string, any>) {
  const oneOf = <T extends string>(v: any, allowed: readonly T[], fallback: T): T =>
    allowed.includes(v) ? (v as T) : fallback
  const num = (v: any, fallback: number) =>
    (typeof v === 'number' && Number.isFinite(v) ? v : fallback)

  sequenceType.value = oneOf(d.sequenceType, ['amino_acid', 'dna'] as const, 'amino_acid')
  if (typeof d.sequence === 'string') sequence.value = d.sequence
  if (typeof d.sequenceName === 'string' && d.sequenceName) sequenceName.value = d.sequenceName
  if (typeof d.vectorId === 'string' && d.vectorId) vectorId.value = d.vectorId
  vectorMode.value = oneOf(d.vectorMode, ['library', 'upload', 'online'] as const, 'library')
  adhocVector.value = isAdhocVectorDraft(d.adhocVector) ? d.adhocVector : null
  // 恢复的是一次性载体但来源模式丢了：按载体来源兜底，避免「选了载体却当载体库」
  if (adhocVector.value && vectorMode.value === 'library') vectorMode.value = 'upload'
  cloningMethod.value = oneOf(d.cloningMethod, ['gibson', 'golden_gate', 'restriction'] as const, 'gibson')
  insertSource.value = oneOf(d.insertSource, ['pcr', 'gene_synthesis'] as const, 'pcr')
  protocolLanguage.value = oneOf(d.protocolLanguage, ['zh', 'en'] as const, 'zh')
  if (typeof d.optimizeCodons === 'boolean') optimizeCodons.value = d.optimizeCodons
  if (typeof d.targetSpecies === 'string' && d.targetSpecies) targetSpecies.value = d.targetSpecies
  gcMin.value = num(d.gcMin, gcMin.value)
  gcMax.value = num(d.gcMax, gcMax.value)
  homologyArm.value = num(d.homologyArm, homologyArm.value)
  if (typeof d.enzyme === 'string' && d.enzyme) enzyme.value = d.enzyme
  if (typeof d.enzyme5 === 'string' && d.enzyme5) enzyme5.value = d.enzyme5
  if (typeof d.enzyme3 === 'string' && d.enzyme3) enzyme3.value = d.enzyme3
  if (typeof d.gibsonSite === 'string') gibsonSite.value = d.gibsonSite
  if (Array.isArray(d.excludeEnzymes)) {
    excludeEnzymes.value = d.excludeEnzymes.filter((x: any) => typeof x === 'string')
  }
  oligoLengthMin.value = num(d.oligoLengthMin, oligoLengthMin.value)
  oligoLengthMax.value = num(d.oligoLengthMax, oligoLengthMax.value)
  overlapLength.value = num(d.overlapLength, overlapLength.value)
}

const restoredDraft = loadDesignDraft()
if (restoredDraft) applyDraft(restoredDraft as Record<string, any>)

let draftTimer: ReturnType<typeof setTimeout> | null = null

// 输入频繁（粘贴长序列）时不必每次按键都写存储，400ms 合并一次
watch(currentDraft, (draft) => {
  if (draftTimer) clearTimeout(draftTimer)
  draftTimer = setTimeout(() => {
    draftTimer = null
    saveDesignDraft(draft)
  }, 400)
})

onBeforeUnmount(() => {
  // 离开页面时按当前值补写一次：Vue 的 watch 默认在下一帧批处理，
  // 「输完立刻点别的页面」时 watcher 可能还没跑，只靠计时器会丢掉最后几个字符
  if (draftTimer) {
    clearTimeout(draftTimer)
    draftTimer = null
  }
  saveDesignDraft(currentDraft())
})

function clearForm() {
  clearDesignDraft()
  sequence.value = ''
  sequenceName.value = 'insert'
  sequenceType.value = 'amino_acid'
  vectorMode.value = 'library'
  vectorId.value = availableVectors.value[0]?.id || 'pET-28a'
  adhocVector.value = null
  cloningMethod.value = 'gibson'
  insertSource.value = 'pcr'
  optimizeCodons.value = true
  targetSpecies.value = 'ecoli'
  gcMin.value = 40
  gcMax.value = 60
  homologyArm.value = 20
  enzyme5.value = 'BamHI'
  enzyme3.value = 'EcoRI'
  gibsonSite.value = ''
  excludeEnzymes.value = []
  oligoLengthMin.value = 40
  oligoLengthMax.value = 80
  overlapLength.value = 20
  error.value = ''
}

// ==================== 序列字符归一与前置校验 ====================
/** 全角/异体星号 → '*'（中文输入法 Shift+8 打出的是 ＊，后端会当成非法字符） */
function normalizeSequence() {
  const fixed = normalizeSequenceInput(sequence.value)
  if (fixed !== sequence.value) sequence.value = fixed
}

// 提交状态
// 动态选项
const availableVectors = ref<VectorInfo[]>([])
const codonTables = ref<CodonTable[]>([])
const availableEnzymes = ref<string[]>([])
const availableEnzymeMap = ref<Record<string, { recognition_sequence?: string; is_type_iis?: boolean }>>({})

// API 不可用时的兜底酶表（名称 → 识别序列）
const FALLBACK_ENZYME_MAP: Record<string, { recognition_sequence: string }> = {
  EcoRI: { recognition_sequence: 'GAATTC' }, BamHI: { recognition_sequence: 'GGATCC' },
  HindIII: { recognition_sequence: 'AAGCTT' }, XhoI: { recognition_sequence: 'CTCGAG' },
  NcoI: { recognition_sequence: 'CCATGG' }, SalI: { recognition_sequence: 'GTCGAC' },
  NotI: { recognition_sequence: 'GCGGCCGC' }, XbaI: { recognition_sequence: 'TCTAGA' },
  PstI: { recognition_sequence: 'CTGCAG' }, SmaI: { recognition_sequence: 'CCCGGG' },
  NdeI: { recognition_sequence: 'CATATG' }, NdeII: { recognition_sequence: 'GATC' },
  SpeI: { recognition_sequence: 'ACTAGT' }, KpnI: { recognition_sequence: 'GGTACC' },
  SacI: { recognition_sequence: 'GAGCTC' },
  BsaI: { recognition_sequence: 'GGTCTC' }, BsmBI: { recognition_sequence: 'CGTCTC' },
  BbsI: { recognition_sequence: 'GAAGAC' },
}

// 全局酶表（API 失败时回退兜底表）
const enzymeMap = computed(() =>
  Object.keys(availableEnzymeMap.value).length ? availableEnzymeMap.value : FALLBACK_ENZYME_MAP
)

// Golden Gate：后端给出的候选酶（REBASE 数据驱动，BsaI/BsmBI/BbsI 在前）；
// 旧后端没有该字段时回退到酶表里标了 is_type_iis 的酶
const goldenGateEnzymes = ref<{ name: string; recognition_sequence: string }[]>([])
const ggEnzymeMap = computed(() => {
  const out: Record<string, { recognition_sequence: string }> = {}
  for (const e of goldenGateEnzymes.value) out[e.name] = { recognition_sequence: e.recognition_sequence }
  if (Object.keys(out).length) return out
  for (const [name, meta] of Object.entries(enzymeMap.value)) {
    if (meta.is_type_iis) out[name] = { recognition_sequence: meta.recognition_sequence || '' }
  }
  if (!Object.keys(out).length) {
    for (const k of ['BsaI', 'BsmBI', 'BbsI']) out[k] = { ...FALLBACK_ENZYME_MAP[k] }
  }
  return out
})

// Gibson 定位：仅当前载体 MCS 上存在的酶
const gibsonMcsMap = computed(() => {
  const out: Record<string, { recognition_sequence: string }> = {}
  for (const name of currentVectorMcsEnzymes.value) {
    out[name] = { recognition_sequence: enzymeMap.value[name]?.recognition_sequence || '' }
  }
  return out
})

const isSubmitting = ref(false)
const error = ref('')

// 提交设计
async function handleSubmit() {
  // 先做字符归一与前置校验：把「哪个字符、第几位」直接告诉用户，
  // 而不是让后端在密码子优化阶段抛「无效的氨基酸代码」
  normalizeSequence()
  if (!sequence.value.trim()) {
    error.value = '请输入序列'
    return
  }
  const isAa = sequenceType.value === 'amino_acid'
  const bad = findUnsupportedChars(sequence.value, isAa ? AMINO_ACID_CHARS : 'ATGC')
  if (bad.length) {
    error.value = describeUnsupportedChars(bad, isAa ? 'amino_acid' : 'dna')
    return
  }
  if (cloningMethod.value === 'restriction' && enzyme5.value === enzyme3.value) {
    error.value = '双酶切请选择两种不同的限制酶'
    return
  }
  if (vectorMode.value !== 'library' && !adhocVector.value) {
    error.value = vectorMode.value === 'upload'
      ? '请先上传载体文件，或切回「载体库」选择载体'
      : '请先在线导入载体，或切回「载体库」选择载体'
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
      oligo_length_min: oligoLengthMin.value,
      oligo_length_max: oligoLengthMax.value,
      overlap_length: overlapLength.value,
      include_report: true,
      protocol_language: protocolLanguage.value
    }
    if (cloningMethod.value === 'restriction') {
      request.enzyme_5 = enzyme5.value
      request.enzyme_3 = enzyme3.value
    }
    if (cloningMethod.value === 'gibson' && gibsonSite.value) {
      request.gibson_site = gibsonSite.value
    }
    if (excludeEnzymesApplicable.value && excludeEnzymes.value.length) {
      request.exclude_enzymes = excludeEnzymes.value
    }
    if (adhocVector.value) {
      // 一次性载体：随请求内联提交，后端现场构造 Vector（不写载体库）
      request.vector_id = adhocVector.value.id
      request.vector = {
        id: adhocVector.value.id,
        name: adhocVector.value.name,
        sequence: adhocVector.value.sequence,
        topology: adhocVector.value.topology,
        features: adhocVector.value.features,
        source: adhocVector.value.source
      }
    }

    const result = await submitDesign(request)
    router.push(`/result/${result.design_id}`)

  } catch (e: any) {
    error.value = formatApiError(e, '提交失败，请重试')
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
    availableEnzymeMap.value = enzymeData.enzymes || {}
    goldenGateEnzymes.value = Array.isArray(enzymeData.golden_gate_enzymes) ? enzymeData.golden_gate_enzymes : []
    availableEnzymes.value = Object.keys(enzymeData.enzymes || {})
    // enzyme 只用于 Golden Gate：回退须落在 Type IIS 候选内，
    // 退到全酶表首项（如 EcoRI）会让 Golden Gate 提交直接 422
    const ggNames = Object.keys(ggEnzymeMap.value)
    if (ggNames.length && !ggNames.includes(enzyme.value)) {
      enzyme.value = ggNames[0]
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
            :placeholder="sequenceType === 'amino_acid' ? '请输入氨基酸序列（单字母代码，终止符用 *；支持 FASTA 分行粘贴）' : '请输入DNA序列'"
            rows="6"
            @blur="normalizeSequence"
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

          <div class="vector-source-toggle">
            <button
              type="button"
              class="toggle-btn"
              :class="{ active: vectorMode === 'library' }"
              @click="setVectorMode('library')"
            >载体库</button>
            <button
              type="button"
              class="toggle-btn"
              :class="{ active: vectorMode === 'upload' }"
              @click="setVectorMode('upload')"
            >本地上传</button>
            <button
              type="button"
              class="toggle-btn"
              :class="{ active: vectorMode === 'online' }"
              @click="setVectorMode('online')"
            >在线导入</button>
          </div>

          <select v-if="vectorMode === 'library'" v-model="vectorId" class="form-select">
            <option v-if="!availableVectors.length" value="pET-28a">pET-28a(+) - E.coli 表达载体</option>
            <option
              v-for="v in availableVectors"
              :key="v.id"
              :value="v.id"
            >
              {{ v.name }}{{ v.host?.length ? ` - ${v.host.join('/')}` : '' }}
            </option>
          </select>
          <DesignVectorPicker
            v-else
            :mode="adhocMode"
            :model-value="adhocVector"
            @update:model-value="adhocVector = $event"
          />
          <p v-if="vectorMode !== 'library'" class="hint-text">
            自己上传/导入的载体只在本次设计中使用，不会写进共享载体库。
          </p>
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
            <label class="form-label">寡核苷酸长度范围 (bp)</label>
            <div class="range-inputs">
              <input v-model.number="oligoLengthMin" type="number" class="form-input" min="20" max="100" placeholder="最短" />
              <span>—</span>
              <input v-model.number="oligoLengthMax" type="number" class="form-input" min="30" max="120" placeholder="最长" />
            </div>
            <p v-if="oligoLengthMin > oligoLengthMax" class="hint-text warning-text">
              ⚠️ 最短长度不能大于最长长度
            </p>
            <p class="hint-text">算法会在该范围内均衡分片，每片同时给出正链与反义链</p>
          </div>
          <div class="form-group">
            <label class="form-label">重叠区域长度 (bp)</label>
            <input v-model.number="overlapLength" type="number" class="form-input" min="10" max="40" />
            <p class="hint-text">相邻寡核苷酸共享的互补重叠区大小，GC 含量高的基因可适当加大</p>
          </div>
          <div v-if="excludeEnzymesApplicable" class="form-group">
            <label class="form-label">排除的限制酶位点（可选）</label>
            <EnzymeAutocomplete v-model="excludeEnzymes" :enzymes="enzymeMap" multiple placeholder="输入以搜索要排除的酶…" />
            <p class="hint-text">仅对氨基酸输入且启用密码子优化时生效；DNA 输入或关闭优化时会自动忽略</p>
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
          <div class="form-group">
            <label class="form-label">定位酶切位点（可选）</label>
            <EnzymeAutocomplete
              v-model="gibsonSite"
              :enzymes="gibsonMcsMap"
              placeholder="搜索；留空为自动（MCS 起点）"
            />
            <p class="hint-text">选择载体上的酶切位点以定位同源重组位置（载体线性化点），引物同源臂将围绕该位点设计</p>
          </div>
        </div>

        <!-- Golden Gate 参数 -->
        <div v-if="cloningMethod === 'golden_gate'" class="method-params">
          <div class="form-group">
            <label class="form-label">Type IIS 酶</label>
            <EnzymeAutocomplete v-model="enzyme" :enzymes="ggEnzymeMap" placeholder="搜索 Type IIS 酶…" />
          </div>
        </div>

        <!-- 限制性酶切参数（双酶切） -->
        <div v-if="cloningMethod === 'restriction'" class="method-params">
          <div class="form-group">
            <label class="form-label">5' 端限制酶</label>
            <EnzymeAutocomplete v-model="enzyme5" :enzymes="enzymeMap" placeholder="搜索 5' 端限制酶…" />
          </div>
          <div class="form-group">
            <label class="form-label">3' 端限制酶</label>
            <EnzymeAutocomplete v-model="enzyme3" :enzymes="enzymeMap" placeholder="搜索 3' 端限制酶…" />
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
        <button
          type="button"
          class="btn btn-secondary clear-btn"
          :disabled="isSubmitting"
          @click="clearForm"
        >
          清空表单
        </button>
        <p class="hint-text">表单会自动保存：切到其他页面或刷新后再回来，输入的内容仍在。</p>
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

.clear-btn {
  margin-left: 0.75rem;
  font-size: 0.875rem;
  padding: 0.6rem 1.2rem;
}

.language-toggle,
.vector-source-toggle {
  display: inline-flex;
  border: 2px solid var(--border-color);
  border-radius: 0.5rem;
  overflow: hidden;
}

.vector-source-toggle {
  margin-bottom: 0.6rem;
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
