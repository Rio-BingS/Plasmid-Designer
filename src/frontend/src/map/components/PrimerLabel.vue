<script setup lang="ts">
/**
 * 环外引物标签：蓝底白字圆角名牌 + 旁侧坐标。
 * 按用户提供的 SnapGene 图谱截图复刻（名牌 + `(280 .. 325)`），
 * 22 条 oligo 全部标出、沿环外堆列。
 */
import { computed } from 'vue'
import { textWidth } from '../core/text'
import { PRIMER_CHIP_FILL, PRIMER_CHIP_INK, PRIMER_CHIP_PAD, PRIMER_CHIP_STROKE, PRIMER_POS_GAP } from '../core/primer_labels'

const props = defineProps<{
  /** 名牌名 */
  name: string
  /** 坐标文本（含括号与前导空格）；空串则不显示 */
  pos: string
  /** 名牌文字宽（定宽用） */
  nameW: number
  /** 布局给出的标签框（靠环一侧的竖边 x / 垂直中心 y / 高） */
  box: { x: number; y: number; w: number; h: number }
  y: number
  /** 标签在环的左(-1)/右(1)列 */
  side: 1 | -1
  fontSize: number
  posFont: number
  hot: boolean
}>()

/** 左侧标签把坐标排在名牌左边，故名牌整体右移 */
const chipX = computed(() => props.box.x + (props.side === -1 && props.pos ? PRIMER_CHIP_PAD + textWidth(props.pos, props.posFont) + PRIMER_POS_GAP : 0))
const chipW = computed(() => props.nameW + 2 * PRIMER_CHIP_PAD)
</script>

<template>
  <text v-if="pos && side === -1" :x="box.x - PRIMER_POS_GAP" :y="y" :font-size="posFont"
        dominant-baseline="central" text-anchor="end" fill="#6b7280" class="cmap-label-text">{{ pos }}</text>
  <rect :x="chipX" :y="box.y + 1" :width="chipW" :height="box.h - 2" rx="3"
        :fill="PRIMER_CHIP_FILL" :stroke="hot ? '#4f46e5' : PRIMER_CHIP_STROKE" :stroke-width="hot ? 1.2 : 0.8" />
  <text v-if="pos && side === 1" :x="chipX + chipW + PRIMER_POS_GAP" :y="y" :font-size="posFont"
        dominant-baseline="central" text-anchor="start" fill="#6b7280" class="cmap-label-text">{{ pos }}</text>
  <text :x="chipX + chipW / 2" :y="y" :font-size="fontSize" dominant-baseline="central" text-anchor="middle"
        :fill="PRIMER_CHIP_INK" font-weight="600" class="cmap-label-text">{{ name }}</text>
</template>
