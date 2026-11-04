<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getCacheInfo, clearCache, invalidateDesignCache, invalidateVectorCache, getCacheHealth } from '@/api'

const cacheInfo = ref<any>(null)
const loading = ref(true)
const clearing = ref(false)
const invalidateId = ref('')
const invalidateType = ref<'design' | 'vector'>('design')
const invalidateLoading = ref(false)
const cacheHealth = ref<any>(null)

const message = ref('')

async function handleInvalidate() {
  if (!invalidateId.value.trim()) return
  try {
    invalidateLoading.value = true
    if (invalidateType.value === 'design') {
      await invalidateDesignCache(invalidateId.value)
    } else {
      await invalidateVectorCache(invalidateId.value)
    }
    message.value = invalidateType.value === 'design' ? '设计缓存已失效' : '载体缓存已失效'
    invalidateId.value = ''
  } catch (e: any) {
    message.value = '失效操作失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    invalidateLoading.value = false
  }
}

async function loadCacheHealth() {
  try {
    cacheHealth.value = await getCacheHealth()
  } catch (e) {
    console.warn('Failed to load cache health:', e)
  }
}

async function loadCacheInfo() {
  try {
    loading.value = true
    cacheInfo.value = await getCacheInfo()
  } catch (e: any) {
    message.value = `获取缓存信息失败: ${e.response?.data?.detail || e.message}`
  } finally {
    loading.value = false
  }
}

async function handleClearCache() {
  if (!confirm('确定要清除所有缓存吗？')) return
  try {
    clearing.value = true
    await clearCache()
    message.value = '缓存已清除'
    await loadCacheInfo()
    await loadCacheHealth()
  } catch (e: any) {
    message.value = `清除失败: ${e.response?.data?.detail || e.message}`
  } finally {
    clearing.value = false
  }
}

onMounted(() => {
  loadCacheInfo()
  loadCacheHealth()
})
</script>

<template>
  <div class="cache-page">
    <h1>缓存管理</h1>
    <p class="subtitle">查看和管理后端缓存状态</p>

    <div v-if="loading" class="loading">
      <div class="spinner"></div>
    </div>

    <div v-else-if="cacheInfo" class="cache-info">
      <div class="info-cards">
        <div class="info-card">
          <span class="info-value">{{ cacheInfo.enabled ? '✅ 启用' : '❌ 禁用' }}</span>
          <span class="info-label">缓存状态</span>
        </div>
        <div class="info-card">
          <span class="info-value">{{ cacheInfo.backend || '-' }}</span>
          <span class="info-label">后端类型</span>
        </div>
        <div v-if="cacheInfo.url" class="info-card">
          <span class="info-value mono">{{ cacheInfo.url }}</span>
          <span class="info-label">连接地址</span>
        </div>
      </div>

      <!-- 精确缓存失效 -->
  <div class="invalidate-section">
    <h3>精确缓存失效</h3>
    <div class="invalidate-form">
      <select v-model="invalidateType" class="form-select small">
        <option value="design">设计缓存</option>
        <option value="vector">载体缓存</option>
      </select>
      <input v-model="invalidateId" class="form-input" placeholder="输入 ID..." />
      <button class="btn btn-secondary" :disabled="!invalidateId.trim() || invalidateLoading" @click="handleInvalidate">
        {{ invalidateLoading ? '失效中...' : '🗑️ 失效' }}
      </button>
    </div>
  </div>

  <!-- 缓存健康状态 -->
  <div v-if="cacheHealth" class="health-section">
    <h3>健康状态</h3>
    <span :class="['health-badge', cacheHealth.status]">{{ cacheHealth.status }}</span>
    <span class="health-backend">后端: {{ cacheHealth.backend }}</span>
  </div>

  <div class="actions">
        <button class="btn btn-secondary" @click="loadCacheInfo">🔄 刷新</button>
        <button class="btn btn-danger" :disabled="clearing" @click="handleClearCache">
          {{ clearing ? '清除中...' : '🗑️ 清除缓存' }}
        </button>
      </div>
    </div>

    <div v-if="message" :class="['message', message.includes('失败') ? 'error' : 'success']">
      {{ message }}
    </div>
  </div>
</template>

<style scoped>
.cache-page { max-width: 800px; margin: 0 auto; }
h1 { font-size: 2rem; margin-bottom: 0.25rem; }
.subtitle { color: var(--text-secondary); margin-bottom: 2rem; }
.info-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
.info-card { text-align: center; padding: 1.5rem; background: white; border-radius: 12px; box-shadow: var(--shadow); }
.info-value { display: block; font-size: 1.125rem; font-weight: 600; color: var(--primary-color); }
.info-label { display: block; font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.5rem; }
.mono { font-family: monospace; font-size: 0.875rem !important; word-break: break-all; }
.actions { display: flex; gap: 0.75rem; }
.btn-danger { background: #ef4444; color: white; border: none; }
.btn-danger:hover { background: #dc2626; }
.message { padding: 0.75rem 1rem; border-radius: 8px; margin-top: 1rem; font-size: 0.875rem; }
.message.success { background: #dcfce7; color: #166534; }
.message.error { background: #fee2e2; color: #991b1b; }
.loading { display: flex; justify-content: center; padding: 3rem; }
.invalidate-section { background: white; padding: 1.5rem; border-radius: 12px; box-shadow: var(--shadow); margin-bottom: 1rem; }
.invalidate-section h3 { font-size: 1rem; margin-bottom: 0.75rem; }
.invalidate-form { display: flex; gap: 0.75rem; align-items: center; }
.invalidate-form .form-input { flex: 1; }
.invalidate-form .form-select.small { width: auto; min-width: 120px; }
.health-section { background: white; padding: 1rem 1.5rem; border-radius: 12px; box-shadow: var(--shadow); margin-bottom: 1rem; display: flex; align-items: center; gap: 1rem; }
.health-section h3 { font-size: 1rem; margin: 0; }
.health-badge { padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; }
.health-badge.healthy { background: #dcfce7; color: #166534; }
.health-badge.degraded { background: #fef3c7; color: #92400e; }
.health-backend { font-size: 0.875rem; color: var(--text-secondary); }
</style>
