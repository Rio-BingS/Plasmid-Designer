/**
 * 在线序列来源（SnapGene 在线库 / Ensembl / Addgene / UniProt）的状态与动作。
 * 从 VectorsView.vue 迁出（纯搬运）：载体库页之外（如图谱页）暂不复用，
 * 抽出是为了让视图只留 UI 编排。
 *
 * reload：导入成功后刷新载体列表（调用方注入，如 loadVectors）。
 */
import { computed, ref, type Ref } from 'vue'
import {
  getAddgeneMetadata, getOnlineSources, getSnapgeneSetIndex, getSnapgeneSets,
  getUniprotEntry, importFromAddgene, importFromEnsembl, importFromSnapgene,
  previewEnsembl, previewSnapgene, searchAddgeneIndex, searchSnapgene,
  searchUniprot, gatewayErrorText,
} from '@/api'
import type {
  AddgeneIndexRow, AddgeneMetadata, OnlinePreview, OnlineSourceInfo,
  SnapgenePlasmidRow, UniProtEntry, UniProtNucleotide,
} from '@/api'

/** 统一的错误消息提取（网关文案 → 后端 detail → 异常 message → 兜底） */
function errTextOf(e: unknown, fallback: string): string {
  const err = e as { response?: { data?: { detail?: string } }; message?: string }
  return gatewayErrorText(e) || err?.response?.data?.detail || err?.message || fallback
}

export function useOnlineImport(opts: { loggedIn: Ref<boolean>; reload: () => Promise<void> | void }) {
  const { loggedIn, reload } = opts

  // 在线序列来源（SnapGene 在线库 / Ensembl / Addgene）
  // 许可提示随 /vectors/import/sources 下发；Addgene 后端明确不绕登录，前端只做引导
  const onlineSources = ref<OnlineSourceInfo[]>([])
  const onlineNotice = ref('')
  const onlineMsg = ref('')
  const onlinePreview = ref<OnlinePreview | null>(null)
  const showOnlinePreview = ref(false)
  // SnapGene 在线库
  const sgSets = ref<{ set_id: string; name: string }[]>([])
  const sgSetId = ref('')
  const sgRows = ref<SnapgenePlasmidRow[]>([])
  const sgLoading = ref(false)
  const sgError = ref('')
  /** 非空 = 正在显示跨集合搜索结果（集合框里输入的是质粒名） */
  const sgSearch = ref('')
  /** 预览弹窗对应的集合（搜索结果来自不同集合，不能用当前集合） */
  const sgPreviewSetId = ref('')
  // SnapGene 在线库默认开启（SNAPGENE_ONLINE_ENABLED=false 可关）且只对登录用户开放：
  // 两者都满足才显示页签（后端仍是权威：关 → 404，匿名 → 401）
  const snapgeneEnabled = ref(false)
  const snapgeneAvailable = computed(() => snapgeneEnabled.value && loggedIn.value)
  // Ensembl
  const ensemblId = ref('')
  const ensemblType = ref('cds')
  const ensemblLoading = ref(false)
  // Addgene
  const addgeneTerm = ref('')
  const addgeneRows = ref<AddgeneIndexRow[]>([])
  const addgeneMeta = ref<AddgeneMetadata | null>(null)
  const addgeneError = ref('')
  const addgeneLoading = ref(false)
  let addgeneGen = 0 // 请求代次：慢响应不得覆盖新请求的结果（同 SnapGene 的 sgIndexGen）
  // Addgene 全长序列导入（需管理员 + 后端已配置用户自备会话）
  const addgeneImporting = ref(false)
  /** SnapGene / Ensembl 在线导入在途锁：防双击重复导入（或第二次得到 409） */
  const onlineImporting = ref(false)
  // UniProt（蛋白库反查：条目 → 核酸交叉引用 → 去 NCBI/Ensembl 取 DNA）
  const uniprotQuery = ref('')
  const uniprotReviewed = ref(false)
  const uniprotRows = ref<UniProtEntry[]>([])
  const uniprotEntry = ref<UniProtEntry | null>(null)
  const uniprotNucleotide = ref<UniProtNucleotide[]>([])
  const uniprotError = ref('')
  const uniprotLoading = ref(false)

  const snapgeneLicense = computed(
    () => onlineSources.value.find(s => s.id === 'snapgene')?.license || '')
  const ensemblLicense = computed(
    () => onlineSources.value.find(s => s.id === 'ensembl')?.license || '')
  const addgeneLicense = computed(
    () => onlineSources.value.find(s => s.id === 'addgene')?.license || '')
  const uniprotLicense = computed(
    () => onlineSources.value.find(s => s.id === 'uniprot')?.license || '')
  // 部署方是否配了 Addgene 会话（决定「导入为本地载体」按钮是否可用）
  const addgeneSession = computed(
    () => onlineSources.value.find(s => s.id === 'addgene')?.session_configured === true)

  async function ensureOnlineSources() {
    if (onlineSources.value.length) return
    try {
      const res = await getOnlineSources()
      onlineSources.value = res.sources || []
      onlineNotice.value = res.disclaimer || ''
      snapgeneEnabled.value = res.snapgene_online_enabled === true
    } catch {
      // 未登录/未开放功能时静默：各面板自己的错误位会显示具体原因
    }
  }

  function switchTab(tab: 'local' | 'ncbi' | 'snapgene' | 'ensembl' | 'addgene' | 'uniprot') {
    if (tab === 'snapgene' && !snapgeneAvailable.value) return
    activeTab.value = tab
    onlineMsg.value = ''
    ensureOnlineSources()
    if (tab === 'snapgene' && sgSets.value.length === 0 && !sgLoading.value) loadSnapgeneSets()
  }

  async function loadSnapgeneSets() {
    sgLoading.value = true
    sgError.value = ''
    try {
      const res = await getSnapgeneSets()
      sgSets.value = res.sets || []
      if (!sgSetId.value && sgSets.value.length) sgSetId.value = sgSets.value[0].set_id
      if (sgSetId.value) await loadSnapgeneIndex()
    } catch (e) {
      sgError.value = errTextOf(e, '在线库集合加载失败')
    } finally {
      sgLoading.value = false
    }
  }

  // 集合索引请求号：快速切换集合时只认最后一次请求，乱序返回的旧集合不覆盖列表
  let sgIndexGen = 0
  async function loadSnapgeneIndex() {
    if (!sgSetId.value) return
    sgSearch.value = ''
    const gen = ++sgIndexGen
    sgLoading.value = true
    sgError.value = ''
    sgRows.value = []
    try {
      const res = await getSnapgeneSetIndex(sgSetId.value)
      if (gen === sgIndexGen) sgRows.value = res.plasmids || []
    } catch (e) {
      if (gen === sgIndexGen) sgError.value = errTextOf(e, '集合索引加载失败')
    } finally {
      if (gen === sgIndexGen) sgLoading.value = false
    }
  }

  /** 集合框输入的不是集合：按质粒名跨集合搜索（如 pET-30a） */
  async function searchSnapgeneByName(q: string) {
    const gen = ++sgIndexGen
    sgSearch.value = q
    sgLoading.value = true
    sgError.value = ''
    sgRows.value = []
    try {
      const res = await searchSnapgene(q)
      if (gen === sgIndexGen) sgRows.value = res.plasmids || []
    } catch (e) {
      if (gen === sgIndexGen) sgError.value = errTextOf(e, '搜索失败')
    } finally {
      if (gen === sgIndexGen) sgLoading.value = false
    }
  }

  function rowSetId(row: SnapgenePlasmidRow): string {
    return row?.set_id || sgSetId.value
  }

  async function openSnapgenePreview(row: SnapgenePlasmidRow) {
    onlineMsg.value = ''
    try {
      sgPreviewSetId.value = rowSetId(row)
      onlinePreview.value = await previewSnapgene(sgPreviewSetId.value, row.plasmid_id)
      showOnlinePreview.value = true
    } catch (e) {
      onlineMsg.value = `预览失败: ${errTextOf(e, '')}`
    }
  }

  /** 列表里直接导入（省去预览一步） */
  async function importSnapgeneRow(row: SnapgenePlasmidRow) {
    if (onlineImporting.value) return
    onlineImporting.value = true
    onlineMsg.value = ''
    try {
      const res = await importFromSnapgene(rowSetId(row), row.plasmid_id)
      onlineMsg.value = `已导入 ${res.vector.name}（${res.vector.length} bp）`
      await reload()
    } catch (e) {
      onlineMsg.value = `导入失败: ${errTextOf(e, '')}`
    } finally {
      onlineImporting.value = false
    }
  }

  async function openEnsemblPreview() {
    if (!ensemblId.value.trim()) return
    ensemblLoading.value = true
    onlineMsg.value = ''
    try {
      onlinePreview.value = await previewEnsembl(ensemblId.value.trim(), ensemblType.value)
      showOnlinePreview.value = true
    } catch (e) {
      onlineMsg.value = `预览失败: ${errTextOf(e, '')}`
    } finally {
      ensemblLoading.value = false
    }
  }

  /** 预览弹窗里的"确认导入"：只对可导入来源生效（Addgene 不在此列） */
  async function confirmOnlineImport() {
    const p = onlinePreview.value
    if (!p || onlineImporting.value) return
    onlineImporting.value = true
    onlineMsg.value = ''
    try {
      if (p.source.startsWith('ensembl')) {
        const res = await importFromEnsembl(p.id, ensemblType.value)
        onlineMsg.value = `已导入 ${res.vector.name}（${res.vector.length} bp）`
      } else {
        const res = await importFromSnapgene(sgPreviewSetId.value || sgSetId.value, p.id)
        onlineMsg.value = `已导入 ${res.vector.name}（${res.vector.length} bp）`
      }
      showOnlinePreview.value = false
      await reload()
    } catch (e) {
      onlineMsg.value = `导入失败: ${errTextOf(e, '')}`
    } finally {
      onlineImporting.value = false
    }
  }

  async function doSearchAddgene() {
    if (!addgeneTerm.value.trim()) return
    const gen = ++addgeneGen
    addgeneLoading.value = true
    addgeneError.value = ''
    addgeneRows.value = []
    addgeneMeta.value = null
    try {
      const res = await searchAddgeneIndex(addgeneTerm.value.trim())
      if (gen !== addgeneGen) return // 已有更新的搜索，丢弃迟到的旧响应
      addgeneRows.value = res.results || []
      if (addgeneRows.value.length === 0) addgeneError.value = res.note || '本地索引无命中'
    } catch (e) {
      if (gen !== addgeneGen) return
      addgeneError.value = errTextOf(e, 'Addgene 搜索失败')
    } finally {
      if (gen === addgeneGen) addgeneLoading.value = false
    }
  }

  async function doAddgeneMetadata(id: string) {
    const gen = ++addgeneGen
    addgeneLoading.value = true
    addgeneError.value = ''
    addgeneMeta.value = null
    try {
      const meta = await getAddgeneMetadata(id, addgeneSession.value)
      if (gen !== addgeneGen) return
      addgeneMeta.value = meta
    } catch (e) {
      if (gen !== addgeneGen) return
      addgeneError.value = errTextOf(e, 'Addgene 元数据查询失败')
    } finally {
      if (gen === addgeneGen) addgeneLoading.value = false
    }
  }

  /** 用部署方配置的（用户自备）Addgene 会话把全长序列导入载体库 */
  async function doAddgeneImport(id: string) {
    const pid = String(id || '').replace('#', '').trim()
    if (!pid || addgeneImporting.value) return
    addgeneImporting.value = true
    onlineMsg.value = ''
    addgeneError.value = ''
    try {
      const res = await importFromAddgene(pid)
      onlineMsg.value = `已导入 ${res.vector.name}（${res.vector.length} bp，${res.vector.features} 个特征）`
      await reload()
    } catch (e) {
      addgeneError.value = `导入失败: ${errTextOf(e, '')}`
    } finally {
      addgeneImporting.value = false
    }
  }

  // -------------------- UniProt（蛋白库：只反查，不给质粒序列） --------------------

  async function doSearchUniprot() {
    const q = uniprotQuery.value.trim()
    if (!q) return
    uniprotLoading.value = true
    uniprotError.value = ''
    uniprotRows.value = []
    uniprotEntry.value = null
    uniprotNucleotide.value = []
    try {
      const res = await searchUniprot(q, 10, uniprotReviewed.value)
      uniprotRows.value = res.results || []
      if (uniprotRows.value.length === 0) uniprotError.value = 'UniProt 无命中'
    } catch (e) {
      uniprotError.value = errTextOf(e, 'UniProt 检索失败')
    } finally {
      uniprotLoading.value = false
    }
  }

  async function openUniprotEntry(accession: string) {
    if (!accession) return
    uniprotLoading.value = true
    uniprotError.value = ''
    uniprotEntry.value = null
    uniprotNucleotide.value = []
    try {
      const res = await getUniprotEntry(accession)
      uniprotEntry.value = res.entry
      uniprotNucleotide.value = res.nucleotide || []
    } catch (e) {
      uniprotError.value = errTextOf(e, 'UniProt 条目查询失败')
    } finally {
      uniprotLoading.value = false
    }
  }

  // 页签状态也在本 composable：switchTab 要联动在线来源的懒加载
  const activeTab = ref<'local' | 'ncbi' | 'snapgene' | 'ensembl' | 'addgene' | 'uniprot'>('local')

  return {
    activeTab,
    onlineSources, onlineNotice, onlineMsg, onlinePreview, showOnlinePreview,
    snapgeneEnabled, snapgeneAvailable, snapgeneLicense, addgeneLicense,
    sgSets, sgSetId, sgRows, sgLoading, sgError, sgSearch, sgPreviewSetId,
    ensemblId, ensemblType, ensemblLoading, ensemblLicense,
    addgeneTerm, addgeneRows, addgeneMeta, addgeneError, addgeneLoading,
    addgeneImporting, addgeneSession, onlineImporting,
    uniprotQuery, uniprotReviewed, uniprotRows, uniprotEntry,
    uniprotNucleotide, uniprotError, uniprotLoading, uniprotLicense,
    ensureOnlineSources, switchTab, loadSnapgeneSets, loadSnapgeneIndex,
    searchSnapgeneByName, openSnapgenePreview, importSnapgeneRow,
    openEnsemblPreview, confirmOnlineImport, doSearchAddgene,
    doAddgeneMetadata, doAddgeneImport, doSearchUniprot, openUniprotEntry,
  }
}
