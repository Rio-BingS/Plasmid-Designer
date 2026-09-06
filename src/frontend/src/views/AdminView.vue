<script setup lang="ts">
/**
 * 站点管理（仅管理员）
 * - 站点设置：注册开关、注册邮箱验证开关、各层级功能开关
 * - 用户管理：角色/状态/验证标记、删除
 * 发信渠道（MAIL_PROVIDER）在服务端 .env 配置，此处只读展示
 */
import { ref, onMounted, computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import {
  getAdminSettings, updateAdminSettings, listAdminUsers,
  updateAdminUser, deleteAdminUser,
  type AdminSettings, type AdminUserInfo
} from '@/api'

const authStore = useAuthStore()

const settings = ref<AdminSettings | null>(null)
const users = ref<AdminUserInfo[]>([])
const loading = ref(false)
const savingSettings = ref(false)
const message = ref('')
const error = ref('')
const currentUserId = ref('')

// 草稿状态（进入页面/保存后与服务器同步；改动后手动点保存）
const draft = ref({
  registration_open: true,
  email_verification_required: false,
  anonymous_features: [] as string[],
  user_features: [] as string[]
})

const featureKeys = computed(() => settings.value?.feature_keys ?? [])
const featureLabels = computed(() => settings.value?.feature_labels ?? {})

function tierLabel(tier: 'anonymous' | 'user'): string {
  return tier === 'anonymous' ? '未登录访客' : '普通登录用户'
}

function toggleFeature(tier: 'anonymous' | 'user', key: string) {
  const list = tier === 'anonymous' ? draft.value.anonymous_features : draft.value.user_features
  const idx = list.indexOf(key)
  if (idx >= 0) list.splice(idx, 1)
  else list.push(key)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    settings.value = await getAdminSettings()
    draft.value = {
      registration_open: settings.value.registration_open,
      email_verification_required: settings.value.email_verification_required,
      anonymous_features: [...settings.value.anonymous_features],
      user_features: [...settings.value.user_features]
    }
    users.value = await listAdminUsers()
    currentUserId.value = authStore.user?.id || ''
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function saveSettings() {
  savingSettings.value = true
  message.value = ''
  error.value = ''
  try {
    const saved = await updateAdminSettings({
      registration_open: draft.value.registration_open,
      email_verification_required: draft.value.email_verification_required,
      anonymous_features: [...draft.value.anonymous_features],
      user_features: [...draft.value.user_features]
    })
    settings.value = saved
    draft.value = {
      registration_open: saved.registration_open,
      email_verification_required: saved.email_verification_required,
      anonymous_features: [...saved.anonymous_features],
      user_features: [...saved.user_features]
    }
    message.value = '设置已保存，立即对全站生效'
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '保存失败'
  } finally {
    savingSettings.value = false
  }
}

async function setUserFlag(u: AdminUserInfo, patch: Record<string, boolean>) {
  error.value = ''
  try {
    const updated = await updateAdminUser(u.id, patch)
    users.value = users.value.map((x) => (x.id === u.id ? updated : x))
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '操作失败'
    await load() // 回滚到服务器状态
  }
}

async function removeUser(u: AdminUserInfo) {
  if (!confirm(`确定删除用户 ${u.email}？其设计记录将保留但不再关联账号。`)) return
  error.value = ''
  try {
    await deleteAdminUser(u.id)
    users.value = users.value.filter((x) => x.id !== u.id)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '删除失败'
  }
}

function fmtTime(iso: string | null): string {
  return (iso || '').replace('T', ' ').slice(0, 16)
}

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <h1>站点管理</h1>
    <p class="page-sub">对全站立即生效的注册/功能开关与用户管理。发信渠道在服务端 .env 配置。</p>

    <div v-if="error" class="msg error">{{ error }}</div>
    <div v-if="message" class="msg ok">{{ message }}</div>
    <div v-if="loading && !settings" class="loading">加载中…</div>

    <template v-if="settings">
      <!-- ==================== 站点设置 ==================== -->
      <section class="panel">
        <h2>站点设置</h2>

        <div class="switch-row">
          <div>
            <div class="switch-title">开放注册</div>
            <div class="switch-desc">关闭后新用户无法注册，已有账号不受影响</div>
          </div>
          <label class="switch">
            <input v-model="draft.registration_open" type="checkbox" />
            <span class="slider"></span>
          </label>
        </div>

        <div class="switch-row">
          <div>
            <div class="switch-title">注册需要邮箱验证</div>
            <div class="switch-desc">
              开启后注册需输入邮箱收到的 6 位验证码；发信渠道：
              <code>{{ settings.mail_provider }}</code>（.env 的 MAIL_PROVIDER，
              可选 console / smtp / resend / brevo）
            </div>
          </div>
          <label class="switch">
            <input v-model="draft.email_verification_required" type="checkbox" />
            <span class="slider"></span>
          </label>
        </div>

        <h3>功能开放范围</h3>
        <p class="hint">控制各级用户能看到与使用的功能（后端 API 同步拦截，管理员始终全量可用）</p>
        <table class="feature-table">
          <thead>
            <tr>
              <th>功能</th>
              <th v-for="tier in (['anonymous', 'user'] as const)" :key="tier">{{ tierLabel(tier) }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="key in featureKeys" :key="key">
              <td>{{ featureLabels[key] || key }} <code class="key">{{ key }}</code></td>
              <td v-for="tier in (['anonymous', 'user'] as const)" :key="tier">
                <input
                  type="checkbox"
                  class="feature-check"
                  :checked="(tier === 'anonymous' ? draft.anonymous_features : draft.user_features).includes(key)"
                  @change="toggleFeature(tier, key)"
                />
              </td>
            </tr>
          </tbody>
        </table>

        <button class="btn primary" :disabled="savingSettings" @click="saveSettings">
          {{ savingSettings ? '保存中…' : '保存设置' }}
        </button>
      </section>

      <!-- ==================== 用户管理 ==================== -->
      <section class="panel">
        <h2>用户管理（{{ users.length }}）</h2>
        <table class="user-table">
          <thead>
            <tr>
              <th>用户</th>
              <th>注册时间</th>
              <th>管理员</th>
              <th>启用</th>
              <th>邮箱验证</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td>
                <div class="user-name">{{ u.username }} <span v-if="u.id === currentUserId" class="self-tag">（我）</span></div>
                <div class="user-email">{{ u.email }}</div>
              </td>
              <td>{{ fmtTime(u.created_at) }}</td>
              <td><input type="checkbox" :checked="u.is_admin" @change="setUserFlag(u, { is_admin: !u.is_admin })" /></td>
              <td><input type="checkbox" :checked="u.is_active" @change="setUserFlag(u, { is_active: !u.is_active })" /></td>
              <td><input type="checkbox" :checked="u.email_verified" @change="setUserFlag(u, { email_verified: !u.email_verified })" /></td>
              <td>
                <button class="btn danger small" :disabled="u.id === currentUserId" @click="removeUser(u)">
                  删除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="hint">保护规则：不能禁用/删除自己的账号；不能移除最后一位管理员。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.admin-page {
  max-width: 900px;
  margin: 0 auto;
}

h1 {
  font-size: 1.6rem;
  margin-bottom: 0.25rem;
}

.page-sub {
  color: #666;
  font-size: 0.875rem;
  margin-bottom: 1.5rem;
}

.panel {
  background: #fff;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}

.panel h2 {
  font-size: 1.15rem;
  margin: 0 0 1rem;
}

.panel h3 {
  font-size: 1rem;
  margin: 1.25rem 0 0.25rem;
}

.switch-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  padding: 0.85rem 0;
  border-bottom: 1px dashed var(--border-color, #eee);
}

.switch-title {
  font-weight: 600;
}

.switch-desc {
  color: #666;
  font-size: 0.8125rem;
  margin-top: 0.25rem;
}

.switch {
  position: relative;
  width: 44px;
  height: 24px;
  flex-shrink: 0;
}

.switch input {
  opacity: 0;
  width: 0;
  height: 0;
}

.slider {
  position: absolute;
  inset: 0;
  background: #ccc;
  border-radius: 24px;
  cursor: pointer;
  transition: 0.2s;
}

.slider:before {
  content: '';
  position: absolute;
  width: 18px;
  height: 18px;
  left: 3px;
  top: 3px;
  background: #fff;
  border-radius: 50%;
  transition: 0.2s;
}

.switch input:checked + .slider {
  background: var(--primary-color, #4f46e5);
}

.switch input:checked + .slider:before {
  transform: translateX(20px);
}

.feature-table,
.user-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 0.5rem;
  font-size: 0.875rem;
}

.feature-table th,
.feature-table td,
.user-table th,
.user-table td {
  padding: 0.55rem 0.6rem;
  border-bottom: 1px solid var(--border-color, #f0f0f0);
  text-align: left;
}

.feature-table .key {
  color: #999;
  font-size: 0.75rem;
  margin-left: 0.35rem;
}

.feature-check {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.user-name {
  font-weight: 600;
}

.self-tag {
  color: #4f46e5;
  font-size: 0.75rem;
}

.user-email {
  color: #666;
  font-size: 0.8125rem;
}

.btn {
  padding: 0.55rem 1.3rem;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.875rem;
}

.btn.primary {
  margin-top: 1.25rem;
  background: var(--primary-color, #4f46e5);
  color: #fff;
}

.btn.danger {
  background: #fee2e2;
  color: #dc2626;
  padding: 0.3rem 0.7rem;
}

.btn.danger:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn:disabled {
  opacity: 0.6;
}

.hint {
  color: #999;
  font-size: 0.8125rem;
}

.msg {
  padding: 0.7rem 1rem;
  border-radius: 8px;
  font-size: 0.875rem;
  margin-bottom: 1rem;
}

.msg.ok {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
}

.msg.error {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}

.loading {
  color: #999;
}
</style>
