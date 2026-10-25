import type { ToolEvent } from './types'

let token = ''
export function setToken(value: string) { token = value }

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/${path}`, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CoreCoder-Token': token },
    body: JSON.stringify(body),
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '连接失败，请重试。')
  return result as T
}

export async function chat(id: string, prompt: string, onEvent: (event: ToolEvent) => void) {
  const response = await fetch('/api/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CoreCoder-Token': token },
    body: JSON.stringify({ id, prompt }),
  })
  if (!response.ok) {
    const result = await response.json()
    throw new Error(result.error || '任务启动失败。')
  }
  if (!response.body) throw new Error('当前浏览器不支持流式响应。')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let doneEvent = false
  for (;;) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    let boundary: number
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      if (block.startsWith('data: ')) {
        const event = JSON.parse(block.slice(6)) as ToolEvent
        if (event.type === 'done') doneEvent = true
        onEvent(event)
      }
    }
    if (done) break
  }
  if (!doneEvent) throw new Error('连接意外断开，请刷新对话查看最后状态。')
}
