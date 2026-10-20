<script setup lang="ts">
/**
 * 设计页的「自有载体」入口：本地上传 / 在线导入 → 一次性载体
 *
 * 为什么不是「导入载体库」：载体库是共享库、写入口只对管理员开放
 * （/api/vectors/import/*），普通用户只想把自己手里这份载体用起来。
 * 这里取到的载体**不落盘、不入库**，只随本次设计提交（后端 /api/design/vector/*）。
 */
import { computed, ref, watch } from 'vue'
import type { DesignVectorPayload } from '@/types'
import {
  formatApiError, getDesignVectorSources, importDesignVectorOnline,
  searchDesignVectorSnapgene, uploadDesignVector
} from '@/api'
import type { OnlineSourceInfo } from '@/api'
import { useIsAdmin, useIsAuthenticated } from '@/composables/useIsAdmin'

const props = defineProps<{
  mode: 'upload' | 'online'
  modelValue: DesignVectorPayload | null
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: DesignVectorPayload | null): void }>()

const ACCEPT = '.gb,.gbk,.genbank,.gbff,.fa,.fasta,.fna,.dna'

const busy = ref(false)
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

// ==================== 在线来源 ====================
const isAdmin = useIsAdmin()
const loggedIn = useIsAuthenticated()
const sources = ref<OnlineSourceInfo[]>([])
const snapgeneEnabled = ref(false)
const snapgeneUsable = computed(() => snapgeneEnabled.value && loggedIn.value)
const onlineSource = ref<'snapgene' | 'ncbi' | 'ensembl' | 'addgene'>('ncbi')

const sgQuery = ref('')
const sgRows = ref<{
  plasmid_id: string; name: string; filename?: string; set_id?: string; set_name?: string
}[]>([])
const sgSearched = ref(false)

const ncbiAccession = ref('')
const ensemblId = ref('')
const ensemblType = ref<'genomic' | 'cds' | 'cdna'>('cds')
const addgeneId = ref('')

const sourceMeta = computed(() => sources.value.find((s) => s.id === onlineSource.value))
const sourceLabel = computed(() => {
  const v = props.modelValue
  if (!v) return ''
  const hit = sources.value.find((s) => v.source.startsWith(s.id))
  return hit ? hit.name : v.source
})

async function ensureSourceMeta() {
  if (sources.value.length) return
  try {
    const res = await getDesignVectorSources()
    sources.value = res.sources || []
    snapgeneEnabled.value = res.snapgene_online_enabled === true
  } catch {
    /* 许可文案拿不到不阻断导入：后端仍按自己的开关与权限判定 */
  }
}

/** SnapGene / Addgene 不可用时不要停在那个页签上 */
watch([snapgeneUsable, isAdmin], () => {
  if (onlineSource.value === 'snapgene' && !snapgeneUsable.value) onlineSource.value = 'ncbi'
  if (onlineSource.value === 'addgene' && !isAdmin.value) onlineSource.value = 'ncbi'
})

watch(() => props.mode, () => { error.value = '' })

function pick(payload: DesignVectorPayload) {
  error.value = ''
  emit('update:modelValue', payload)
}

function clear() {
  error.value = ''
  sgRows.value = []
  sgSearched.value = false
  if (fileInput.value) fileInput.value.value = ''
  emit('update:modelValue', null)
}

// ==================== 本地上传 ====================
async function onFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files && input.files[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    pick(await uploadDesignVector(file))
  } catch (e: any) {
    error.value = formatApiError(e, '文件解析失败，请确认格式正确')
  } finally {
    busy.value = false
  }
}

// ==================== 在线导入 ====================
async function searchSnapgene() {
  const q = sgQuery.value.trim()
  if (!q) { error.value = '请输入质粒名，如 pET-30a'; return }
  busy.value = true
  error.value = ''
  sgRows.value = []
  try {
    const res = await searchDesignVectorSnapgene(q, 20)
    sgRows.value = (res.plasmids || []).slice(0, 20)
    sgSearched.value = true
    if (!sgRows.value.length) error.value = '没有找到与「' + q + '」匹配的 SnapGene 序列'
  } catch (e: any) {
    error.value = formatApiError(e, '搜索失败，请稍后再试')
  } finally {
    busy.value = false
  }
}

async function pickSnapgene(row: { plasmid_id: string; set_id?: string }) {
  busy.value = true
  error.value = ''
  try {
    pick(await importDesignVectorOnline({
      source: 'snapgene', plasmid_id: row.plasmid_id, set_id: row.set_id
    }))
  } catch (e: any) {
    error.value = formatApiError(e, '导入失败，请稍后再试')
  } finally {
    busy.value = false
  }
}

async function importOnline() {
  const payload: Parameters<typeof importDesignVectorOnline>[0] = { source: onlineSource.value }
  if (onlineSource.value === 'ncbi') {
    const acc = ncbiAccession.value.trim()
    if (!acc) { error.value = '请输入 NCBI accession，如 L09137'; return }
    payload.accession = acc
  } else if (onlineSource.value === 'ensembl') {
    const id = ensemblId.value.trim()
    if (!id) { error.value = '请输入 Ensembl ID，如 ENSG00000139618'; return }
    payload.identifier = id
    payload.seq_type = ensemblType.value
  } else if (onlineSource.value === 'addgene') {
    const id = addgeneId.value.trim()
    if (!id) { error.value = '请输入 Addgene 质粒编号，如 12259'; return }
    payload.addgene_id = id
  } else {
    await searchSnapgene()
    return
  }
  busy.value = true
  error.value = ''
  try {
    pick(await importDesignVectorOnline(payload))
  } catch (e: any) {
    error.value = formatApiError(e, '导入失败，请稍后再试')
  } finally {
    busy.value = false
  }
}

if (props.mode === 'online') void ensureSourceMeta()
watch(() => props.mode, (m) => { if (m === 'online') void ensureSourceMeta() })
</script>

<template>
  <div class="dvp">
    <!-- 已选中的一次性载体 -->
    <div v-if="modelValue" class="dvp-card">
      <div class="dvp-card-head">
        <strong class="dvp-name">{{ modelValue.name }}</strong>
        <span class="dvp-badge">{{ sourceLabel }}</span>
        <button type="button" class="dvp-link" @click="clear">换一个</button>
      </div>
      <p class="dvp-meta">
        {{ modelValue.length }} bp ·
        {{ modelValue.topology === 'linear' ? '线性' : '环状' }} ·
        {{ modelValue.features.length }} 个元件
        <template v-if="modelValue.mcs">
          · {{ modelValue.mcs.origin === 'feature' ? 'MCS（文件注释）' : '推测插入点' }}
          {{ modelValue.mcs.start }}<template
            v-if="modelValue.mcs.end >= modelValue.mcs.start"
          >–{{ modelValue.mcs.end }}</template> bp
        </template>
      </p>
      <ul v-if="modelValue.warnings.length" class="dvp-warn">
        <li v-for="w in modelValue.warnings" :key="w">⚠️ {{ w }}</li>
      </ul>
      <p v-if="modelValue.license" class="dvp-license">{{ modelValue.license }}</p>
    </div>

    <template v-else>
      <!-- 本地上传 -->
      <div v-if="mode === 'upload'" class="dvp-panel">
        <label class="dvp-file">
          <input
            ref="fileInput"
            type="file"
            :accept="ACCEPT"
            :disabled="busy"
            @change="onFileChange"
          />
          <span>{{ busy ? '解析中…' : '选择载体文件（GenBank / FASTA / SnapGene .dna）' }}</span>
        </label>
        <p class="dvp-hint">
          解析结果只在本次设计里使用，<strong>不会写进共享载体库</strong>，也不需要管理员权限。
        </p>
      </div>

      <!-- 在线导入 -->
      <div v-else class="dvp-panel">
        <div class="dvp-tabs">
          <button
            type="button"
            class="dvp-tab"
            :class="{ active: onlineSource === 'ncbi' }"
            @click="onlineSource = 'ncbi'"
          >NCBI</button>
          <button
            type="button"
            class="dvp-tab"
            :class="{ active: onlineSource === 'ensembl' }"
            @click="onlineSource = 'ensembl'"
          >Ensembl</button>
          <button
            v-if="snapgeneUsable"
            type="button"
            class="dvp-tab"
            :class="{ active: onlineSource === 'snapgene' }"
            @click="onlineSource = 'snapgene'"
          >SnapGene 在线库</button>
          <button
            v-if="isAdmin"
            type="button"
            class="dvp-tab"
            :class="{ active: onlineSource === 'addgene' }"
            @click="onlineSource = 'addgene'"
          >Addgene</button>
        </div>

        <div v-if="onlineSource === 'ncbi'" class="dvp-row">
          <input
            v-model="ncbiAccession"
            class="dvp-input"
            placeholder="NCBI accession，如 L09137 / M77789.1"
            @keydown.enter.prevent="importOnline"
          />
          <button type="button" class="dvp-btn" :disabled="busy" @click="importOnline">
            {{ busy ? '导入中…' : '导入' }}
          </button>
        </div>

        <div v-else-if="onlineSource === 'ensembl'" class="dvp-row">
          <input
            v-model="ensemblId"
            class="dvp-input"
            placeholder="Ensembl ID，如 ENSG00000139618"
            @keydown.enter.prevent="importOnline"
          />
          <select v-model="ensemblType" class="dvp-select">
            <option value="cds">CDS</option>
            <option value="cdna">cDNA</option>
            <option value="genomic">genomic</option>
          </select>
          <button type="button" class="dvp-btn" :disabled="busy" @click="importOnline">
            {{ busy ? '导入中…' : '导入' }}
          </button>
        </div>

        <div v-else-if="onlineSource === 'addgene'" class="dvp-row">
          <input
            v-model="addgeneId"
            class="dvp-input"
            placeholder="Addgene 质粒编号，如 12259"
            @keydown.enter.prevent="importOnline"
          />
          <button type="button" class="dvp-btn" :disabled="busy" @click="importOnline">
            {{ busy ? '导入中…' : '导入' }}
          </button>
        </div>

        <div v-else class="dvp-online-snapgene">
          <div class="dvp-row">
            <input
              v-model="sgQuery"
              class="dvp-input"
              placeholder="按质粒名搜索，如 pET-30a"
              @keydown.enter.prevent="searchSnapgene"
            />
            <button type="button" class="dvp-btn" :disabled="busy" @click="searchSnapgene">
              {{ busy ? '搜索中…' : '搜索' }}
            </button>
          </div>
          <ul v-if="sgRows.length" class="dvp-results">
            <li v-for="row in sgRows" :key="row.set_id + '/' + row.plasmid_id">
              <button type="button" class="dvp-result" :disabled="busy" @click="pickSnapgene(row)">
                <span class="dvp-result-name">{{ row.name || row.filename }}</span>
                <span class="dvp-result-set">{{ row.set_name }}</span>
              </button>
            </li>
          </ul>
          <p v-else-if="sgSearched && !busy" class="dvp-hint">没有匹配的序列，换个名字再试。</p>
        </div>

        <p v-if="!loggedIn && onlineSource !== 'ncbi' && onlineSource !== 'ensembl'" class="dvp-hint">
          SnapGene 在线库需登录后使用。
        </p>
        <p v-if="sourceMeta" class="dvp-license">{{ sourceMeta.license }}</p>
      </div>
    </template>

    <p v-if="error" class="dvp-error">{{ error }}</p>
  </div>
</template>

<style scoped>
.dvp-panel { display: flex; flex-direction: column; gap: 0.5rem; }
.dvp-file {
  display: inline-flex; align-items: center; gap: 0.5rem; cursor: pointer;
  padding: 0.6rem 0.9rem; border: 2px dashed var(--border-color); border-radius: 0.5rem;
  color: var(--primary-color); font-size: 0.9rem;
}
.dvp-file:hover { border-color: var(--primary-color); background: var(--bg-secondary); }
.dvp-file input { display: none; }
.dvp-hint { font-size: 0.75rem; color: var(--text-secondary); }
.dvp-tabs { display: inline-flex; border: 1px solid var(--border-color); border-radius: 0.5rem; overflow: hidden; width: fit-content; }
.dvp-tab {
  padding: 0.35rem 0.8rem; border: none; background: white; cursor: pointer;
  font-size: 0.8rem;
}
.dvp-tab:not(:last-child) { border-right: 1px solid var(--border-color); }
.dvp-tab.active { background: var(--primary-color); color: white; }
.dvp-row { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.dvp-input {
  flex: 1 1 16rem; padding: 0.5rem 0.7rem; border: 1px solid var(--border-color);
  border-radius: 0.4rem; font-size: 0.875rem;
}
.dvp-select { padding: 0.5rem; border: 1px solid var(--border-color); border-radius: 0.4rem; }
.dvp-btn {
  padding: 0.5rem 1rem; border: none; border-radius: 0.4rem; cursor: pointer;
  background: var(--primary-color); color: white; font-size: 0.875rem;
}
.dvp-btn:disabled { opacity: 0.6; cursor: default; }
.dvp-results { list-style: none; margin: 0; padding: 0; max-height: 14rem; overflow-y: auto; }
.dvp-result {
  display: flex; justify-content: space-between; gap: 1rem; width: 100%;
  padding: 0.4rem 0.6rem; border: none; background: none; cursor: pointer;
  text-align: left; font-size: 0.85rem; border-radius: 0.3rem;
}
.dvp-result:hover { background: var(--bg-secondary); }
.dvp-result-set { color: var(--text-secondary); font-size: 0.75rem; }
.dvp-card { border: 1px solid var(--border-color); border-radius: 0.5rem; padding: 0.7rem 0.9rem; }
.dvp-card-head { display: flex; align-items: center; gap: 0.6rem; }
.dvp-name { font-size: 0.95rem; }
.dvp-badge {
  font-size: 0.7rem; padding: 0.1rem 0.45rem; border-radius: 999px;
  background: var(--bg-secondary); color: var(--text-secondary);
}
.dvp-link {
  margin-left: auto; border: none; background: none; cursor: pointer;
  color: var(--primary-color); font-size: 0.8rem;
}
.dvp-meta { font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.35rem; }
.dvp-warn { margin: 0.4rem 0 0; padding-left: 1rem; font-size: 0.75rem; color: #b45309; }
.dvp-license { margin-top: 0.4rem; font-size: 0.7rem; color: var(--text-secondary); }
.dvp-error { margin-top: 0.4rem; font-size: 0.8rem; color: var(--error-color); }
</style>