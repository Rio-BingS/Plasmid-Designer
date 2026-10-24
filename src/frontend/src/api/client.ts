import axios from 'axios'
import { currentAuthEpoch, notifySessionExpired } from './session'

const API_BASE = '/api'

// 注意：这里不能硬编码 Content-Type。
// axios 1.x 的 transformRequest 见到「Content-Type 含 application/json」且 data 是
// FormData 时，会把 FormData 转成 JSON 字符串（File 全变成 {}）发出去，
// 后端 multipart 接口就会收到空表单并报 "Field required"。
// 交给 axios 自己判断：普通对象发 application/json，FormData 发 multipart（含 boundary）。
//
// 登录态在后端下发的 httpOnly 会话 Cookie 里（前端不保存令牌）：
// withCredentials 让跨源部署（CORS_ORIGINS 显式来源）也带上 Cookie；
// X-Requested-With 是后端 CSRF 校验要求的自定义头——Cookie 会话的
// POST/PUT/PATCH/DELETE 缺它会被 403 拒绝。
export const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  headers: { 'X-Requested-With': 'XMLHttpRequest' }
})

/** 共享 axios 实例：供按功能拆分的接口模块（如 map/api.ts）复用拦截器与凭证 */
export { api as http }

// 请求拦截器：记下请求发出时的会话代际（见 api/session.ts）
api.interceptors.request.use(
  (config) => {
    ;(config as any).authEpoch = currentAuthEpoch()
    return config
  },
  (error) => Promise.reject(error)
)

/**
 * 这些端点的 401 表示「凭据/验证码无效」或「匿名令牌失效」，不是会话失效。
 * 用显式清单而不是散落的字符串判断：新增业务端点若用 401 表达业务错误，
 * 往这里加一条即可，不会静默踢掉用户的登录态。
 */
const SESSION_401_EXEMPT_PATHS = [
  '/auth/login',
  '/auth/register',
  '/auth/verify',
  '/auth/verify-email',
  '/auth/resend-verification',
]

/** 该请求的 401 是否应清掉本地登录态 */
export function shouldClearSessionOn401(config: any): boolean {
  if (!config) return true
  const url = String(config.url || '')
  if (SESSION_401_EXEMPT_PATHS.some((p) => url.includes(p))) return false
  // 带匿名测序访问令牌的请求：401 属于那个令牌，与会话无关
  const headers = config.headers
  const token = typeof headers?.get === 'function'
    ? headers.get('X-Access-Token')
    : headers?.['X-Access-Token'] ?? headers?.['x-access-token']
  return !token
}

// 响应拦截器：处理 401 错误
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401 && shouldClearSessionOn401(error.config)) {
      // 只清理「发出该请求时」的会话：旧会话发出的请求晚到的 401
      // 不能把期间重新登录建立的新会话一并清掉
      const sentEpoch = error.config?.authEpoch
      if (sentEpoch === currentAuthEpoch()) {
        // 同步清理 Pinia 状态，避免界面仍显示已登录（经 session 模块回调，
        // 避免与 auth store 循环依赖）
        notifySessionExpired()
      }
    }
    return Promise.reject(error)
  }
)

/** 把错误响应转成可读文本（axios 错误 / 后端 detail 都能吃）。
 *
 *  FastAPI 的 422 校验错误 detail 是数组，每条只有 "Field required" 这类 msg，
 *  必须带上 loc 里的字段名，否则界面上只会出现「Field required；Field required」
 *  这种看不出缺了什么的文案。
 */
/**
 * Cloudflare 网关错误（520–527，或 502/504 时返回的 Cloudflare 英文说明）翻译成中文。
 * 这类错误不是后端给的业务提示，原文对用户没有可行动的信息。返回空串表示不是网关错误。
 */
export function gatewayErrorText(e: any): string {
  const status = Number(e?.response?.status) || 0
  const data = e?.response?.data
  const raw = typeof data === 'string' ? data : `${data?.title ?? ''} ${data?.detail ?? ''}`
  const fromCf = /cloudflare/i.test(raw)
  if (!(status >= 520 && status <= 527) && !(fromCf && status >= 500)) return ''
  if (status === 524) return '服务器处理超时（Cloudflare 524：超过 100 秒未返回），请稍后重试'
  return `网关错误（HTTP ${status}）：服务器暂时没有返回有效响应，可能正在重启或过载，请稍后重试；持续出现请查看后端日志`
}

export function formatApiError(e: any, fallback = '请求失败'): string {
  const gw = gatewayErrorText(e)
  if (gw) return gw
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: any) => {
        const msg = d?.msg || JSON.stringify(d)
        const field = Array.isArray(d?.loc)
          ? d.loc.filter((x: any) => x !== 'body' && x !== 'query' && x !== 'path').join('.')
          : ''
        return field ? `${field}: ${msg}` : msg
      })
      .join('；')
  }
  // 后端没给 detail 时的兜底文案：axios 原文是英文的
  // "Request failed with status code 429"，对用户没有任何可行动信息
  const status = Number(e?.response?.status) || 0
  if (status === 401) return '登录状态已失效，请重新登录'
  if (status === 403) return '没有权限执行该操作：可能需要管理员身份，或登录状态已变化'
  if (status === 429) {
    const retry = Number(e?.response?.headers?.['retry-after'])
    return retry > 0
      ? `请求过于频繁，请 ${Math.ceil(retry)} 秒后重试`
      : '请求过于频繁，请稍后重试'
  }
  if (status >= 500) {
    return `服务器错误（HTTP ${status}）：请稍后重试；持续出现请查看后端日志`
  }
  // 浏览器 XHR 在发送阶段失败（文件被其他程序占用、连接被掐断）时，axios 只给
  // 一句笼统的 "Network Error"，翻译成可行动的提示
  const msg = typeof e?.message === 'string' ? e.message : ''
  if (/network error|failed to fetch|load failed/i.test(msg)) {
    return '网络异常：请求未能送达服务器（可能是文件被其他程序占用、网络中断或超出传输大小限制），请检查后重试'
  }
  return msg || fallback
}

/** blob 下载公共路径：错误响应(JSON)直接抛出，成功则触发保存并释放 URL */
export async function saveBlobResponse(response: { data: any }, filename: string): Promise<void> {
  const data = response.data as Blob
  if (data.type && data.type.includes('application/json')) {
    // responseType: 'blob' 时后端的 4xx/5xx JSON 错误也以 Blob 返回，不能存成下载文件
    const text = await data.text()
    let detail = text
    try { detail = JSON.parse(text)?.detail ?? text } catch { /* 保留原文 */ }
    throw new Error(typeof detail === 'string' ? detail : '下载失败')
  }
  const url = window.URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', filename)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

// 设计任务
export function assertBlobIsGenbank(data: Blob, name: string): void {
  if (data.type && data.type.includes('application/json')) {
    throw new Error(`无法获取 ${name} 的参考序列（请确认其仍然存在）`)
  }
}

/** 取载体库载体的 GenBank 文件（测序页深链自动带入参考序列用） */
