<script setup lang="ts">
/**
 * 站点管理（仅管理员）
 * - 站点设置：注册开关、注册邮箱验证开关、各层级功能开关、各分组批量测序 read 上限
 * - 用户管理：角色/状态/验证标记、个人功能权限与 read 上限覆盖、删除
 * 发信渠道（MAIL_PROVIDER）在服务端 .env 配置，此处只读展示
 */
import { ref, onMounted, computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import {
  getAdminSettings, updateAdminSettings, listAdminUsers,
  updateAdminUser, deleteAdminUser,
  type AdminSettings, type AdminUserInfo, type LimitTier, type BatchReadLimitPair
} from '@/api'
import { parseLimitInput, limitToInput } from '@/utils/batchReadLimits'

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

// ==================== 批量测序 read 上限 ====================
type LimitField = keyof BatchReadLimitPair
const LIMIT_TIERS: LimitTier[] = ['anonymous', 'user', 'admin']
const LIMIT_FIELDS: LimitField[] = ['max_reads_per_group', 'max_total_reads']
const LIMIT_TIER_LABELS: Record<LimitTier, string> = { anonymous: '匿名访客', user: '普通用户', admin: '管理员' }
const LIMIT_FIELD_LABELS: Record<LimitField, string> = { max_reads_per_group: '每组最多 read 数', max_total_reads: '整批最多 read 数' }
const FALLBACK_DEFAULTS = { max_reads_per_group: 24, max_total_reads: 240 }
const FALLBACK_HARD_MAX = { max_reads_per_group: 384, max_total_reads: 5000 }

type LimitDraft = Record<LimitTier, Record<LimitField, string>>
const limitDraft = ref<LimitDraft>(emptyLimitDraft())

function emptyLimitDraft(): LimitDraft {
  return {
    anonymous: { max_reads_per_group: '', max_total_reads: '' },
    user: { max_reads_per_group: '', max_total_reads: '' },
    admin: { max_reads_per_group: '', max_total_reads: '' }
  }
}

function limitDraftFrom(s: AdminSettings): LimitDraft {
  const d = emptyLimitDraft()
  for (const t of LIMIT_TIERS) {
    for (const f of LIMIT_FIELDS) d[t][f] = limitToInput(s.batch_read_limits?.[t]?.[f])
  }
  return d
}

const limitDefaults = computed(() => settings.value?.batch_read_limit_defaults ?? FALLBACK_DEFAULTS)
const limitHardMax = computed(() => settings.value?.batch_read_limit_hard_max ?? FALLBACK_HARD_MAX)

/** 某分组当前生效值（已保存的分组设置，未设置则服务器默认） */
function tierLimit(tier: LimitTier, field: LimitField): number {
  return settings.value?.batch_read_limits?.[tier]?.[field] ?? limitDefaults.value[field]
}

function userTier(u: AdminUserInfo): LimitTier {
  return u.is_admin ? 'admin' : 'user'
}

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
    limitDraft.value = limitDraftFrom(settings.value)
    users.value = await listAdminUsers()
    currentUserId.value = authStore.user?.id || ''
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function saveSettings() {
  message.value = ''
  error.value = ''
  // read 上限先在前端校验（后端同规则 422 兜底）：空白 = 回到服务器默认
  const limits = {} as Record<LimitTier, BatchReadLimitPair>
  for (const t of LIMIT_TIERS) {
    limits[t] = { max_reads_per_group: null, max_total_reads: null }
    for (const f of LIMIT_FIELDS) {
      const r = parseLimitInput(limitDraft.value[t][f], limitHardMax.value[f],
        `「${LIMIT_TIER_LABELS[t]}」${LIMIT_FIELD_LABELS[f]}`)
      if (!r.ok) {
        error.value = r.error
        return
      }
      limits[t][f] = r.value
    }
  }
  savingSettings.value = true
  try {
    const saved = await updateAdminSettings({
      registration_open: draft.value.registration_open,
      email_verification_required: draft.value.email_verification_required,
      anonymous_features: [...draft.value.anonymous_features],
      user_features: [...draft.value.user_features],
      batch_read_limits: limits
    })
    settings.value = saved
    draft.value = {
      registration_open: saved.registration_open,
      email_verification_required: saved.email_verification_required,
      anonymous_features: [...saved.anonymous_features],
      user_features: [...saved.user_features]
    }
    limitDraft.value = limitDraftFrom(saved)
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

// ==================== 用户功能权限 ====================
const permUser = ref<AdminUserInfo | null>(null)
const permDraft = ref<{ followDefault: boolean; features: string[] }>({ followDefault: true, features: [] })
const savingPerm = ref(false)

const permDefaultFeatures = computed(() => settings.value?.user_features ?? [])

function openPermEditor(u: AdminUserInfo) {
  permUser.value = u
  if (u.allowed_features && u.allowed_features.length > 0) {
    permDraft.value = { followDefault: false, features: [...u.allowed_features] }
  } else {
    permDraft.value = { followDefault: true, features: [...permDefaultFeatures.value] }
  }
}

function togglePermFeature(key: string) {
  const list = permDraft.value.features
  const idx = list.indexOf(key)
  if (idx >= 0) list.splice(idx, 1)
  else list.push(key)
}

async function savePerm() {
  if (!permUser.value) return
  savingPerm.value = true
  error.value = ''
  try {
    const features = permDraft.value.followDefault ? [] : permDraft.value.features
    const updated = await updateAdminUser(permUser.value.id, { allowed_features: features })
    users.value = users.value.map((x) => (x.id === updated.id ? updated : x))
    permUser.value = null
    message.value = '用户功能权限已更新'
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '保存失败'
  } finally {
    savingPerm.value = false
  }
}

function permSummary(u: AdminUserInfo): string {
  if (!u.allowed_features || u.allowed_features.length === 0) return '跟随默认'
  if (u.allowed_features.length === featureKeys.value.length) return '全部功能'
  return u.allowed_features.map((k) => featureLabels.value[k] || k).join('、')
}

// ==================== 用户 read 上限覆盖 ====================
const limitUser = ref<AdminUserInfo | null>(null)
const limitUserDraft = ref<Record<LimitField, string>>({ max_reads_per_group: '', max_total_reads: '' })
const savingLimit = ref(false)

const USER_LIMIT_COLUMNS: Record<LimitField, 'batch_max_reads_per_group' | 'batch_max_total_reads'> = {
  max_reads_per_group: 'batch_max_reads_per_group',
  max_total_reads: 'batch_max_total_reads'
}

function openLimitEditor(u: AdminUserInfo) {
  limitUser.value = u
  limitUserDraft.value = {
    max_reads_per_group: limitToInput(u.batch_max_reads_per_group),
    max_total_reads: limitToInput(u.batch_max_total_reads)
  }
}

async function saveUserLimit() {
  const u = limitUser.value
  if (!u) return
  error.value = ''
  const patch: { batch_max_reads_per_group?: number | null; batch_max_total_reads?: number | null } = {}
  for (const f of LIMIT_FIELDS) {
    const r = parseLimitInput(limitUserDraft.value[f], limitHardMax.value[f], `个人「${LIMIT_FIELD_LABELS[f]}」`)
    if (!r.ok) {
      error.value = r.error
      return
    }
    patch[USER_LIMIT_COLUMNS[f]] = r.value // null = 清除覆盖（跟随分组）
  }
  savingLimit.value = true
  try {
    const updated = await updateAdminUser(u.id, patch)
    users.value = users.value.map((x) => (x.id === updated.id ? updated : x))
    limitUser.value = null
    message.value = '用户批量 read 上限已更新'
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '保存失败'
  } finally {
    savingLimit.value = false
  }
}

function limitSummary(u: AdminUserInfo): string {
  const g = u.batch_max_reads_per_group
  const t = u.batch_max_total_reads
  if (g == null && t == null) return '跟随分组'
  const tier = userTier(u)
  const gs = g ?? `${tierLimit(tier, 'max_reads_per_group')}（分组）`
  const ts = t ?? `${tierLimit(tier, 'max_total_reads')}（分组）`
  return `每组 ${gs} / 整批 ${ts}`
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

        <h3>批量测序 read 上限</h3>
        <p class="hint">
          按分组限制单次批量测序参与比对的 read 数；留空 = 使用服务器默认（每组
          {{ limitDefaults.max_reads_per_group }} / 整批 {{ limitDefaults.max_total_reads }} 条，环境变量配置），
          最多可设每组 {{ limitHardMax.max_reads_per_group }} / 整批 {{ limitHardMax.max_total_reads }} 条（服务器安全上限）。
          可在下方用户列表为单个账号单独设置。
        </p>
        <table class="feature-table limit-table">
          <thead>
            <tr>
              <th>分组</th>
              <th v-for="f in LIMIT_FIELDS" :key="f">{{ LIMIT_FIELD_LABELS[f] }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in LIMIT_TIERS" :key="t">
              <td>{{ LIMIT_TIER_LABELS[t] }}</td>
              <td v-for="f in LIMIT_FIELDS" :key="f">
                <input
                  v-model="limitDraft[t][f]"
                  type="text"
                  inputmode="numeric"
                  class="limit-input"
                  :data-testid="`limit-${t}-${f}`"
                  :placeholder="`默认 ${limitDefaults[f]}`"
                />
              </td>
            </tr>
          </tbody>
        </table>
        <p class="hint">
          批量测序页会把文件自动分批上传，文件个数不再限制批次；单批总体积上限
          {{ settings.batch_session_max_mb ?? 2048 }} MB（环境变量 SEQUENCING_BATCH_SESSION_MAX_MB）。
          旧的单请求接口仍受 {{ settings.batch_upload_max_mb ?? 500 }} MB / {{ settings.batch_max_files ?? 400 }} 个文件限制。
        </p>

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
              <th>功能权限</th>
              <th>批量 read 上限</th>
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
                <button class="btn small perm-btn" @click="openPermEditor(u)">{{ permSummary(u) }}</button>
              </td>
              <td>
                <button class="btn small perm-btn limit-btn" @click="openLimitEditor(u)">{{ limitSummary(u) }}</button>
              </td>
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

    <!-- ==================== 用户功能权限弹窗 ==================== -->
    <div v-if="permUser" class="modal-mask" @click.self="permUser = null">
      <div class="modal">
        <h3>功能权限 — {{ permUser.username }}</h3>
        <p class="hint">{{ permUser.email }}</p>

        <div class="switch-row">
          <div>
            <div class="switch-title">跟随站点默认</div>
            <div class="switch-desc">与「站点设置 → 普通登录用户」的功能清单保持一致；关闭后为该用户精确指定</div>
          </div>
          <label class="switch">
            <input v-model="permDraft.followDefault" type="checkbox" />
            <span class="slider"></span>
          </label>
        </div>

        <div v-if="!permDraft.followDefault" class="perm-list">
          <label v-for="key in featureKeys" :key="key" class="perm-item">
            <input
              type="checkbox"
              :checked="permDraft.features.includes(key)"
              @change="togglePermFeature(key)"
            />
            {{ featureLabels[key] || key }} <code class="key">{{ key }}</code>
          </label>
          <p v-if="permDraft.features.length === 0" class="hint warn">
            未勾选任何功能——该用户将无法使用任何功能（管理员不受影响）
          </p>
        </div>

        <div class="modal-actions">
          <button class="btn" @click="permUser = null">取消</button>
          <button class="btn primary" :disabled="savingPerm" @click="savePerm">
            {{ savingPerm ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ==================== 用户批量 read 上限弹窗 ==================== -->
    <div v-if="limitUser" class="modal-mask" @click.self="limitUser = null">
      <div class="modal">
        <h3>批量测序 read 上限 — {{ limitUser.username }}</h3>
        <p class="hint">
          {{ limitUser.email }} · 所在分组：{{ LIMIT_TIER_LABELS[userTier(limitUser)] }}。
          留空 = 跟随分组；单独设置可高于或低于分组值。
        </p>
        <label v-for="f in LIMIT_FIELDS" :key="f" class="limit-row">
          <span>{{ LIMIT_FIELD_LABELS[f] }}</span>
          <input
            v-model="limitUserDraft[f]"
            type="text"
            inputmode="numeric"
            class="limit-input"
            :data-testid="`user-limit-${f}`"
            :placeholder="`跟随分组（${tierLimit(userTier(limitUser), f)}）`"
          />
        </label>
        <div class="modal-actions">
          <button class="btn" @click="limitUser = null">取消</button>
          <button class="btn primary save-limit" :disabled="savingLimit" @click="saveUserLimit">
            {{ savingLimit ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
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

.btn.small {
  padding: 0.25rem 0.6rem;
  font-size: 0.8125rem;
}

.perm-btn {
  background: #eef2ff;
  color: #4f46e5;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}

.modal {
  background: #fff;
  border-radius: 12px;
  padding: 1.5rem;
  width: min(480px, 92vw);
  max-height: 85vh;
  overflow-y: auto;
}

.modal h3 {
  margin: 0 0 0.25rem;
  font-size: 1.05rem;
}

.perm-list {
  margin-top: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.perm-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  font-size: 0.875rem;
}

.perm-item input {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.hint.warn {
  color: #dc2626;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.6rem;
  margin-top: 1.25rem;
}

.modal-actions .btn.primary {
  margin-top: 0;
}

.limit-input {
  width: 9rem;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--border-color, #ddd);
  border-radius: 6px;
  font-size: 0.875rem;
}

.limit-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  margin-top: 0.75rem;
  font-size: 0.875rem;
}
</style>
