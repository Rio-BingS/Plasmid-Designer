<script setup lang="ts">
/**
 * 证据包面板（goal T22；A 5.4）
 *
 * 点击变体/未决位点/混合位点行时多 read 堆叠对齐显示：该位 ±10 峰的四通道
 * 小图（后端纯 Python 生成 SVG）+ 调用/Q/峰强比/评分/伪迹表与判定理由。
 * Markdown 证据包可整包下载（单样品，脱离系统后仍可核对）。
 */
import { ref, watch } from 'vue'
import {
  downloadEvidencePack, getEvidencePack,
  type EvidencePackData,
} from '@/api'

const props = defineProps<{
  analysisId: string
  sampleName: string
  focusRefPos: number | null
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const pack = ref<EvidencePackData | null>(null)
const loading = ref(false)
const errorMsg = ref('')
const current = ref<number | null>(null)   // 当前展示的位点 ref_pos

watch(() => props.focusRefPos, async (pos) => {
  if (pos == null) return
  current.value = pos
  if (!pack.value) {
    loading.value = true
    errorMsg.value = ''
    try {
      pack.value = await getEvidencePack(props.analysisId)
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }
}, { immediate: true })

function kindText(kind: string): string {
  if (kind.startsWith('variant_')) return kind === 'variant_resequence' ? '建议重测' : '建议核对峰图'
  const map: Record<string, string> = {
    confirmed_variant: '确证差异', unresolved_variant: '未决差异',
    unresolved_site: '未决位点', overlap_discordance: '重叠区分歧',
    primer_mismatch: '引物校验', orf_annotation_suspect: 'ORF 标注疑点',
    critical_element_variant: '关键元件差异', junction_variant: '接合区差异',
    unaligned_hq_segment: '端部未比对', structural_breakpoint: '结构断点',
  }
  return map[kind] ?? kind
}

function artifactText(a: string | null | undefined): string {
  const map: Record<string, string> = {
    compression: '压缩区', dye_blob: '染料团', short_slippage: '短滑移',
  }
  return a ? (map[a] ?? a) : '-'
}

async function download(): Promise<void> {
  await downloadEvidencePack(props.analysisId, props.sampleName)
}
</script>

<template>
  <div class="pack-overlay" @click.self="emit('close')">
    <div class="pack-panel">
      <div class="pack-head">
        <h4 class="section-title">证据包{{ current ? `：位点 ${current}` : '' }}</h4>
        <div class="pack-actions">
          <button class="btn btn-sm" :disabled="loading" @click="download">下载 Markdown 证据包</button>
          <button class="btn btn-sm" @click="emit('close')">关闭</button>
        </div>
      </div>
      <div v-if="errorMsg" class="pack-error">{{ errorMsg }}</div>
      <div v-else-if="loading" class="pack-loading">正在生成证据包…</div>
      <template v-else-if="pack">
        <div class="pack-sites">
          <button v-for="s in pack.sites" :key="`${s.ref_pos}-${s.kind}`"
                  class="chip" :class="{ active: s.ref_pos === current }"
                  @click="current = s.ref_pos">
            {{ s.ref_pos }} · {{ kindText(s.kind) }}
          </button>
          <span v-if="!pack.sites.length" class="pack-empty">无需要出图的位点</span>
        </div>
        <template v-for="s in pack.sites" :key="`body-${s.ref_pos}-${s.kind}`">
          <div v-if="s.ref_pos === current" class="pack-body">
            <p class="pack-detail">{{ s.detail || s.label }}</p>
            <table class="seq-table">
              <thead>
                <tr><th>read</th><th>read 位</th><th>调用</th><th>Q</th>
                    <th>峰强比</th><th title="T15 规则评分（0-100，非校准概率）">评分</th><th>伪迹</th></tr>
              </thead>
              <tbody>
                <tr v-for="r in s.reads" :key="r.read">
                  <td>{{ r.read }}</td>
                  <td>{{ r.read_pos ?? '-' }}</td>
                  <td class="mono">{{ r.call || '-' }}</td>
                  <td>{{ r.q ?? '-' }}</td>
                  <td>{{ r.mutant_pct ?? '-' }}</td>
                  <td>{{ r.p_real != null ? Math.round(r.p_real * 100) : '-' }}</td>
                  <td>{{ artifactText(r.artifact) }}</td>
                </tr>
              </tbody>
            </table>
            <div v-for="r in s.reads" :key="`svg-${r.read}`" class="pack-svg">
              <div class="pack-read-name">{{ r.read }}（位点 ±10 峰，A 绿 / C 蓝 / G 黑 / T 红）</div>
              <div v-if="r.svg" class="pack-svg-box" v-html="r.svg"></div>
              <div v-else class="pack-nosvg">该 read 无峰图数据</div>
            </div>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.pack-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 60;
  display: flex; align-items: center; justify-content: center; }
.pack-panel { background: var(--bg, #fff); border-radius: 8px; max-width: 860px;
  width: min(92vw, 860px); max-height: 86vh; overflow: auto; padding: 14px 16px; }
.pack-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.pack-actions { display: flex; gap: 8px; }
.pack-sites { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
.chip { border: 1px solid var(--border, #ccc); background: transparent; border-radius: 999px;
  padding: 2px 10px; cursor: pointer; font-size: 12px; }
.chip.active { background: var(--accent, #2563eb); color: #fff; border-color: transparent; }
.pack-body { margin-top: 6px; }
.pack-detail { color: var(--text-2, #555); margin: 4px 0 10px; }
.pack-svg { margin: 10px 0; }
.pack-read-name { font-size: 12px; color: var(--text-2, #555); margin-bottom: 2px; }
.pack-svg-box :deep(svg) { width: 100%; height: auto; border: 1px solid var(--border, #e2e2e2);
  border-radius: 4px; }
.pack-nosvg, .pack-empty, .pack-loading { color: var(--text-3, #888); font-size: 13px; }
.pack-error { color: #d0342c; font-size: 13px; }
</style>
