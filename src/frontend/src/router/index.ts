import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import ForbiddenView from '@/views/ForbiddenView.vue'
import { useAuthStore } from '@/stores/auth'

// 首页与 403 页随主包加载；其余页面按路由拆包（图谱模块只在用到它的页面加载）
const DesignView = () => import('@/views/DesignView.vue')
const ResultView = () => import('@/views/ResultView.vue')
const VectorsView = () => import('@/views/VectorsView.vue')
const VectorDetailView = () => import('@/views/VectorDetailView.vue')
const BatchDesignView = () => import('@/views/BatchDesignView.vue')
const AnalysisView = () => import('@/views/AnalysisView.vue')
const SequencingView = () => import('@/views/SequencingView.vue')
const BatchSequencingView = () => import('@/views/BatchSequencingView.vue')
const CacheView = () => import('@/views/CacheView.vue')
const MapView = () => import('@/views/MapView.vue')
const AdminView = () => import('@/views/AdminView.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/design', name: 'design', component: DesignView, meta: { feature: 'design' } },
    { path: '/batch', name: 'batch', component: BatchDesignView, meta: { feature: 'batch' } },
    { path: '/result/:designId', name: 'result', component: ResultView, props: true, meta: { feature: 'design' } },
    { path: '/vectors', name: 'vectors', component: VectorsView, meta: { feature: 'vectors' } },
    { path: '/vectors/:id', name: 'vector-detail', component: VectorDetailView, props: true, meta: { feature: 'vectors' } },
    { path: '/analysis', name: 'analysis', component: AnalysisView, meta: { feature: 'analysis' } },
    // 测序分析页与批量测序页权限独立（sequencing / sequencing_batch）；
    // 测序分析页是两者共用的详情回看载体（批量结果深链 ?history=<id>），
    // 任一功能开放即可进入，页内未开放的部分自行降级隐藏
    { path: '/sequencing', name: 'sequencing', component: SequencingView,
      meta: { feature: 'sequencing', anyFeature: ['sequencing', 'sequencing_batch'] } },
    { path: '/sequencing/batch', name: 'sequencing-batch', component: BatchSequencingView, meta: { feature: 'sequencing_batch' } },
    { path: '/map', name: 'map', component: MapView, meta: { feature: 'map' } },
    { path: '/cache', name: 'cache', component: CacheView },
    { path: '/admin', name: 'admin', component: AdminView, meta: { adminOnly: true } },
    { path: '/forbidden', name: 'forbidden', component: ForbiddenView }
  ]
})

// 站点功能门控：导航入口已按 site-config 隐藏，这里拦截直链访问。
// 站点配置未加载完成（null）时放行，避免刷新时误拦。
// anyFeature：任一功能开放即放行（共用页面）；feature：单独要求。
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.siteConfig) {
    await auth.refreshSiteConfig()
  }
  if (to.meta.adminOnly) {
    // 登录态来自后端核实（httpOnly Cookie，前端无本地副本）：
    // 刷新直达管理页时先等会话核实完成，否则会被误判为未登录
    await auth.ensureSession()
    if (!auth.isAdmin) return { name: 'home' }
  }
  const anyFeature = to.meta.anyFeature as string[] | undefined
  if (anyFeature?.length) {
    if (!anyFeature.some((f) => auth.featureAllowed(f))) {
      return { name: 'forbidden', query: { feature: anyFeature[0] } }
    }
    return true
  }
  const feature = to.meta.feature as string | undefined
  if (feature && !auth.featureAllowed(feature)) {
    return { name: 'forbidden', query: { feature } }
  }
  return true
})

// 按路由拆包后，重新部署会删掉旧的分包文件；还开着旧页面的标签页导航时会加载失败。
// 遇到这种错误整页跳到目标地址一次以拿到新版本（同一地址只重试一次，避免死循环）。
const CHUNK_ERROR_RE = /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i
router.onError((err, to) => {
  if (!CHUNK_ERROR_RE.test(String((err as Error)?.message ?? err))) return
  const KEY = 'pd.chunk-reload'
  try {
    if (sessionStorage.getItem(KEY) === to.fullPath) return
    sessionStorage.setItem(KEY, to.fullPath)
  } catch { /* 存储不可用时仍尝试一次 */ }
  window.location.assign(to.fullPath)
})

export default router
