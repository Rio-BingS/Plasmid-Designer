<script setup lang="ts">
/**
 * 环外引物标签：圆角名牌 + 旁侧坐标。
 *
 * 视觉（模块 B/D）：
 *   - 正/反向分色：正向蓝 #2563EB、反向紫 #7C3AED（白字对比度 5.17 / 5.70，AA 达标）；
 *   - 名牌内加方向符号（→ / ←），颜色之外再叠一层形状编码，色觉异常也可区分；
 *   - 字重 600、字距 -0.01em，小字号下不发虚。
 */
import { computed } from 'vue'
import { textWidth } from '../core/text'
import { primerPalette, type PrimerPalette } from '../core/primer_style'

const props = defineProps<{
  /** 名牌名 */
  name: string
  /** 坐标文本（含括号与前导空格）；空串则不显示 */
  pos: string
  /** 名牌文字宽（含方向符号，定宽用） */
  nameW: number
  /** 布局给出的标签框（靠环一侧的竖边 x / 垂直中心 y / 高） */
  box: { x: number; y: number; w: number; h: number }
  y: number
  /** 标签在环的左(-1)/右(1)列 */
  side: 1 | -1
  fontSize: number
  posFont: number
  hot: boolean
  /** 反向引物（紫色 + ←） */
  rev: boolean
}>()

/** 名牌左右内边距 */
const CHIP_PAD = 4
/** 名牌与坐标之间的间隙 */
const POS_GAP = 4

const pal = computed<PrimerPalette>(() => (props.rev ? primerPalette({ name: 'AS', strand: -1 }) : primerPalette({ name: 'S', strand: 1 })))
const arrow = computed(() => (props.rev ? '←' : '→'))
const label = computed(() => (props.rev ? `${arrow.value} ${props.name}` : `${props.name} ${arrow.value}`))
/**
 * 名牌宽直接用布局传入的 nameW（已含方向符号，由 core/primer_labels 实测），
 * 不再在此处二次量宽——两处量宽口径稍有差异就会把文字裁掉（曾出现
 * `← C01_AS2(`）。
 */
const chipW = computed(() => props.nameW + 2 * CHIP_PAD)
/** 左侧标签把坐标排在名牌左边，故名牌整体右移 */
const chipX = computed(() => props.box.x + (props.side === -1 && props.pos ? CHIP_PAD + textWidth(props.pos, props.posFont) + POS_GAP : 0))
</script>

<template>
  <text v-if="pos && side === -1" :x="box.x - POS_GAP" :y="y" :font-size="posFont"
        dominant-baseline="central" text-anchor="end" fill="#6b7280" class="cmap-label-text"
        style="letter-spacing: -0.01em">{{ pos }}</text>
  <rect :x="chipX" :y="box.y + 1" :width="chipW" :height="box.h - 2" rx="3"
        :fill="pal.base" :stroke="hot ? '#4f46e5' : pal.dark" :stroke-width="hot ? 1.4 : 0.8" />
  <text v-if="pos && side === 1" :x="chipX + chipW + POS_GAP" :y="y" :font-size="posFont"
        dominant-baseline="central" text-anchor="start" fill="#6b7280" class="cmap-label-text"
        style="letter-spacing: -0.01em">{{ pos }}</text>
  <text :x="chipX + chipW / 2" :y="y" :font-size="fontSize" dominant-baseline="central" text-anchor="middle"
        fill="#ffffff" font-weight="600" class="cmap-label-text" style="letter-spacing: -0.01em">{{ label }}</text>
</template>
