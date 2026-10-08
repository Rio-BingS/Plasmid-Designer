/** utils/download 的单元测试：blob 下载收敛后的唯一实现 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { downloadBlob, downloadTextFile } from '@/utils/download'

describe('download utils', () => {
  beforeEach(() => {
    vi.mocked(URL.createObjectURL).mockClear()
    vi.mocked(URL.revokeObjectURL).mockClear()
  })

  it('downloadTextFile 用 text/plain Blob 触发下载并释放 URL', () => {
    downloadTextFile('>seq\nATCG', 'demo.fasta')

    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    const blob = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob
    expect(blob.type).toBe('text/plain')

    const link = document.querySelector('a[download="demo.fasta"]')
    expect(link).toBeNull() // click 后已从 DOM 移除
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-url')
  })

  it('downloadBlob 直接下载 Blob 数据', () => {
    const payload = new Blob(['data'], { type: 'application/octet-stream' })
    downloadBlob(payload, 'file.bin')

    expect(URL.createObjectURL).toHaveBeenCalledWith(payload)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-url')
  })
})
