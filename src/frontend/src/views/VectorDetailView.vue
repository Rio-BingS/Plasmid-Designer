<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { VectorInfo } from '@/types'
import PlasmidMap from '@/components/PlasmidMap.vue'
import type { PlasmidFeature, EnzymeSite } from '@/components/PlasmidMap.vue'
import SequenceView from '@/components/SequenceView.vue'
import { getVectorMapData, getVectorSequence, getVector } from '@/api'

interface MapData {
  name: string
  length: number
  sequence?: string
  features: PlasmidFeature[]
  enzyme_sites?: EnzymeSite[]
}

const route = useRoute()
const router = useRouter()
const mapData = ref<MapData | null>(null)
const loading = ref(true)
const error = ref('')
const selectedFeature = ref<PlasmidFeature | null>(null)
const activeTab = ref<'map' | 'sequence' | 'features'>('map')
const seqHighlight = ref<{ start: number; end: number } | null>(null)
const sequenceViewRef = ref<InstanceType<typeof SequenceView> | null>(null)
const mapRef = ref<InstanceType<typeof PlasmidMap> | null>(null)

const vectorId = computed(() => route.params.id as string)

function goToSequencing() {
  router.push({ path: '/sequencing', query: { mode: 'vector', ref: vectorId.value } })
}

async function loadVectorMap() {
  try {
    loading.value = true
    const data = await getVectorMapData(vectorId.value)
    mapData.value = data
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message || 'Failed to load vector data'
  } finally {
    loading.value = false
  }
}

function formatSequence(seq: string | undefined, lineLength: number = 60): string {
  if (!seq) return ''
  const lines = []
  for (let i = 0; i < seq.length; i += lineLength) {
    const line = seq.slice(i, i + lineLength)
    const groups = line.match(/.{1,10}/g) || []
    lines.push(`${(i + 1).toString().padStart(5, ' ')}  ${groups.join(' ')}`)
  }
  return lines.join('\n')
}

function selectFeature(feature: PlasmidFeature) {
  selectedFeature.value = selectedFeature.value === feature ? null : feature
}

// 环形图点击特征 → 线性视图定位 + 高亮区间
function onMapFeatureSelect(feature: PlasmidFeature | null) {
  selectedFeature.value = feature
  if (feature) {
    seqHighlight.value = { start: feature.start, end: feature.end }
    sequenceViewRef.value?.scrollTo(feature.start)
  } else {
    seqHighlight.value = null
  }
}

// 点击酶切位点 → 定位并高亮识别序列（按 cut 位置回找识别位点）
function onEnzymeClick(site: EnzymeSite) {
  const pos = site.cut_fwd
  const siteLen = 6
  seqHighlight.value = { start: Math.max(1, pos - siteLen), end: pos }
  sequenceViewRef.value?.scrollTo(Math.max(1, pos - siteLen))
}

const vectorInfo = ref<VectorInfo | null>(null)

async function loadVectorInfo() {
  try {
    vectorInfo.value = await getVector(vectorId.value)
  } catch (e) {
    console.warn('Failed to load vector info:', e)
  }
}

onMounted(() => {
  loadVectorMap()
  loadVectorInfo()
})
function copySequence() {
  navigator.clipboard.writeText(mapData.value?.sequence || "")
}

async function downloadSeq(format: string) {
  try {
    const data = await getVectorSequence(vectorId.value, format)
    const ext = format === 'genbank' ? 'gb' : 'fasta'
    const blob = new Blob([data], { type: 'text/plain' })
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', `${vectorId.value}.${ext}`)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch (e: any) {
    alert('下载失败: ' + (e.message || '未知错误'))
  }
}
</script>

<template>
  <div class="vector-detail-page">
    <div v-if="loading" class="loading">
      <div class="spinner"></div>
      <p>加载中...</p>
    </div>

    <div v-else-if="error" class="error-state">
      <p>{{ error }}</p>
      <router-link to="/vectors" class="btn btn-secondary">返回载体库</router-link>
    </div>

    <div v-else-if="mapData" class="content">
      <!-- 头部信息 -->
      <div class="header">
        <router-link to="/vectors" class="back-link">← 返回载体库</router-link>
        <div class="header-row">
          <h1>{{ mapData.name }}</h1>
          <button class="btn btn-secondary seq-link-btn" @click="goToSequencing">🔬 测序验证</button>
        </div>
        <p class="subtitle">{{ mapData.length.toLocaleString() }} bp</p>
      </div>

      <!-- 标签页 -->
      <div class="tabs">
  <button
    :class="['tab', { active: activeTab === 'map' }]"
    @click="activeTab = 'map'"
  >
    🗺️ 质粒图谱
  </button>
  <button
    :class="['tab', { active: activeTab === 'features' }]"
    @click="activeTab = 'features'"
  >
    📋 元件列表
  </button>
  <button
    :class="['tab', { active: activeTab === 'sequence' }]"
    @click="activeTab = 'sequence'"
  >
    🧬 序列
  </button>
</div>

      <!-- 图谱视图 -->
      <div v-if="activeTab === 'map'" class="map-view">
        <div class="map-column">
          <div class="map-container">
            <PlasmidMap
              ref="mapRef"
              :name="mapData.name"
              :length="mapData.length"
              :features="mapData.features"
              :enzyme-sites="mapData.enzyme_sites || []"
              :sequence="mapData.sequence"
              :width="620"
              :height="620"
              @feature-select="onMapFeatureSelect"
              @enzyme-click="onEnzymeClick"
            />
          </div>

          <!-- SnapGene 风格线性序列视图（与环形图联动） -->
          <div v-if="mapData.sequence" class="seq-view-container">
            <SequenceView
              ref="sequenceViewRef"
              :sequence="mapData.sequence"
              :features="mapData.features"
              :enzyme-sites="mapData.enzyme_sites || []"
              :highlight="seqHighlight"
              :height="300"
            />
          </div>
        </div>
        
        <!-- 特征列表（侧边） -->
        <div class="features-sidebar">
          <h3>元件信息</h3>
          <div v-if="selectedFeature" class="selected-feature">
            <h4>{{ selectedFeature.name }}</h4>
            <div class="feature-details">
              <p><strong>类型:</strong> {{ selectedFeature.type }}</p>
              <p><strong>位置:</strong> {{ selectedFeature.start }} - {{ selectedFeature.end }} bp</p>
              <p><strong>长度:</strong> {{ selectedFeature.end - selectedFeature.start + 1 }} bp</p>
              <p><strong>方向:</strong> {{ selectedFeature.strand === '+' ? '正向' : '反向' }}</p>
              <p v-if="selectedFeature.description"><strong>描述:</strong> {{ selectedFeature.description }}</p>
            </div>
          </div>
          <div v-else class="hint">
            <p>点击图谱上的元件查看详情</p>
          </div>
        </div>
      </div>

      <!-- 元件列表视图 -->
      <div v-if="activeTab === 'features'" class="features-view">
        <div v-if="mapData.features.length === 0" class="empty-state">
          <p>该载体暂无元件注释</p>
        </div>
        <table v-else class="features-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>起始</th>
              <th>终止</th>
              <th>长度</th>
              <th>方向</th>
            </tr>
          </thead>
          <tbody>
            <tr 
              v-for="feature in mapData.features" 
              :key="feature.name + feature.start"
              @click="selectFeature(feature)"
              :class="{ selected: selectedFeature === feature }"
            >
              <td>{{ feature.name }}</td>
              <td>
                <span class="type-badge" :style="{ backgroundColor: `var(--color-${feature.type}, #ccc)` }">
                  {{ feature.type }}
                </span>
              </td>
              <td>{{ feature.start.toLocaleString() }}</td>
              <td>{{ feature.end.toLocaleString() }}</td>
              <td>{{ (feature.end - feature.start + 1).toLocaleString() }} bp</td>
              <td>{{ feature.strand === '+' ? '→' : '←' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 序列视图 -->
      <div v-if="activeTab === 'sequence'" class="sequence-view">
        <div v-if="!mapData.sequence" class="empty-state">
          <p>该载体暂无序列数据</p>
        </div>
        <div v-else>
          <div class="sequence-header">
            <span>长度: {{ mapData.sequence.length.toLocaleString() }} bp</span>
          <button class="btn btn-small" @click="copySequence">📋 复制</button>
          <button class="btn btn-small" @click="downloadSeq('fasta')">🧬 FASTA</button>
          <button class="btn btn-small" @click="downloadSeq('genbank')">📄 GenBank</button>
          </div>
          <pre class="sequence-display">{{ formatSequence(mapData.sequence) }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vector-detail-page {
  max-width: 1200px;
  margin: 0 auto;
}

.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 400px;
  gap: 1rem;
}

.error-state {
  text-align: center;
  padding: 3rem;
}

.header {
  margin-bottom: 2rem;
}

.header-row {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.seq-link-btn {
  white-space: nowrap;
}

.back-link {
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 0.875rem;
}

.back-link:hover {
  color: var(--primary-color);
}

.header h1 {
  font-size: 2rem;
  margin: 0.5rem 0;
}

.subtitle {
  color: var(--text-secondary);
}

.tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 0.5rem;
}

.tab {
  padding: 0.75rem 1.5rem;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.95rem;
  color: var(--text-secondary);
  border-radius: 6px 6px 0 0;
  transition: all 0.2s;
}

.tab:hover {
  background: var(--bg-secondary);
}

.tab.active {
  background: var(--primary-color);
  color: white;
}

.map-view {
  display: grid;
  grid-template-columns: 1fr 300px;
  gap: 2rem;
}

.map-container {
  display: flex;
  justify-content: center;
  padding: 1rem;
  background: white;
  border-radius: 12px;
  box-shadow: var(--shadow);
}

.features-sidebar {
  background: white;
  padding: 1.5rem;
  border-radius: 12px;
  box-shadow: var(--shadow);
}

.features-sidebar h3 {
  font-size: 1rem;
  margin-bottom: 1rem;
}

.selected-feature h4 {
  font-size: 1.125rem;
  color: var(--primary-color);
  margin-bottom: 0.75rem;
}

.feature-details p {
  font-size: 0.875rem;
  margin-bottom: 0.5rem;
  color: var(--text-secondary);
}

.hint {
  color: var(--text-secondary);
  font-size: 0.875rem;
  font-style: italic;
}

.features-view {
  background: white;
  border-radius: 12px;
  box-shadow: var(--shadow);
  overflow: hidden;
}

.features-table {
  width: 100%;
  border-collapse: collapse;
}

.features-table th,
.features-table td {
  padding: 0.75rem 1rem;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}

.features-table th {
  background: var(--bg-secondary);
  font-weight: 600;
}

.features-table tr:hover {
  background: var(--bg-secondary);
}

.features-table tr.selected {
  background: rgba(79, 70, 229, 0.1);
}

.type-badge {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.75rem;
  color: white;
}

.sequence-view {
  background: white;
  border-radius: 12px;
  box-shadow: var(--shadow);
  padding: 1.5rem;
}

.sequence-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border-color);
}

.sequence-display {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 12px;
  line-height: 1.6;
  background: var(--bg-secondary);
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
  white-space: pre;
}

.empty-state {
  text-align: center;
  padding: 3rem;
  color: var(--text-secondary);
}

.map-column {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.seq-view-container {
  background: white;
  border-radius: 12px;
  box-shadow: var(--shadow);
  padding: 0.75rem;
}

@media (max-width: 768px) {
  .map-view {
    grid-template-columns: 1fr;
  }
}
.vector-meta { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.5rem; align-items: center; }
.meta-tag { padding: 0.25rem 0.75rem; background: var(--bg-secondary); border-radius: 9999px; font-size: 0.75rem; color: var(--text-secondary); }
.vector-description { width: 100%; font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem; line-height: 1.5; }
</style>
