/**
 * useOnlineImport composable 独立单测（不经 VectorsView 组件）
 *
 * 覆盖在线导入的核心行为：
 * - ensureOnlineSources：来源清单/许可/SnapGene 开关装载与失败静默
 * - loadSnapgeneIndex / searchSnapgeneByName：请求号防乱序（旧集合响应不覆盖新列表）
 * - importSnapgeneRow / confirmOnlineImport：导入成功消息与 reload 回调、
 *   双源分支（ensembl / snapgene）、失败落错误位
 * - doSearchAddgene：无命中显示 note；失败落 addgeneError
 * - switchTab：SnapGene 未开放（未登录/后端关）时页签不切换
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref, nextTick } from 'vue'
import { useOnlineImport } from '@/composables/useOnlineImport'

vi.mock('@/api', () => ({
  getOnlineSources: vi.fn(),
  getSnapgeneSets: vi.fn(),
  getSnapgeneSetIndex: vi.fn(),
  searchSnapgene: vi.fn(),
  previewSnapgene: vi.fn(),
  importFromSnapgene: vi.fn(),
  previewEnsembl: vi.fn(),
  importFromEnsembl: vi.fn(),
  searchAddgeneIndex: vi.fn(),
  getAddgeneMetadata: vi.fn(),
  importFromAddgene: vi.fn(),
  searchUniprot: vi.fn(),
  getUniprotEntry: vi.fn(),
  gatewayErrorText: (e: any) => e?.response?.data?.detail || e?.message || '',
}))

import {
  getOnlineSources, getSnapgeneSets, getSnapgeneSetIndex, searchSnapgene,
  importFromSnapgene, importFromEnsembl, searchAddgeneIndex,
} from '@/api'

const mocked = {
  getOnlineSources: vi.mocked(getOnlineSources),
  getSnapgeneSets: vi.mocked(getSnapgeneSets),
  getSnapgeneSetIndex: vi.mocked(getSnapgeneSetIndex),
  searchSnapgene: vi.mocked(searchSnapgene),
  importFromSnapgene: vi.mocked(importFromSnapgene),
  importFromEnsembl: vi.mocked(importFromEnsembl),
  searchAddgeneIndex: vi.mocked(searchAddgeneIndex),
}

function setup(loggedIn = true) {
  const reload = vi.fn().mockResolvedValue(undefined)
  const c = useOnlineImport({ loggedIn: ref(loggedIn), reload })
  return { c, reload }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ensureOnlineSources', () => {
  it('装载来源清单、免责声明与 SnapGene 开关', async () => {
    mocked.getOnlineSources.mockResolvedValue({
      sources: [
        { id: 'snapgene', license: 'SnapGene EULA', session_configured: false },
        { id: 'addgene', license: '', session_configured: true },
      ],
      disclaimer: '数据来自第三方',
      snapgene_online_enabled: true,
    } as any)
    const { c } = setup()
    await c.ensureOnlineSources()
    expect(c.onlineSources.value).toHaveLength(2)
    expect(c.onlineNotice.value).toBe('数据来自第三方')
    expect(c.snapgeneEnabled.value).toBe(true)
    expect(c.snapgeneLicense.value).toBe('SnapGene EULA')
    expect(c.addgeneSession.value).toBe(true)  // 决定「导入为本地载体」可用
  })

  it('未登录/接口失败时静默（错误位留给各面板）', async () => {
    mocked.getOnlineSources.mockRejectedValue(new Error('401'))
    const { c } = setup()
    await expect(c.ensureOnlineSources()).resolves.toBeUndefined()
    expect(c.onlineSources.value).toHaveLength(0)
    expect(c.onlineNotice.value).toBe('')
  })
})

describe('SnapGene 集合与乱序防护', () => {
  it('loadSnapgeneSets 选默认集合并加载索引', async () => {
    mocked.getSnapgeneSets.mockResolvedValue({
      sets: [{ set_id: 'basic', name: 'Basic' }, { set_id: 'auto', name: 'Auto' }],
    } as any)
    mocked.getSnapgeneSetIndex.mockResolvedValue({
      plasmids: [{ plasmid_id: 'p1', name: 'pET-30a' }],
    } as any)
    const { c } = setup()
    await c.loadSnapgeneSets()
    expect(c.sgSetId.value).toBe('basic')
    expect(c.sgRows.value).toHaveLength(1)
    expect(c.sgLoading.value).toBe(false)
  })

  it('快速切换集合时，乱序返回的旧响应不覆盖新列表', async () => {
    const { c } = setup()
    c.sgSetId.value = 'setA'
    let resolveA!: (v: any) => void
    mocked.getSnapgeneSetIndex.mockImplementationOnce(
      () => new Promise(r => { resolveA = r }))           // A 慢
    mocked.getSnapgeneSetIndex.mockResolvedValueOnce({ plasmids: [{ plasmid_id: 'new' }] } as any)

    const pA = c.loadSnapgeneIndex()   // gen=1（旧集合）
    const pB = c.loadSnapgeneIndex()   // gen=2（新集合，立即完成）
    await pB
    resolveA({ plasmids: [{ plasmid_id: 'stale' }] })     // 旧响应最后到达
    await pA
    expect(c.sgRows.value.map((r: any) => r.plasmid_id)).toEqual(['new'])
  })

  it('索引失败落 sgError，成功后清除', async () => {
    const { c } = setup()
    c.sgSetId.value = 'setA'
    mocked.getSnapgeneSetIndex.mockRejectedValueOnce({ message: 'boom' })
    await c.loadSnapgeneIndex()
    expect(c.sgError.value).toContain('boom')
    mocked.getSnapgeneSetIndex.mockResolvedValueOnce({ plasmids: [] } as any)
    await c.loadSnapgeneIndex()
    expect(c.sgError.value).toBe('')
  })
})

describe('在线导入', () => {
  it('importSnapgeneRow 成功：消息带名称与长度，并触发 reload', async () => {
    mocked.importFromSnapgene.mockResolvedValue({
      vector: { name: 'pET-30a', length: 5368 },
    } as any)
    const { c, reload } = setup()
    c.sgSetId.value = 'basic'
    await c.importSnapgeneRow({ plasmid_id: 'p1' })
    expect(mocked.importFromSnapgene).toHaveBeenCalledWith('basic', 'p1')
    expect(c.onlineMsg.value).toBe('已导入 pET-30a（5368 bp）')
    expect(reload).toHaveBeenCalledOnce()
    expect(c.onlineImporting.value).toBe(false)
  })

  it('importSnapgeneRow 失败：消息带原因，仍触发不了 reload', async () => {
    mocked.importFromSnapgene.mockRejectedValue({ response: { data: { detail: '已存在' } } })
    const { c, reload } = setup()
    await c.importSnapgeneRow({ plasmid_id: 'p1' })
    expect(c.onlineMsg.value).toBe('导入失败: 已存在')
    expect(reload).not.toHaveBeenCalled()
  })

  it('confirmOnlineImport 按 ensembl 前缀分流到 Ensembl 导入', async () => {
    mocked.importFromEnsembl.mockResolvedValue({
      vector: { name: 'ENSG00000123456', length: 1000 },
    } as any)
    const { c, reload } = setup()
    c.onlinePreview.value = { source: 'ensembl-cds', id: 'ENSG00000123456' } as any
    await c.confirmOnlineImport()
    expect(mocked.importFromEnsembl).toHaveBeenCalled()
    expect(c.showOnlinePreview.value).toBe(false)
    expect(reload).toHaveBeenCalledOnce()
  })

  it('confirmOnlineImport 无预览时直接返回（不置导入锁）', async () => {
    const { c } = setup()
    await c.confirmOnlineImport()
    expect(c.onlineImporting.value).toBe(false)
    expect(mocked.importFromSnapgene).not.toHaveBeenCalled()
  })
})

describe('Addgene 本地索引搜索', () => {
  it('无命中显示后端 note 提示', async () => {
    mocked.searchAddgeneIndex.mockResolvedValue({
      results: [], note: '索引未构建，请管理员导入',
    } as any)
    const { c } = setup()
    c.addgeneTerm.value = 'pCX'
    await c.doSearchAddgene()
    expect(c.addgeneRows.value).toHaveLength(0)
    expect(c.addgeneError.value).toBe('索引未构建，请管理员导入')
  })

  it('失败落 addgeneError 且 loading 复位', async () => {
    mocked.searchAddgeneIndex.mockRejectedValue({ message: 'network down' })
    const { c } = setup()
    c.addgeneTerm.value = 'pCX'
    await c.doSearchAddgene()
    expect(c.addgeneError.value).toBe('network down')
    expect(c.addgeneLoading.value).toBe(false)
  })

  it('空检索词不发起请求', async () => {
    const { c } = setup()
    c.addgeneTerm.value = '   '
    await c.doSearchAddgene()
    expect(mocked.searchAddgeneIndex).not.toHaveBeenCalled()
  })
})

describe('switchTab 联动', () => {
  it('SnapGene 未开放（未登录或后端关）时页签保持不变', async () => {
    const { c } = setup(false)   // 未登录
    c.snapgeneEnabled.value = true
    mocked.getOnlineSources.mockResolvedValue({ sources: [] } as any)
    c.switchTab('snapgene')
    await nextTick()
    expect(c.activeTab.value).toBe('local')
  })
})
