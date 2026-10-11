/** 批量测序 read 上限：管理面板分组/个人设置与批量页本账号上限提示 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { parseLimitInput, limitToInput } from '@/utils/batchReadLimits'
import { useAuthStore } from '@/stores/auth'
import AdminView from '@/views/AdminView.vue'
import BatchSequencingView from '@/views/BatchSequencingView.vue'
import * as api from '@/api'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

vi.mock('@/api', () => ({
  getAdminSettings: vi.fn(),
  updateAdminSettings: vi.fn(),
  listAdminUsers: vi.fn(),
  updateAdminUser: vi.fn(),
  deleteAdminUser: vi.fn(),
  analyzeSequencingBatch: vi.fn(),
  downloadBatchSequencingReport: vi.fn(),
  formatApiError: vi.fn((_e: unknown, fallback: string) => fallback),
  getSiteConfig: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
  verifyToken: vi.fn()
}))

function adminSettings(overrides: Partial<api.AdminSettings> = {}): api.AdminSettings {
  return {
    registration_open: true,
    email_verification_required: false,
    anonymous_features: ['design'],
    user_features: ['design'],
    feature_keys: ['design'],
    feature_labels: { design: '引物/序列设计' },
    mail_provider: 'console',
    mail_from: 'x@y.z',
    batch_read_limits: {
      anonymous: { max_reads_per_group: null, max_total_reads: 60 },
      user: { max_reads_per_group: null, max_total_reads: 600 },
      admin: { max_reads_per_group: null, max_total_reads: null }
    },
    batch_read_limit_defaults: { max_reads_per_group: 24, max_total_reads: 240 },
    batch_read_limit_hard_max: { max_reads_per_group: 384, max_total_reads: 5000 },
    batch_max_files: 400,
    batch_upload_max_mb: 500,
    batch_session_max_mb: 2048,
    ...overrides
  }
}

const plainUser: api.AdminUserInfo = {
  id: 'u1', email: 'u1@test.com', username: 'u1', is_admin: false, is_active: true,
  email_verified: true, allowed_features: null, batch_max_reads_per_group: null,
  batch_max_total_reads: null, created_at: null
}

describe('parseLimitInput', () => {
  it('空白 = 跟随上一级（null）', () => {
    expect(parseLimitInput('', 5000, 'x')).toEqual({ ok: true, value: null })
    expect(parseLimitInput('  ', 5000, 'x')).toEqual({ ok: true, value: null })
    expect(parseLimitInput(null, 5000, 'x')).toEqual({ ok: true, value: null })
  })
  it('合法整数通过，越界/非整数给中文错误', () => {
    expect(parseLimitInput('600', 5000, 'x')).toEqual({ ok: true, value: 600 })
    expect(parseLimitInput(5000, 5000, 'x')).toEqual({ ok: true, value: 5000 })
    const over = parseLimitInput('5001', 5000, '「普通用户」整批最多 read 数')
    expect(over.ok).toBe(false)
    if (!over.ok) expect(over.error).toContain('安全上限 5000')
    for (const bad of ['0', '-3', '12.5', 'abc']) {
      expect(parseLimitInput(bad, 5000, 'x').ok).toBe(false)
    }
  })
  it('limitToInput：null → 空白', () => {
    expect(limitToInput(null)).toBe('')
    expect(limitToInput(undefined)).toBe('')
    expect(limitToInput(48)).toBe('48')
  })
})

describe('AdminView 批量 read 上限', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    vi.mocked(api.getAdminSettings).mockResolvedValue(adminSettings())
    vi.mocked(api.listAdminUsers).mockResolvedValue([plainUser])
  })

  async function mountAdmin() {
    const w = mount(AdminView)
    await flushPromises()
    return w
  }

  it('分组输入框按已保存值预填，未设置为空白并提示默认值', async () => {
    const w = await mountAdmin()
    const userTotal = w.find('[data-testid="limit-user-max_total_reads"]').element as HTMLInputElement
    const adminTotal = w.find('[data-testid="limit-admin-max_total_reads"]').element as HTMLInputElement
    expect(userTotal.value).toBe('600')
    expect(adminTotal.value).toBe('')
    expect(adminTotal.placeholder).toBe('默认 240')
    expect(w.text()).toContain('500 MB')
    expect(w.text()).toContain('400 个文件')
    // 批量页自动分块上传：管理员看到的是单批总体积上限
    expect(w.text()).toContain('自动分批上传')
    expect(w.text()).toContain('2048 MB')
  })

  it('保存时提交三个分组的两项上限，空白提交 null', async () => {
    vi.mocked(api.updateAdminSettings).mockImplementation(async (p) => adminSettings({
      batch_read_limits: p.batch_read_limits as api.AdminSettings['batch_read_limits']
    }))
    const w = await mountAdmin()
    await w.find('[data-testid="limit-admin-max_total_reads"]').setValue('2000')
    await w.find('[data-testid="limit-user-max_total_reads"]').setValue('')
    await w.find('button.btn.primary').trigger('click')
    await flushPromises()
    const patch = vi.mocked(api.updateAdminSettings).mock.calls[0][0]
    expect(patch.batch_read_limits).toEqual({
      anonymous: { max_reads_per_group: null, max_total_reads: 60 },
      user: { max_reads_per_group: null, max_total_reads: null },
      admin: { max_reads_per_group: null, max_total_reads: 2000 }
    })
    expect(w.text()).toContain('设置已保存')
  })

  it('超出安全上限时就地报错且不提交', async () => {
    const w = await mountAdmin()
    await w.find('[data-testid="limit-anonymous-max_reads_per_group"]').setValue('385')
    await w.find('button.btn.primary').trigger('click')
    await flushPromises()
    expect(api.updateAdminSettings).not.toHaveBeenCalled()
    expect(w.text()).toContain('安全上限 384')
  })

  it('用户列表可单独设置 read 上限，空白 = 跟随分组（提交 null）', async () => {
    vi.mocked(api.updateAdminUser).mockResolvedValue({ ...plainUser, batch_max_total_reads: 1000 })
    const w = await mountAdmin()
    const btn = w.find('button.limit-btn')
    expect(btn.text()).toBe('跟随分组')
    await btn.trigger('click')
    const total = w.find('[data-testid="user-limit-max_total_reads"]')
    expect((total.element as HTMLInputElement).placeholder).toBe('跟随分组（600）')
    await total.setValue('1000')
    await w.find('button.save-limit').trigger('click')
    await flushPromises()
    expect(api.updateAdminUser).toHaveBeenCalledWith('u1', {
      batch_max_reads_per_group: null, batch_max_total_reads: 1000
    })
    expect(w.find('button.limit-btn').text()).toBe('每组 24（分组） / 整批 1000')
  })

  it('个人上限非法时不提交', async () => {
    const w = await mountAdmin()
    await w.find('button.limit-btn').trigger('click')
    await w.find('[data-testid="user-limit-max_total_reads"]').setValue('0')
    await w.find('button.save-limit').trigger('click')
    await flushPromises()
    expect(api.updateAdminUser).not.toHaveBeenCalled()
    expect(w.text()).toContain('正整数')
  })
})

describe('BatchSequencingView 本账号 read 上限提示', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function mountBatch() {
    return mount(BatchSequencingView, {
      global: { stubs: { 'router-link': { template: '<a><slot /></a>', props: ['to'] } } }
    })
  }

  it('site-config 下发上限时显示本账号单批最多 N 条', async () => {
    const store = useAuthStore()
    store.siteConfig = {
      registration_open: true, email_verification_required: false, tier: 'user',
      features: { anonymous: [], user: [] }, effective_features: [],
      batch_read_limits: {
        max_reads_per_group: 48, max_total_reads: 900, max_files: 400, max_upload_mb: 500,
        chunk_max_files: 200, chunk_max_mb: 90, session_max_mb: 2048
      }
    }
    const w = mountBatch()
    await flushPromises()
    const note = w.find('[data-testid="read-limit-note"]')
    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('本账号单批最多 900 条 read')
    expect(note.text()).toContain('每组最多 48 条')
    // 文件数与单请求体积不再限制批次：提示自动分批与整批体积上限
    expect(note.text()).toContain('文件会自动分批上传')
    expect(note.text()).toContain('2048 MB')
    expect(note.text()).not.toContain('400 个文件')
  })

  it('旧后端未下发时不显示', () => {
    const w = mountBatch()
    expect(w.find('[data-testid="read-limit-note"]').exists()).toBe(false)
  })
})
