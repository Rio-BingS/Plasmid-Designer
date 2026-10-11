<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { RouterLink, useRouter } from 'vue-router'
import AuthModal from './AuthModal.vue'

const router = useRouter()
const authStore = useAuthStore()
const showAuthModal = ref(false)
// user from store
const user = ref<any>(null)  // will be set from authStore
const showUserMenu = ref(false)

onMounted(() => {
  authStore.initSession()
  user.value = authStore.user
})

// Watch store changes
watch(() => authStore.user, (newUser) => {
  user.value = newUser
})

// 会话中途失效：收起用户菜单；停留在仅管理员可见的页面时回首页
watch(() => authStore.sessionExpired, (expired) => {
  if (!expired) return
  showUserMenu.value = false
  if (router.currentRoute.value.meta?.adminOnly) router.push('/')
})

function reloginAfterExpiry() {
  authStore.dismissSessionExpired()
  showAuthModal.value = true
}

// 功能导航项：按站点配置过滤（siteConfig 未加载完成时 featureAllowed 放行全部）
const navItems = [
  { to: '/', label: '首页', feature: null },
  { to: '/design', label: '设计', feature: 'design' },
  { to: '/batch', label: '批量设计', feature: 'batch' },
  { to: '/vectors', label: '载体库', feature: 'vectors' },
  { to: '/sequencing', label: '测序分析', feature: 'sequencing' },
  { to: '/sequencing/batch', label: '批量测序', feature: 'sequencing_batch' },
  { to: '/analysis', label: '序列工具', feature: 'analysis' },
  { to: '/map', label: '图谱', feature: 'map' }
]

const visibleNavItems = computed(() =>
  navItems.filter((item) => item.feature === null || authStore.featureAllowed(item.feature))
)

function handleAuthClick() {
  if (user.value) {
    showUserMenu.value = !showUserMenu.value
  } else {
    showAuthModal.value = true
  }
}

function handleAuthenticated(userData: any) {
  user.value = userData
  showAuthModal.value = false
}

// 登出失败时会话仍有效（Cookie 只能由服务端删除），保持登录态并提示重试
const logoutError = ref('')

async function handleLogout() {
  logoutError.value = ''
  try {
    await authStore.logout()
  } catch {
    logoutError.value = '退出登录未完成，请稍后重试'
    return
  }
  user.value = null
  showUserMenu.value = false
  router.push('/')
}

function goToBatch() {
  showUserMenu.value = false
  router.push('/batch')
}
</script>

<template>
  <nav class="navbar">
    <div class="nav-brand">
      <span class="logo">🧬</span>
      <RouterLink to="/" class="brand-text">Plasmid Designer</RouterLink>
    </div>
    
    <div class="nav-links">
      <RouterLink v-for="item in visibleNavItems" :key="item.to" :to="item.to" class="nav-link">
        {{ item.label }}
      </RouterLink>
    </div>
    
    <div class="nav-user">
      <!-- 会话核实中：不显示登录按钮也不显示用户菜单，只占位防止布局跳动 -->
      <span
        v-if="authStore.checking"
        class="auth-placeholder"
        role="status"
        aria-label="正在确认登录状态"
      ></span>
      <div v-else-if="user" class="user-section">
        <button class="user-btn" @click="handleAuthClick">
          <span class="user-avatar">{{ user.username?.charAt(0).toUpperCase() || 'U' }}</span>
          <span class="user-name">{{ user.username }}</span>
        </button>
        
        <div v-if="showUserMenu" class="user-dropdown">
          <div class="dropdown-header">
            {{ user.email }}
          </div>
          <div class="dropdown-divider"></div>
          <RouterLink v-if="authStore.isAdmin" to="/admin" class="dropdown-item">
            ⚙️ 站点管理
          </RouterLink>
          <RouterLink v-if="authStore.featureAllowed('batch')" to="/batch" class="dropdown-item" @click="goToBatch">
            📦 批量设计
          </RouterLink>
          <RouterLink v-if="authStore.featureAllowed('sequencing_batch')" to="/sequencing/batch" class="dropdown-item">
            🧬 批量测序
          </RouterLink>
          <button class="dropdown-item logout-btn" @click="handleLogout">
            🚪 退出登录
          </button>
          <div v-if="logoutError" class="logout-error" role="alert">{{ logoutError }}</div>
        </div>
      </div>
      
      <button v-else class="login-btn" @click="showAuthModal = true">
        登录 / 注册
      </button>
    </div>
    
    <div v-if="authStore.sessionExpired && !user" class="session-expired" role="alert">
      <span>登录已过期，请重新登录</span>
      <button class="session-expired-login" @click="reloginAfterExpiry">重新登录</button>
      <button class="session-expired-close" aria-label="关闭提示" @click="authStore.dismissSessionExpired()">×</button>
    </div>

    <AuthModal 
      v-if="showAuthModal" 
      @close="showAuthModal = false"
      @authenticated="handleAuthenticated"
    />
  </nav>
</template>

<style scoped>
.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 2rem;
  background-color: white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  position: relative;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.logo {
  font-size: 1.5rem;
}

.brand-text {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--primary-color);
  text-decoration: none;
}

.nav-links {
  display: flex;
  gap: 1.5rem;
}

.nav-link {
  color: var(--text-secondary);
  text-decoration: none;
  font-weight: 500;
  transition: color 0.2s;
}

.nav-link:hover,
.nav-link.router-link-active {
  color: var(--primary-color);
}

.nav-user {
  position: relative;
}

.user-section {
  position: relative;
}

.auth-placeholder {
  display: inline-block;
  width: 6.5rem;
  height: 2.25rem;
  border-radius: 9999px;
  background: var(--bg-secondary);
  opacity: 0.6;
}

.user-btn {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  background: var(--bg-secondary);
  border: none;
  border-radius: 9999px;
  cursor: pointer;
  transition: all 0.2s;
}

.user-btn:hover {
  background: #E5E7EB;
}

.user-avatar {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--primary-color);
  color: white;
  border-radius: 50%;
  font-weight: 600;
  font-size: 0.875rem;
}

.user-name {
  font-weight: 500;
  color: #333;
}

.login-btn {
  padding: 0.5rem 1.25rem;
  background: var(--primary-color);
  color: white;
  border: none;
  border-radius: 9999px;
  cursor: pointer;
  font-weight: 500;
  transition: all 0.2s;
}

.login-btn:hover {
  background: #4338CA;
}

.user-dropdown {
  position: absolute;
  top: calc(100% + 0.5rem);
  right: 0;
  background: white;
  border-radius: 8px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
  min-width: 200px;
  overflow: hidden;
  z-index: 100;
}

.dropdown-header {
  padding: 0.75rem 1rem;
  font-size: 0.75rem;
  color: #666;
  border-bottom: 1px solid #eee;
}

.dropdown-divider {
  height: 1px;
  background: #eee;
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.75rem 1rem;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  color: #333;
  text-decoration: none;
  transition: background 0.2s;
}

.dropdown-item:hover {
  background: #F3F4F6;
}

.session-expired {
  position: absolute;
  top: 100%;
  right: 2rem;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 1rem;
  background: #fff4e5;
  border: 1px solid #f0c36d;
  border-radius: 6px;
  color: #8a5300;
  font-size: 0.9rem;
}

.session-expired button {
  border: none;
  background: none;
  cursor: pointer;
  color: inherit;
}

.session-expired-login {
  font-weight: 600;
  text-decoration: underline;
}

.logout-error {
  padding: 0.5rem 1rem;
  color: #c0392b;
  font-size: 0.85rem;
}

.logout-btn {
  color: #DC2626;
}
</style>
