/** Dashboard data: the shop's state and the trail of what the agent did. */

const API = '/api'

export interface ShelfProduct {
  product: string
  unit: string
  on_shelf: number
  shelf_capacity: number
  low_threshold: number
  is_low: boolean
  in_back_room: number
  refill_pending: boolean
  on_order: boolean
}

export interface ShopOrder {
  order_number: number
  product: string
  supplier: string
  quantity: number
  total: number
  status: string
  hours_until_delivery: number
}

export interface ShopCall {
  call_id: number
  role: 'employee' | 'supervisor'
  product: string | null
  reason: string
  status: 'ringing' | 'answered' | 'ended' | 'declined' | 'missed'
}

/** A voice agent Agora is running for a call right now. */
export interface AgoraSession {
  agent_id: string
  channel: string
  role: 'employee' | 'supervisor'
  voice_agent: string
  started_at: number
  /** "studio" when the call was started from an agent saved in Agent Studio. */
  source?: 'studio' | 'code'
  stt: string
  llm: string
  tts: string
}

export interface ShopState {
  products: ShelfProduct[]
  budget_remaining: number
  /** Newest event id on the server; lower than ours means the demo was reset. */
  last_event_id: number
  agora?: { sessions: AgoraSession[] }
  orders: ShopOrder[]
  calls: ShopCall[]
  refill_tasks: { task_id: number; product: string; bring_quantity: number }[]
  proposals: { id: number; product: string; supplier: string; quantity: number; total: number }[]
}

export interface ShopEvent {
  id: number
  ts: number
  kind: 'shelf' | 'decision' | 'call' | 'tool' | 'stock' | 'system'
  title: string
  detail: Record<string, unknown>
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { cache: 'no-store', ...init })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${response.status}`)
  }
  const result = await response.json()
  return result.data as T
}

export const getShopState = () => request<ShopState>('/state')

export const getShopEvents = (after: number) => request<ShopEvent[]>(`/events?after=${after}`)

/**
 * Step a shelf count up or down. The server applies the step to its own
 * current count, so quick repeated presses cannot overwrite each other. If the
 * product is now low, the agent decides who to call.
 */
export const stepShelfCount = (product: string, delta: number) =>
  request<{ action: string; on_shelf: number }>('/shelf', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product, delta, source: 'dashboard' }),
  })

export const resetDemo = () => request<void>('/admin/reset', { method: 'POST' })

/** What the shelf camera saw in one frame. */
export interface VisionResult {
  boxes: { product: string; confidence: number; box: [number, number, number, number] }[]
  /** Count per product in this frame alone. */
  frame_counts: Record<string, number>
  /** Counts that have held steady long enough to be believed. */
  stable_counts: Record<string, number>
  /** Products the camera has actually seen on this shelf. */
  tracked: string[]
  changed: Record<string, number>
  actions: Record<string, string>
}

/** Send one webcam frame to the detector. */
export async function sendVisionFrame(frame: Blob): Promise<VisionResult> {
  return request<VisionResult>('/vision/frame', {
    method: 'POST',
    headers: { 'Content-Type': 'image/jpeg' },
    body: frame,
  })
}

/** Forget what the camera has seen, e.g. after rearranging the shelf. */
export const resetVision = () => request<void>('/vision/reset', { method: 'POST' })
