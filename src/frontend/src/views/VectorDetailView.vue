<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { VectorInfo } from '@/types'
import type { MapDocument } from '@/map/types'
import { getVectorMap } from '@/map/api'
import MapViewer from '@/map/components/MapViewer.vue'
import { getVectorSequence, getVector, formatApiError } from '@/api'
import { downloadTextFile } from '@/utils/download'

const route = useRoute()
const router = useRouter()
const mapDoc = ref<MapDocument | null>(null)
const loading = ref(true)
const error = ref('')
const vectorInfo = ref<VectorInfo | null>(null)
const copied = ref(false)

const vectorId = computed(() => route.params.id as string)

function goToSequencing() {
  router.push({ path: '/sequencing', query: { mode: 'vector', ref: vectorId.value } })
}

async function loadVectorMap() {
  try {
    loading.value = true
    error.value = ''
    mapDoc.value = await getVectorMap(vectorId.value)
  } catch (e: any) {
    error.value = formatApiError(e, '载体数据加载失败')
  } finally {
    loading.value = false
  }
}

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

// 路由参数变化时组件会被复用（页内从载体 A 导航到 B），必须重载
watch(vectorId, () => {
  loadVectorMap()
  loadVectorInfo()
})

async function copySequence() {
  try {
    await navigator.clipboard.writeText(mapDoc.value?.sequence || '')
    copied.value = true
    setTimeout(() => { copied.value = false }, 1200)
  } catch { /* 剪贴板不可用时静默 */ }
}

async function downloadSeq(format: string) {
  try {
    const data = await getVectorSequence(vectorId.value, format)
    const ext = format === 'genbank' ? 'gb' : 'fasta'
    downloadTextFile(data, `${vectorId.value}.${ext}`)
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

    <div v-else-if="mapDoc" class="content">
      <div class="header">
        <router-link to="/vectors" class="back-link">← 返回载体库</router-link>
        <div class="header-row">
          <h1>{{ mapDoc.name }}</h1>
          <button class="btn btn-secondary seq-link-btn" @click="goToSequencing">🔬 测序验证</button>
        </div>
        <p class="subtitle">{{ mapDoc.length.toLocaleString() }} bp · {{ mapDoc.topology === 'circular' ? '环状' : '线性' }} · {{ mapDoc.features.length }} 个元件</p>
        <div v-if="vectorInfo" class="vector-meta">
          <span v-if="vectorInfo.vector_type" class="meta-tag">{{ vectorInfo.vector_type }}</span>
          <span v-for="h in vectorInfo.host" :key="'h' + h" class="meta-tag">{{ h }}</span>
          <span v-for="a in vectorInfo.antibiotic_resistance" :key="'a' + a" class="meta-tag">{{ a }}</span>
          <span v-if="vectorInfo.description" class="vector-description">{{ vectorInfo.description }}</span>
        </div>
        <div class="seq-actions">
          <button class="btn btn-small" @click="copySequence">{{ copied ? '✓ 已复制' : '📋 复制序列' }}</button>
          <button class="btn btn-small" @click="downloadSeq('fasta')">🧬 FASTA</button>
          <button class="btn btn-small" @click="downloadSeq('genbank')">📄 GenBank</button>
        </div>
      </div>

      <MapViewer :doc="mapDoc" />
    </div>
  </div>
</template>

<style scoped>
.vector-detail-page { max-width: 1280px; margin: 0 auto; }
.loading { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 400px; gap: 1rem; }
.error-state { text-align: center; padding: 3rem; }
.header { margin-bottom: 1.25rem; }
.header-row { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
.seq-link-btn { white-space: nowrap; }
.back-link { color: var(--text-secondary); text-decoration: none; font-size: 0.875rem; }
.back-link:hover { color: var(--primary-color); }
.header h1 { font-size: 2rem; margin: 0.5rem 0; }
.subtitle { color: var(--text-secondary); }
.seq-actions { display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap; }
.btn-small { padding: 0.35rem 0.8rem; font-size: 0.85rem; background: var(--bg-primary); border: 1px solid var(--border-color); }
.vector-meta { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.5rem; align-items: center; }
.meta-tag { padding: 0.25rem 0.75rem; background: var(--bg-secondary); border-radius: 9999px; font-size: 0.75rem; color: var(--text-secondary); }
.vector-description { width: 100%; font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem; line-height: 1.5; }
@media (max-width: 640px) { .header h1 { font-size: 1.5rem; } }
</style>
