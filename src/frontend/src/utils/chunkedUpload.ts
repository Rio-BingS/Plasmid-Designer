/**
 * 批量测序分块上传：切分、逐块上传（失败重试）、提交分析、轮询结果
 *
 * 站点在 Cloudflare 免费版之后，单个请求体上限 100 MB；整批一次上传会被
 * 直接掐断。这里把文件按体积（≤90 MB）与个数切成多个分块依次上传到同一个
 * 服务器会话，最后 finalize 一次分析，结果与单请求入口完全一致。
 *
 * 传输层（HTTP 调用）由调用方注入，便于单测；api/index.ts 提供 axios 实现。
 */

export interface ChunkLimits {
  maxFiles: number
  maxBytes: number
}

export const DEFAULT_CHUNK_LIMITS: ChunkLimits = { maxFiles: 200, maxBytes: 90 * 1024 * 1024 }

/** 按原顺序贪心切分：加入下一个文件会超出体积或个数上限时另起一块。
 *  单个文件本身超过体积上限时独占一块（由服务器给出单文件过大的提示）。 */
export function splitIntoChunks<T extends { size: number }>(files: T[], limits: ChunkLimits = DEFAULT_CHUNK_LIMITS): T[][] {
  const maxFiles = Math.max(1, Math.floor(limits.maxFiles))
  const maxBytes = Math.max(1, limits.maxBytes)
  const chunks: T[][] = []
  let cur: T[] = []
  let curBytes = 0
  for (const f of files) {
    if (cur.length && (cur.length >= maxFiles || curBytes + f.size > maxBytes)) {
      chunks.push(cur)
      cur = []
      curBytes = 0
    }
    cur.push(f)
    curBytes += f.size
  }
  if (cur.length) chunks.push(cur)
  return chunks
}

export interface SessionCreated {
  session_id: string
  access_token?: string
  limits?: { chunk_max_files?: number; chunk_max_mb?: number }
}

export interface SessionStatus<R> {
  state: 'open' | 'queued' | 'running' | 'done' | 'failed'
  result?: R
  error?: { status_code: number; detail: string }
}

export interface FinalizeResponse<R> {
  status: number
  data: R | SessionStatus<R>
}

export interface ChunkedTransport<R> {
  create(manifest: { name: string; size: number }[], excel: File | null): Promise<SessionCreated>
  upload(sessionId: string, files: File[], index: number, token?: string, signal?: AbortSignal): Promise<unknown>
  finalize(sessionId: string, minQ: number, token?: string): Promise<FinalizeResponse<R>>
  status(sessionId: string, token?: string): Promise<SessionStatus<R>>
  abort(sessionId: string, token?: string): Promise<unknown>
}

export interface ChunkedProgress {
  phase: 'uploading' | 'analyzing'
  /** 已上传完成的分块数 */
  done: number
  total: number
}

export interface ChunkedOptions {
  onProgress?: (p: ChunkedProgress) => void
  signal?: AbortSignal
  /** 单个分块失败后的重试次数（不含首次） */
  retries?: number
  retryDelayMs?: number
  pollIntervalMs?: number
  /** 轮询分析结果的最长时间 */
  pollTimeoutMs?: number
}

/** 用户取消上传 */
export class UploadCancelledError extends Error {
  constructor() {
    super('已取消上传')
    this.name = 'UploadCancelledError'
  }
}

/** 构造与 axios 错误同形的对象，界面统一经 formatApiError 显示后端 detail */
function apiError(status: number, detail: string) {
  const e: any = new Error(detail)
  e.response = { status, data: { detail } }
  return e
}

/** 可重试：网络中断（无响应）、超时、429、5xx；其余 4xx 是确定性的拒绝 */
export function isRetryable(e: any): boolean {
  if (e instanceof UploadCancelledError || e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED') return false
  const status = e?.response?.status
  if (!status) return true
  return status === 408 || status === 429 || status >= 500
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new UploadCancelledError())
    const t = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    function onAbort() {
      clearTimeout(t)
      reject(new UploadCancelledError())
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function checkAborted(signal?: AbortSignal) {
  if (signal?.aborted) throw new UploadCancelledError()
}

export async function runChunkedBatch<R>(
  files: File[],
  excel: File | null,
  minQ: number,
  transport: ChunkedTransport<R>,
  opts: ChunkedOptions = {}
): Promise<R> {
  const { onProgress, signal } = opts
  const retries = opts.retries ?? 2
  const retryDelayMs = opts.retryDelayMs ?? 1500
  const pollIntervalMs = opts.pollIntervalMs ?? 3000
  const pollTimeoutMs = opts.pollTimeoutMs ?? 2 * 60 * 60 * 1000

  checkAborted(signal)
  // 带文件清单建会话：服务器按文件名预检 read 上限，超限在上传前就拒绝
  const session = await transport.create(files.map((f) => ({ name: f.name, size: f.size })), excel)
  const sid = session.session_id
  const token = session.access_token
  const limits: ChunkLimits = {
    maxFiles: session.limits?.chunk_max_files || DEFAULT_CHUNK_LIMITS.maxFiles,
    maxBytes: session.limits?.chunk_max_mb ? session.limits.chunk_max_mb * 1024 * 1024 : DEFAULT_CHUNK_LIMITS.maxBytes
  }
  const chunks = splitIntoChunks(files, limits)
  let submitted = false

  try {
    onProgress?.({ phase: 'uploading', done: 0, total: chunks.length })
    for (let i = 0; i < chunks.length; i++) {
      for (let attempt = 0; ; attempt++) {
        checkAborted(signal)
        try {
          // chunk_index 不变：上次其实已成功、只是响应丢了时，服务器按幂等处理
          await transport.upload(sid, chunks[i], i, token, signal)
          break
        } catch (e) {
          if (signal?.aborted) throw new UploadCancelledError()
          if (attempt >= retries || !isRetryable(e)) throw e
          await sleep(retryDelayMs * (attempt + 1), signal)
        }
      }
      onProgress?.({ phase: 'uploading', done: i + 1, total: chunks.length })
    }

    checkAborted(signal)
    onProgress?.({ phase: 'analyzing', done: chunks.length, total: chunks.length })
    submitted = true
    let resp: FinalizeResponse<R> | null = null
    try {
      resp = await transport.finalize(sid, minQ, token)
    } catch (e: any) {
      // 确定性拒绝（413 read 超限等）直接抛出；网络中断时提交可能已到达
      // 服务器，转为轮询会话状态，而不是盲目重复提交
      if (e?.response?.status && !isRetryable(e)) throw e
      const st = await transport.status(sid, token).catch(() => null)
      if (st && st.state === 'open') {
        resp = await transport.finalize(sid, minQ, token)
      }
    }
    if (resp && resp.status === 200) return resp.data as R

    // 202 或提交结果未知：轮询（分析可能超过 Cloudflare 100 秒代理超时）
    const deadline = Date.now() + pollTimeoutMs
    for (;;) {
      const st = await transport.status(sid, token)
      if (st.state === 'done' && st.result) return st.result
      if (st.state === 'failed') {
        throw apiError(st.error?.status_code ?? 500, st.error?.detail || '批量分析失败')
      }
      if (Date.now() > deadline) throw apiError(504, '批量分析等待超时，请稍后在测序分析历史中查看或重新提交')
      await sleep(pollIntervalMs)
    }
  } catch (e) {
    // 未提交分析前出错或取消：删除服务器上的暂存文件（尽力而为）
    if (!submitted) await transport.abort(sid, token).catch(() => undefined)
    throw e
  }
}
