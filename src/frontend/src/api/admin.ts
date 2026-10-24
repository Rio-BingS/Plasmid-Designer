// 本文件由 api/index.ts 按域拆分而来（纯搬运）：index.ts 保留 re-export 兼容层，
// 新代码请直接从 '@/api/<域>' 导入。

import { api } from './client'

export type LimitTier = 'anonymous' | 'user' | 'admin'

// 批量测序 read 上限的一组取值；null = 跟随上一级（分组 → 服务器默认）
export interface BatchReadLimitPair {
  max_reads_per_group: number | null
  max_total_reads: number | null
}

export interface AdminSettings {
  registration_open: boolean
  email_verification_required: boolean
  anonymous_features: string[]
  user_features: string[]
  feature_keys: string[]
  feature_labels: Record<string, string>
  mail_provider: string
  mail_from: string
  batch_read_limits?: Record<LimitTier, BatchReadLimitPair>
  batch_read_limit_defaults?: { max_reads_per_group: number; max_total_reads: number }
  batch_read_limit_hard_max?: { max_reads_per_group: number; max_total_reads: number }
  batch_max_files?: number
  batch_upload_max_mb?: number
  batch_session_max_mb?: number
}

export type AdminSettingsPatch = Partial<Omit<AdminSettings, 'batch_read_limits'>> & {
  batch_read_limits?: Partial<Record<LimitTier, Partial<BatchReadLimitPair>>>
}

export interface AdminUserInfo {
  id: string
  email: string
  username: string
  is_admin: boolean
  is_active: boolean
  email_verified: boolean
  allowed_features: string[] | null
  batch_max_reads_per_group?: number | null
  batch_max_total_reads?: number | null
  created_at: string | null
}

export async function getAdminSettings(): Promise<AdminSettings> {
  const response = await api.get('/admin/settings')
  return response.data
}

export async function updateAdminUser(
  userId: string,
  patch: {
    is_admin?: boolean
    is_active?: boolean
    email_verified?: boolean
    allowed_features?: string[]
    // null = 清除个人覆盖（跟随所在分组）
    batch_max_reads_per_group?: number | null
    batch_max_total_reads?: number | null
  }
): Promise<AdminUserInfo> {
  const response = await api.put(`/admin/users/${encodeURIComponent(userId)}`, patch)
  return response.data
}

export async function deleteAdminUser(userId: string): Promise<void> {
  await api.delete(`/admin/users/${encodeURIComponent(userId)}`)
}

// 获取导出格式列表
export async function updateAdminSettings(patch: AdminSettingsPatch): Promise<AdminSettings> {
  const response = await api.put('/admin/settings', patch)
  return response.data
}

export async function listAdminUsers(): Promise<AdminUserInfo[]> {
  const response = await api.get('/admin/users')
  return response.data
}

