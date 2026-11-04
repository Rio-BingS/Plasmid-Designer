// 本文件由 api/index.ts 按域拆分而来（纯搬运）：index.ts 保留 re-export 兼容层，
// 新代码请直接从 '@/api/<域>' 导入。

import { api } from './client'
import type {
  AuthTokenResponse, UserInfo, VerifyTokenResponse
} from '@/types'

export async function login(email: string, password: string): Promise<AuthTokenResponse> {
  const response = await api.post('/auth/login', { email, password })
  return response.data
}

export async function register(email: string, username: string, password: string, confirmPassword: string): Promise<AuthTokenResponse> {
  const response = await api.post('/auth/register', {
    email,
    username,
    password,
    confirm_password: confirmPassword
  })
  return response.data
}

export async function getCurrentUser(): Promise<UserInfo | null> {
  try {
    const response = await api.get('/auth/me')
    return response.data
  } catch {
    return null
  }
}

export async function verifyToken(): Promise<VerifyTokenResponse> {
  const response = await api.get('/auth/verify')
  return response.data
}

// 用户登出
export async function logout(): Promise<void> {
  const response = await api.post('/auth/logout')
  return response.data
}

// 公开站点配置（注册开关/邮箱验证开关/各级别可用功能）
export interface SiteConfig {
  registration_open: boolean
  email_verification_required: boolean
  tier: 'anonymous' | 'user' | 'admin'
  features: { anonymous: string[]; user: string[] }
  effective_features: string[]
  // 当前账号的批量测序上限（后端已按 个人覆盖 → 分组 → 服务器默认 解析；旧后端无此字段）
  batch_read_limits?: BatchReadLimitsInfo | null
}

export interface BatchReadLimitsInfo {
  max_reads_per_group: number
  max_total_reads: number
  /** 单请求入口的文件数 / 总体积上限（批量页走分块上传，不受这两项约束） */
  max_files: number
  max_upload_mb: number
  /** 分块上传：单个分块的文件数 / 体积上限与整批体积上限（旧后端无此字段） */
  chunk_max_files?: number
  chunk_max_mb?: number
  session_max_mb?: number
}

export async function getSiteConfig(): Promise<SiteConfig> {
  const response = await api.get('/auth/site-config')
  return response.data
}

// 提交注册邮箱验证码（成功返回登录令牌）
export async function verifyEmail(verifyToken: string, code: string): Promise<any> {
  const response = await api.post('/auth/verify-email', { verify_token: verifyToken, code })
  return response.data
}

// 重发注册验证码（60 秒冷却）
export async function resendVerification(verifyToken: string): Promise<any> {
  const response = await api.post('/auth/resend-verification', { verify_token: verifyToken })
  return response.data
}

// ==================== 管理员 ====================

