/**
 * Sanger 融合视图（SequencingPanel）纯函数模型
 *
 * 从 SequencingPanel.vue 抽离的可独立单测部分：
 * - buildSeqCols: alignment_view → 逐列列模型（含插入列插值坐标）
 * - packLanes:    read 覆盖区段 → 泳道贪心装箱
 * - readSegments / readOverlaps / readSpanText: 环状参考跨原点 read 的折回区段
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
  /** 对齐块在原始电泳 read 内的 1-based 起止（终审 A-20：软剪切偏移）。 */
  query_start?: number | null
  query_end?: number | null
}

/** read 落点的最小类型面：环状参考跨原点时 ref_end 为展开坐标（> 参考长度），
 *  ref_segments 为后端折回后的区段（[s, L] 与 [1, e-L]）；线性 read 无此字段 */
export interface SpanLike {
  ref_start: number
  ref_end: number
  ref_segments?: [number, number][] | null
}

/** read 在参考上的覆盖区段（均落在 1..L）；无对齐（ref_end<=0）返回 [] */
export function readSegments(r: SpanLike): [number, number][] {
  if (r.ref_end <= 0) return []
  if (r.ref_segments && r.ref_segments.length) {
    return r.ref_segments.map(([s, e]) => [s, e] as [number, number])
  }
  return [[r.ref_start, r.ref_end]]
}

/** read 任一覆盖区段与参考区间 [lo, hi] 相交 */
export function readOverlaps(r: SpanLike, lo: number, hi: number): boolean {
  return readSegments(r).some(([s, e]) => e >= lo && s <= hi)
}

/** 落点文本：线性「s–e」；跨原点「s–L、1–e'」 */
export function readSpanText(r: SpanLike, sep = '–'): string {
  const segs = readSegments(r)
  if (!segs.length) return `${r.ref_start}${sep}${r.ref_end}`
  return segs.map(([s, e]) => `${s}${sep}${e}`).join('、')
}

/**
 * alignment_view → 逐列列模型。
 * 反向 read 的 query 是 revcomp：原始下标 = L-1-query 下标（与后端镜像同式）。
 * 常规列落在其参考 bp 中心（refPos-0.5）；连续插入列在左右两列之间等分插缝；
 * 末列 xEnd = 末列 xu + 1。
 * 终审 A-20：局部比对会把 read 端部 junk/低质量段软剪切——对齐内相对列号
 * qi 不是原始 read 下标，需加 query_start 偏移（正向）；反向 read 对齐块
 * 第 0 列对应原始电泳 query_end 位置，向左递减。
 * refLen>0 时按环状参考折回：跨原点 read 的展开坐标（> refLen）折回 1..refLen，
 * 原点后的列从参考起点重新排布（线性 read 坐标从不超过 refLen，不受影响）。
 */
export function buildSeqCols(read: ReadLike, refLen = 0): SeqCol[] | null {
  const av = read.alignment_view
  if (!av || !av.ref_aligned) return null
  const L = read.trimmed_length
  const cols: SeqCol[] = []
  let refPos = av.ref_start
  let qi = -1
  const qs = typeof read.query_start === 'number' ? read.query_start : 1
  const qe = typeof read.query_end === 'number' ? read.query_end : L
  const blockFirst = read.direction === '-' ? qe : qs
  for (let i = 0; i < av.ref_aligned.length; i++) {
    const rb = av.ref_aligned[i]
    const qb = av.read_aligned[i]
    if (qb !== '-') qi++
    // 原始电泳下标（0-based）：正向 = blockFirst-1+qi；反向 = blockFirst-1-qi
    const origIdx = qb === '-' ? -1 : (read.direction === '-' ? blockFirst - 1 - qi : blockFirst - 1 + qi)
    cols.push({
      ref: rb, read: qb, q: av.q_aligned?.[i] ?? 0,
      mm: rb !== '-' && qb !== '-' && rb !== qb,
      ins: rb === '-' && qb !== '-',
      refPos: rb !== '-' ? (refLen > 0 && refPos > refLen ? refPos - refLen : refPos) : 0,
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
    let nextXu = j < cols.length ? cols[j].refPos - 0.5 : prevXu + 1
    // 插入恰在环状原点（L 与 1 之间）：右侧列折回到起点，插缝取左翼之后
    if (nextXu < prevXu) nextXu = prevXu + 1
    const n = j - i
    for (let m = i; m < j; m++) cols[m].xu = prevXu + (nextXu - prevXu) * ((m - i + 1) / (n + 1))
    i = j
  }
  for (let k = 0; k < cols.length; k++) {
    const nx = k + 1 < cols.length ? cols[k + 1].xu : cols[k].xu + 1
    // 环状折回处下一列跳回起点：本列宽度按 1bp 计
    cols[k].xEnd = nx > cols[k].xu ? nx : cols[k].xu + 1
  }
  return cols
}

/**
 * 泳道贪心装箱：按 ref_start 排序依次放入第一条“尾 < 当前头”的泳道。
 * 无对齐 read（ref_end<=0）不上图（laneOf 保持 -1）。
 */
export function packLanes(
  reads: SpanLike[]
): { lanes: number[][]; laneOf: number[] } {
  const lanes: number[][] = []
  const laneOf: number[] = reads.map(() => -1)
  const order = reads.map((_, i) => i).sort((x, y) => reads[x].ref_start - reads[y].ref_start)
  for (const i of order) {
    if (reads[i].ref_end <= 0) continue   // 无对齐 read 没有落点，不上图
    let placed = false
    for (let l = 0; l < lanes.length; l++) {
      // 互不相交才同道；跨原点 read 按折回的两段判断（线性即 ref_end_j < ref_start_i）
      if (lanes[l].every((j) => !segmentsOverlap(reads[j], reads[i]))) {
        lanes[l].push(i); laneOf[i] = l; placed = true; break
      }
    }
    if (!placed) { lanes.push([i]); laneOf[i] = lanes.length - 1 }
  }
  return { lanes, laneOf }
}

function segmentsOverlap(a: SpanLike, b: SpanLike): boolean {
  const sb = readSegments(b)
  return readSegments(a).some(([s1, e1]) => sb.some(([s2, e2]) => e1 >= s2 && e2 >= s1))
}

/**
 * 长文件名截断：>18 字符取前 17 位加省略号。
 * 匹配简图标签 / 峰图字母行芯片 / 工具栏复选框三处共用同一口径。
 */
export function shortName(name: string): string {
  return name.length > 18 ? name.slice(0, 17) + '…' : name
}
