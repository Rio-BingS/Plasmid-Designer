/** read 可信区边界（read 坐标 bp）：与后端 core/sanger/reliable_zone.ZoneParams 同口径 */
export interface ReliableZone {
  /** 首端硬不可信区终点：1..frontHard 一律不可信 */
  frontHard: number
  /** 首端灰区终点：frontHard+1..frontGray 按 Q 值与峰型逐条判定 */
  frontGray: number
  /** 尾端灰区起点前一位：tailGray 之后默认不可信，靠其他引物覆盖 */
  tailGray: number
  /** 尾端硬不可信区起点前一位：tailHard 之后一律不可信 */
  tailHard: number
}

export const DEFAULT_RELIABLE_ZONE: Readonly<ReliableZone> = Object.freeze({
  frontHard: 30, frontGray: 60, tailGray: 700, tailHard: 900,
})

/** 校验，返回错误文字；合法返回空串（与后端校验一致，避免 422） */
export function validateReliableZone(z: ReliableZone): string {
  const vals = [z.frontHard, z.frontGray, z.tailGray, z.tailHard]
  if (vals.some((v) => !Number.isInteger(v) || v < 0)) return '边界须为非负整数'
  if (!(z.frontHard <= z.frontGray && z.frontGray < z.tailGray && z.tailGray <= z.tailHard)) {
    return '须满足 首端硬区 ≤ 首端灰区 < 尾端灰区 ≤ 尾端硬区'
  }
  if (z.tailHard > 5000) return '尾端硬区不应超过 5000bp'
  return ''
}

/** 表单值：与默认相同时发空串（后端走默认），否则 "30,60,700,900" */
export function reliableZoneFormValue(z: ReliableZone): string {
  const d = DEFAULT_RELIABLE_ZONE
  if (z.frontHard === d.frontHard && z.frontGray === d.frontGray
      && z.tailGray === d.tailGray && z.tailHard === d.tailHard) return ''
  return [z.frontHard, z.frontGray, z.tailGray, z.tailHard].join(',')
}
