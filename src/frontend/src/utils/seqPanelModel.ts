/**
 * Sanger 融合视图（SequencingPanel）纯函数模型
 *
 * 从 SequencingPanel.vue 抽离的可独立单测部分：
 * - buildSeqCols: alignment_view → 逐列列模型（含插入列插值坐标）
 * - packLanes:    read 覆盖区段 → 泳道贪心装箱
 *
 * 组件保留 ref/computed 壳与 canvas 绘制；无状态纯逻辑都在这里。
 * 类型用结构兼容的最小面（与 @/api 的 SequencingAnalysis 对齐）。
 */

/** 一列 = 参考方向的一个对齐列（read 缺口列 read='-'） */
export interface SeqCol {
  ref: string
  read: string
  q: number
  mm: boolean
  ins: boolean
  refPos: number
  /** 原始电泳顺序的 read 碱基下标（0-based；read 缺口列 = -1） */
  origIdx: number
  /** 横轴坐标（参考 bp 单位；插入列在缝内插值） */
  xu: number
  xEnd: number
}

/** buildSeqCols 输入的最小类型面（与 @/api 的 AlignmentView/Read 结构兼容） */
export interface AlignmentViewLike {
  ref_start: number
  ref_aligned: string
  read_aligned: string
  q_aligned?: number[] | null
}

export interface ReadLike {
  trimmed_length: number
  direction: string
  alignment_view?: AlignmentViewLike | null
}

/**
 * alignment_view → 逐列列模型。
 * 反向 read 的 query 是 revcomp：原始下标 = L-1-query 下标（与后端镜像同式）。
 * 常规列落在其参考 bp 中心（refPos-0.5）；连续插入列在左右两列之间等分插缝；
 * 末列 xEnd = 末列 xu + 1。
 */
export function buildSeqCols(read: ReadLike): SeqCol[] | null {
  const av = read.alignment_view
  if (!av || !av.ref_aligned) return null
  const L = read.trimmed_length
  const cols: SeqCol[] = []
  let refPos = av.ref_start
  let qi = -1
  for (let i = 0; i < av.ref_aligned.length; i++) {
    const rb = av.ref_aligned[i]
    const qb = av.read_aligned[i]
    if (qb !== '-') qi++
    // 反向 read 的 query 是 revcomp：原始下标 = L-1-query 下标（与后端镜像同式）
    const origIdx = qb === '-' ? -1 : (read.direction === '-' ? L - 1 - qi : qi)
    cols.push({
      ref: rb, read: qb, q: av.q_aligned?.[i] ?? 0,
      mm: rb !== '-' && qb !== '-' && rb !== qb,
      ins: rb === '-' && qb !== '-',
      refPos: rb !== '-' ? refPos : 0,
      origIdx, xu: 0, xEnd: 0,
    })
    if (rb !== '-') refPos++
  }
  // 横轴坐标：常规列落在其参考 bp 中心；连续插入列在左右两列之间等分插缝
  let i = 0
  let prevXu = -1
  while (i < cols.length) {
    if (!cols[i].ins) {
      cols[i].xu = cols[i].refPos - 0.5
      prevXu = cols[i].xu
      i++
      continue
    }
    let j = i
    while (j < cols.length && cols[j].ins) j++
    const nextXu = j < cols.length ? cols[j].refPos - 0.5 : prevXu + 1
    const n = j - i
    for (let m = i; m < j; m++) cols[m].xu = prevXu + (nextXu - prevXu) * ((m - i + 1) / (n + 1))
    i = j
  }
  for (let k = 0; k < cols.length; k++) {
    cols[k].xEnd = k + 1 < cols.length ? cols[k + 1].xu : cols[k].xu + 1
  }
  return cols
}

/**
 * 泳道贪心装箱：按 ref_start 排序依次放入第一条“尾 < 当前头”的泳道。
 * 无对齐 read（ref_end<=0）不上图（laneOf 保持 -1）。
 */
export function packLanes(
  reads: Array<{ ref_start: number; ref_end: number }>
): { lanes: number[][]; laneOf: number[] } {
  const lanes: number[][] = []
  const laneOf: number[] = reads.map(() => -1)
  const order = reads.map((_, i) => i).sort((x, y) => reads[x].ref_start - reads[y].ref_start)
  for (const i of order) {
    if (reads[i].ref_end <= 0) continue   // 无对齐 read 没有落点，不上图
    let placed = false
    for (let l = 0; l < lanes.length; l++) {
      if (lanes[l].every((j) => reads[j].ref_end < reads[i].ref_start)) {
        lanes[l].push(i); laneOf[i] = l; placed = true; break
      }
    }
    if (!placed) { lanes.push([i]); laneOf[i] = lanes.length - 1 }
  }
  return { lanes, laneOf }
}
