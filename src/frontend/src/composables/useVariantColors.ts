/**
 * 峰图变体刻度三色（goal T15；A 5.3 / P2-15）
 *
 * 峰图条带与覆盖简图上的差异刻度按证据三态渲染：
 * - confirmed   红（#D0342C）——确证差异，判定由它决定；
 * - unresolved  琥珀（#E6A700）——证据不足，待人工核对/重测；
 * - rejected    灰绿（#8AA88F）——互检定案的测序误差（弱化，不干扰视线）。
 * 旧记录无 status 字段时按 confidence 兼容回退（low → 琥珀）。
 */
export function variantColor(v: { status?: string; confidence?: string }): string {
  if (v.status === 'rejected') return '#8AA88F'
  if (v.status === 'unresolved' || (!v.status && v.confidence === 'low')) {
    return '#E6A700'
  }
  return '#D0342C'
}
