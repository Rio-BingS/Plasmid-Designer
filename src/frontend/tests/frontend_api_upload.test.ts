import { describe, it, expect, vi, beforeAll } from 'vitest'
import axios from 'axios'

/**
 * FormData 上传的回归测试
 *
 * 历史 bug：api 实例硬编码了 headers: { 'Content-Type': 'application/json' }。
 * axios 1.x 的 transformRequest 只要看到 Content-Type 含 application/json 且 data 是
 * FormData，就会把 FormData 序列化成 JSON（File 全变成 {}）发出去；后端的 multipart
 * 接口拿到空表单，只会返回两条 "Field required"，界面上就是
 * 「Field required；Field required」，看不出到底缺了什么。
 *
 * 这里刻意 <b>不整体 mock axios</b>（其余 api 测试都 mock 掉了，所以看不到这个 bug），
 * 而是用真实 axios 实例 + 假 adapter 抓住「真正发出去的请求体」。
 */

let createConfig: any
let sent: any[] = []
let api: typeof import('@/api')

/** 假 adapter：不发网络请求，只记录 axios 最终交给它的请求配置 */
function fakeAdapter(config: any) {
  sent.push(config)
  return Promise.resolve({ data: {}, status: 200, statusText: 'OK', headers: {}, config })
}

beforeAll(async () => {
  // spy 必须在 import @/api 之前装好 —— 实例是在模块加载时创建的
  const realCreate = axios.create.bind(axios)
  vi.spyOn(axios, 'create').mockImplementation((cfg: any = {}) => {
    createConfig = cfg
    return realCreate({ ...cfg, adapter: fakeAdapter })
  })
  api = await import('@/api')
})

describe('axios 实例配置', () => {
  it('不应硬编码 Content-Type（FormData 会被 axios 转成 JSON）', () => {
    expect(createConfig?.baseURL).toBe('/api')
    expect(createConfig?.headers?.['Content-Type']).toBeUndefined()
  })
})

describe('测序分析上传', () => {
  it('单样品：reference / reads 以 FormData 原样发出，而不是 JSON', async () => {
    sent = []
    const reference = new File(['LOCUS ref'], 'ref.gb', { type: 'text/plain' })
    const read = new File(['basecall'], 's1.ab1')
    await api.analyzeSequencingFiles(reference, [read], 25, false)

    const cfg = sent[sent.length - 1]
    expect(cfg.url).toBe('/sequencing/analyze')
    // 关键断言：不能是 JSON 字符串
    expect(typeof cfg.data).not.toBe('string')
    expect(cfg.data).toBeInstanceOf(FormData)

    const form = cfg.data as FormData
    expect(form.get('reference')).toBeInstanceOf(File)
    expect((form.get('reference') as File).name).toBe('ref.gb')
    expect(form.getAll('reads').length).toBe(1)
    expect(form.get('min_q')).toBe('25')
    expect(form.get('allow_decompose')).toBe('false')
  })

  it('批量：多个测序文件 + Excel 信息表都是 FormData 字段', async () => {
    sent = []
    const ab1 = new File(['a'], 'p1-1.ab1')
    const ab2 = new File(['b'], 'p2-1.ab1')
    const excel = new File(['x'], 'info.xlsx')
    await api.analyzeSequencingBatch([ab1, ab2], excel, 20)

    const cfg = sent[sent.length - 1]
    expect(cfg.url).toBe('/sequencing/analyze-batch')
    expect(cfg.data).toBeInstanceOf(FormData)
    const form = cfg.data as FormData
    expect(form.getAll('files').length).toBe(2)
    expect((form.get('excel') as File).name).toBe('info.xlsx')
  })
})

describe('formatApiError', () => {
  it('422 校验错误带上字段名，不再是两遍一样的 Field required', () => {
    const e = {
      response: {
        data: {
          detail: [
            { type: 'missing', loc: ['body', 'reference'], msg: 'Field required' },
            { type: 'missing', loc: ['body', 'reads'], msg: 'Field required' }
          ]
        }
      }
    }
    expect(api.formatApiError(e, '分析失败')).toBe('reference: Field required；reads: Field required')
  })

  it('detail 是字符串时原样透出，缺 detail 时用兜底文案', () => {
    expect(api.formatApiError({ response: { data: { detail: '参考序列过短' } } })).toBe('参考序列过短')
    // 浏览器发送阶段失败（文件被占用/连接中断）时 axios 只给 "Network Error"，
    // 翻译成可行动的提示而不是原样透出
    expect(api.formatApiError({ message: 'Network Error' }, '分析失败')).toContain('网络异常')
    expect(api.formatApiError({ message: '超时了' }, '分析失败')).toBe('超时了')
    expect(api.formatApiError({}, '分析失败')).toBe('分析失败')
  })
})
