<script setup lang="ts">
/**
 * 图例 / 全局管理侧栏（模块 C）：统一展示分类色标，并提供勾选隐藏/显示。
 *
 * 分组：
 *   - 引物：正向（蓝 →）/ 反向（紫 ←），色标与图中名牌、框一致；
 *   - 特征：按类型聚合（只列出现过的类型），色标取自 featureColor；
 *   - 酶切 / ORF / 位置坐标：开关直达 store.show。
 * 可折叠：折叠后只留一行摘要，主视图腾出宽度（窄屏友好）。
 */
import { computed, ref } from 'vue'
import { useMapStore } from '../store'
import { FEATURE_COLORS, FEATURE_TYPE_LABELS, featureColor } from '../core/palette'
import { PRIMER_FWD, PRIMER_REV, isRevPrimer } from '../core/primer_style'
import type { FeatureType } from '../types'

const props = defineProps<{ store: ReturnType<typeof useMapStore>['store'] }>()
const collapsed = ref(false)

const primers = computed(() => props.store.primers.value)
const fwdCount = computed(() => primers.value.filter((p) => !isRevPrimer(p)).length)
const revCount = computed(() => primers.value.filter((p) => isRevPrimer(p)).length)
const hasPrimers = computed(() => primers.value.length > 0)

/** 特征按类型聚合（只列文档里出现过的） */
const featureGroups = computed(() => {
  const seen = new Map<FeatureType, number>()
  for (const f of props.store.doc.value.features) seen.set(f.type, (seen.get(f.type) ?? 0) + 1)
  return [...seen.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([type, count]) => ({
      type,
      count,
      label: FEATURE_TYPE_LABELS[type] ?? type,
      color: FEATURE_COLORS[type] ?? FEATURE_COLORS.misc_feature,
    }))
})

/** 示例特征（可能带用户自定义颜色）取首个用色，避免图例与图上不一致 */
function swatchOf(type: FeatureType): string {
  const f = props.store.doc.value.features.find((x) => x.type === type)
  return f ? featureColor(f) : (FEATURE_COLORS[type] ?? FEATURE_COLORS.misc_feature)
}
</script>

<template>
  <div class="mv-legend">
    <button type="button" class="mv-legend-head" :aria-expanded="!collapsed" @click="collapsed = !collapsed">
      <span class="mv-legend-title">图例</span>
      <span class="mv-muted mv-legend-sum">
        {{ hasPrimers ? `引物 ${primers.length}` : '' }}{{ hasPrimers ? ' · ' : '' }}特征 {{ store.doc.value.features.length }}
      </span>
      <span class="mv-legend-caret" aria-hidden="true">{{ collapsed ? '▸' : '▾' }}</span>
    </button>

    <div v-if="!collapsed" class="mv-legend-body">
      <section v-if="hasPrimers" class="mv-legend-group">
        <div class="mv-legend-glabel">引物</div>
        <label class="mv-legend-item">
          <input v-model="store.show.primers" type="checkbox" />
          <i class="mv-swatch mv-swatch-chip" :style="{ background: PRIMER_FWD.base, borderColor: PRIMER_FWD.dark }" />
          <span>正向 <span class="mv-muted">→</span></span>
          <span class="mv-legend-count">{{ fwdCount }}</span>
        </label>
        <label class="mv-legend-item">
          <input v-model="store.show.primers" type="checkbox" aria-label="引物" />
          <i class="mv-swatch mv-swatch-chip" :style="{ background: PRIMER_REV.base, borderColor: PRIMER_REV.dark }" />
          <span>反向 <span class="mv-muted">←</span></span>
          <span class="mv-legend-count">{{ revCount }}</span>
        </label>
        <p class="mv-legend-hint">名牌/框色即链向；图上的箭头为 5′→3′ 延伸方向</p>
      </section>

      <section class="mv-legend-group">
        <div class="mv-legend-glabel">特征</div>
        <label class="mv-legend-item">
          <input v-model="store.show.features" type="checkbox" /> 显示全部特征
          <span class="mv-legend-count">{{ store.doc.value.features.length }}</span>
        </label>
        <div v-for="g in featureGroups" :key="g.type" class="mv-legend-item mv-legend-static">
          <i class="mv-swatch" :style="{ background: swatchOf(g.type), borderColor: 'transparent' }" />
          <span>{{ g.label }}</span>
          <span class="mv-legend-count">{{ g.count }}</span>
        </div>
      </section>

      <section class="mv-legend-group">
        <div class="mv-legend-glabel">显示选项</div>
        <label class="mv-legend-item"><input v-model="store.show.enzymes" type="checkbox" /> 酶切位点</label>
        <label class="mv-legend-item"><input v-model="store.show.labels" type="checkbox" /> 标签文字</label>
        <label class="mv-legend-item"><input v-model="store.show.locations" type="checkbox" /> 位置坐标</label>
        <label class="mv-legend-item"><input v-model="store.show.orfs" type="checkbox" /> ORF</label>
        <label class="mv-legend-item"><input v-model="store.show.complement" type="checkbox" /> 互补链</label>
        <label class="mv-legend-item"><input v-model="store.show.translations" type="checkbox" /> 翻译</label>
      </section>
    </div>
  </div>
</template>

<style scoped>
.mv-legend { font-size: 12px; }
.mv-legend-head {
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 2px 0;
  background: none; border: 0; cursor: pointer; color: var(--text-primary); text-align: left;
}
.mv-legend-title { font-weight: 600; }
.mv-legend-sum { flex: 1; font-size: 11px; }
.mv-legend-caret { color: var(--text-secondary); }
.mv-legend-body { display: flex; flex-direction: column; gap: 10px; padding: 4px 0 2px; }
.mv-legend-group { display: flex; flex-direction: column; gap: 3px; }
.mv-legend-glabel {
  font-size: 10.5px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
  color: var(--text-secondary); margin-bottom: 1px;
}
.mv-legend-item {
  display: flex; align-items: center; gap: 6px; line-height: 1.6; cursor: pointer;
  color: var(--text-primary);
}
.mv-legend-item.mv-legend-static { cursor: default; padding-left: 18px; }
.mv-legend-count { margin-left: auto; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
.mv-swatch {
  width: 11px; height: 11px; border-radius: 2px; flex: none;
  border: 1px solid transparent; box-sizing: border-box;
}
.mv-swatch-chip { border-radius: 3px; }
.mv-legend-hint { margin: 2px 0 0; font-size: 10.5px; line-height: 1.4; color: var(--text-secondary); }
</style>
