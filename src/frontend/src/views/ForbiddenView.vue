<script setup lang="ts">
/** 功能未开放 — 管理员未对当前用户组开放该功能时的落地页 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const FEATURE_LABELS: Record<string, string> = {
  design: '引物/序列设计',
  batch: '批量设计',
  vectors: '载体库',
  sequencing: '测序分析',
  analysis: '序列工具',
  codon: '密码子表'
}

const label = computed(() => FEATURE_LABELS[String(route.query.feature)] || '该功能')
const loggedIn = computed(() => auth.isAuthenticated)
</script>

<template>
  <div class="forbidden-page">
    <div class="card">
      <div class="icon">🔒</div>
      <h2>{{ label }}未开放</h2>
      <p v-if="!loggedIn">管理员未对未登录访客开放「{{ label }}」。
        <a class="link" @click="router.push('/')">登录</a>
        后可能可以使用，或联系管理员开通。
      </p>
      <p v-else>管理员未对普通账号开放「{{ label }}」，如有需要请联系管理员。</p>
      <button class="btn" @click="router.push('/')">返回首页</button>
    </div>
  </div>
</template>

<style scoped>
.forbidden-page {
  display: flex;
  justify-content: center;
  padding-top: 4rem;
}

.card {
  text-align: center;
  max-width: 420px;
  padding: 2.5rem;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 12px;
  background: #fff;
}

.icon {
  font-size: 2.5rem;
  margin-bottom: 0.5rem;
}

h2 {
  margin: 0 0 0.75rem;
}

p {
  color: #666;
  font-size: 0.9rem;
  line-height: 1.6;
}

.link {
  color: var(--primary-color, #4f46e5);
  cursor: pointer;
  text-decoration: underline;
}

.btn {
  margin-top: 1rem;
  padding: 0.55rem 1.4rem;
  border: none;
  border-radius: 6px;
  background: var(--primary-color, #4f46e5);
  color: #fff;
  cursor: pointer;
}

.btn:hover {
  opacity: 0.9;
}
</style>
