/** 环状参考跨原点 read：匹配简图分两段绘制、read 表按折回坐标显示 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MatchMap from '@/components/sequencing/MatchMap.vue'
import ReadTable from '@/components/sequencing/ReadTable.vue'
import type { SequencingAnalysis } from '@/api'

type ReadRow = SequencingAnalysis['reads'][number]

const wrapped: ReadRow = {
  index: 0, filename: 'wrap.ab1', raw_length: 400, trimmed_length: 400,
  mean_q: 40, direction: '+', ref_start: 851, ref_end: 1250,
  wraps_origin: true, ref_segments: [[851, 1000], [1, 250]],
  identity: 0.995, mixed_positions: [],
  alignment_view: {
    ref_start: 999, ref_aligned: 'ACGT', read_aligned: 'ACTT', q_aligned: [40, 40, 40, 40],
  },
}
const linear: ReadRow = {
  index: 1, filename: 'lin.ab1', raw_length: 300, trimmed_length: 300,
  mean_q: 40, direction: '-', ref_start: 300, ref_end: 600,
  identity: 1, mixed_positions: [],
}

function mountMap(reads: ReadRow[]) {
  return mount(MatchMap, {
    props: {
      reads, variants: [], features: [],
      coverageRanges: [[1, 250], [851, 1000]], referenceLength: 1000,
    },
  })
}

describe('跨原点 read 渲染', () => {
  it('匹配简图：跨原点 read 两段箭头，线性 read 一段；差异点按折回坐标', () => {
    const w = mountMap([wrapped, linear])
    const rows = w.findAll('g.map-row')
    expect(rows).toHaveLength(2)
    // 排序按 ref_start：线性 read（300）在前
    expect(rows[0].findAll('path')).toHaveLength(1)
    expect(rows[1].findAll('path')).toHaveLength(2)
    expect(rows[1].find('title').text()).toContain('851-1000、1-250')
    expect(rows[1].find('title').text()).toContain('跨越环状参考原点')
    // 差异列 refPos 1001 → 折回 1：圆点落在轴最左端附近，而不是画到图外
    const cx = Number(rows[1].find('circle').attributes('cx'))
    expect(cx).toBeLessThan(200)
  })

  it('read 表：跨原点 read 显示两段，线性 read 与旧格式一致', () => {
    const w = mount(ReadTable, { props: { reads: [wrapped, linear], errors: [] } })
    const cells = w.findAll('tbody tr').map((tr) => tr.findAll('td')[2].text())
    expect(cells).toEqual(['851 - 1000、1 - 250', '300 - 600'])
  })
})
