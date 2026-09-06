<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { verifyEmail, resendVerification } from '@/api'
import { useRouter } from 'vue-router'

const router = useRouter()
const authStore = useAuthStore()
const emit = defineEmits(['close', 'authenticated'])

const mode = ref<'login' | 'register' | 'verify'>('login')
const loading = ref(false)
const error = ref('')
const notice = ref('')

// 邮箱验证流程状态（verify 模式下使用）
const verifyToken = ref('')
const verifyEmailAddr = ref('')
const code = ref('')
const resendTimer = ref(0)
let timerHandle: ReturnType<typeof setInterval> | null = null

// 登录表单
const loginForm = ref({
  email: '',
  password: ''
})

// 注册表单
const registerForm = ref({
  email: '',
  username: '',
  password: '',
  confirmPassword: ''
})

const registrationOpen = computed(() => authStore.registrationOpen)

function startResendTimer(seconds = 60) {
  resendTimer.value = seconds
  if (timerHandle) clearInterval(timerHandle)
  timerHandle = setInterval(() => {
    resendTimer.value -= 1
    if (resendTimer.value <= 0 && timerHandle) {
      clearInterval(timerHandle)
      timerHandle = null
    }
  }, 1000)
}

onUnmounted(() => {
  if (timerHandle) clearInterval(timerHandle)
})

// 后端错误文本：FastAPI 的 HTTPException detail 在 e.response.data.detail；
// detail 为对象时（EMAIL_NOT_VERIFIED）取其 message
function errText(e: any): string {
  const d = e?.response?.data?.detail ?? e?.detail
  if (typeof d === 'string') return d
  if (d?.message) return d.message
  return e?.message || '操作失败'
}

function enterVerifyMode(token: string, email: string, noticeText?: string) {
  verifyToken.value = token
  verifyEmailAddr.value = email
  code.value = ''
  notice.value = noticeText || ''
  error.value = ''
  mode.value = 'verify'
  startResendTimer()
}

async function handleLogin() {
  if (!loginForm.value.email || !loginForm.value.password) {
    error.value = '请填写邮箱和密码'
    return
  }

  loading.value = true
  error.value = ''

  try {
    const result = await authStore.login(loginForm.value.email, loginForm.value.password)
    emit('authenticated', result.user)
    emit('close')
    router.push('/')
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    // 邮箱未验证：后端返回 verify_token，直接进入验证码输入步骤
    if (detail && typeof detail === 'object' && detail.code === 'EMAIL_NOT_VERIFIED') {
      enterVerifyMode(detail.verify_token, detail.email, '该账号邮箱尚未验证，请输入验证码完成验证')
    } else {
      error.value = errText(e)
    }
  } finally {
    loading.value = false
  }
}

async function handleRegister() {
  if (!registerForm.value.email || !registerForm.value.username || !registerForm.value.password) {
    error.value = '请填写所有必填项'
    return
  }

  if (registerForm.value.password !== registerForm.value.confirmPassword) {
    error.value = '两次输入的密码不一致'
    return
  }

  if (registerForm.value.password.length < 8) {
    error.value = '密码长度至少8位'
    return
  }

  loading.value = true
  error.value = ''

  try {
    const result = await authStore.register(
      registerForm.value.email,
      registerForm.value.username,
      registerForm.value.password,
      registerForm.value.confirmPassword
    )
    if (result.requires_verification) {
      enterVerifyMode(
        result.verify_token,
        result.email,
        result.mail_sent
          ? `验证码已发送至 ${result.email}，请查收邮箱完成验证`
          : '验证码邮件发送失败，请点击"重新发送"重试'
      )
      return
    }
    emit('authenticated', result.user)
    emit('close')
    router.push('/')
  } catch (e: any) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

async function handleVerify() {
  if (!code.value || code.value.trim().length !== 6) {
    error.value = '请输入 6 位验证码'
    return
  }

  loading.value = true
  error.value = ''

  try {
    const result = await verifyEmail(verifyToken.value, code.value.trim())
    tokenToSession(result)
  } catch (e: any) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

async function handleResend() {
  if (resendTimer.value > 0) return
  loading.value = true
  error.value = ''
  try {
    const result = await resendVerification(verifyToken.value)
    verifyToken.value = result.verify_token || verifyToken.value
    notice.value = `验证码已重新发送至 ${result.email || verifyEmailAddr.value}`
    startResendTimer()
  } catch (e: any) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

function tokenToSession(result: any) {
  localStorage.setItem('token', result.access_token)
  localStorage.setItem('user', JSON.stringify(result.user))
  authStore.token = result.access_token
  authStore.user = result.user
  authStore.refreshSiteConfig()
  emit('authenticated', result.user)
  emit('close')
  router.push('/')
}

function switchMode() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  error.value = ''
  notice.value = ''
}
</script>

<template>
  <div class="auth-modal">
    <div class="auth-card">
      <button class="close-btn" @click="$emit('close')">×</button>

      <div class="auth-header">
        <h2>{{ mode === 'login' ? '登录' : mode === 'register' ? '注册' : '邮箱验证' }}</h2>
        <p v-if="mode === 'login'">登录您的账户以使用完整功能</p>
        <p v-else-if="mode === 'register'">创建新账户开始设计质粒</p>
        <p v-else>验证码已发送至 {{ verifyEmailAddr }}</p>
      </div>

      <div v-if="error" class="error-message">{{ error }}</div>
      <div v-if="notice && !error" class="notice-message">{{ notice }}</div>

      <!-- 登录表单 -->
      <form v-if="mode === 'login'" @submit.prevent="handleLogin" class="auth-form">
        <div class="form-group">
          <label>邮箱</label>
          <input
            v-model="loginForm.email"
            type="email"
            placeholder="your@email.com"
            class="form-input"
          />
        </div>

        <div class="form-group">
          <label>密码</label>
          <input
            v-model="loginForm.password"
            type="password"
            placeholder="输入密码"
            class="form-input"
          />
        </div>

        <button type="submit" class="btn btn-primary submit-btn" :disabled="loading">
          {{ loading ? '登录中...' : '登录' }}
        </button>

        <p v-if="registrationOpen" class="switch-text">
          还没有账户？
          <a @click="switchMode">立即注册</a>
        </p>
        <p v-else class="switch-text closed-text">站点当前已关闭注册</p>
      </form>

      <!-- 注册表单（站点关闭注册时不渲染） -->
      <form v-if="mode === 'register'" @submit.prevent="handleRegister" class="auth-form">
        <div class="form-group">
          <label>邮箱 *</label>
          <input
            v-model="registerForm.email"
            type="email"
            placeholder="your@email.com"
            class="form-input"
          />
        </div>

        <div class="form-group">
          <label>用户名 *</label>
          <input
            v-model="registerForm.username"
            type="text"
            placeholder="用户名"
            class="form-input"
          />
        </div>

        <div class="form-group">
          <label>密码 *</label>
          <input
            v-model="registerForm.password"
            type="password"
            placeholder="至少8位"
            class="form-input"
          />
        </div>

        <div class="form-group">
          <label>确认密码 *</label>
          <input
            v-model="registerForm.confirmPassword"
            type="password"
            placeholder="再次输入密码"
            class="form-input"
          />
        </div>

        <button type="submit" class="btn btn-primary submit-btn" :disabled="loading">
          {{ loading ? '注册中...' : '注册' }}
        </button>

        <p class="switch-text">
          已有账户？
          <a @click="switchMode">立即登录</a>
        </p>
      </form>

      <!-- 邮箱验证码表单 -->
      <form v-if="mode === 'verify'" @submit.prevent="handleVerify" class="auth-form">
        <div class="form-group">
          <label>验证码（6 位数字，10 分钟内有效）</label>
          <input
            v-model="code"
            type="text"
            inputmode="numeric"
            maxlength="6"
            placeholder="000000"
            class="form-input code-input"
          />
        </div>

        <button type="submit" class="btn btn-primary submit-btn" :disabled="loading">
          {{ loading ? '验证中...' : '完成验证并登录' }}
        </button>

        <p class="switch-text">
          没收到验证码？
          <a v-if="resendTimer <= 0" @click="handleResend">重新发送</a>
          <span v-else class="cooldown">{{ resendTimer }}s 后可重发</span>
        </p>
        <p class="switch-text">
          <a @click="mode = 'login'">返回登录</a>
        </p>
      </form>
    </div>
  </div>
</template>

<style scoped>
.auth-modal {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.auth-card {
  background: white;
  padding: 2rem;
  border-radius: 12px;
  width: 100%;
  max-width: 400px;
  position: relative;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
}

.close-btn {
  position: absolute;
  top: 1rem;
  right: 1rem;
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  color: #999;
}

.close-btn:hover {
  color: #333;
}

.auth-header {
  text-align: center;
  margin-bottom: 1.5rem;
}

.auth-header h2 {
  font-size: 1.5rem;
  margin-bottom: 0.5rem;
}

.auth-header p {
  color: #666;
  font-size: 0.875rem;
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.form-group label {
  font-size: 0.875rem;
  font-weight: 500;
}

.form-input {
  padding: 0.75rem;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 1rem;
}

.form-input:focus {
  outline: none;
  border-color: var(--primary-color);
  box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1);
}

.error-message {
  padding: 0.75rem;
  background: rgba(239, 68, 68, 0.1);
  border-radius: 6px;
  color: #dc2626;
  font-size: 0.875rem;
  text-align: center;
}

.notice-message {
  padding: 0.75rem;
  background: rgba(79, 70, 229, 0.08);
  border-radius: 6px;
  color: #4f46e5;
  font-size: 0.875rem;
  text-align: center;
  margin-bottom: 1rem;
}

.code-input {
  text-align: center;
  font-size: 1.4rem;
  letter-spacing: 0.5rem;
}

.cooldown {
  color: #999;
}

.closed-text {
  color: #b45309;
}

.submit-btn {
  margin-top: 0.5rem;
}

.switch-text {
  text-align: center;
  font-size: 0.875rem;
  color: #666;
}

.switch-text a {
  color: var(--primary-color);
  cursor: pointer;
  text-decoration: underline;
}

.switch-text a:hover {
  color: #4338ca;
}
</style>
