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
  /** False when the agent is switched off for this product: counted, never acted on. */
  enabled: boolean
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
  /** Which occasion the demand forecast treats as coming up. */
  calendar?: { simulated: string | null; occasions: string[]; coming_up: string | null; days_away: number | null }
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
  kind: 'shelf' | 'decision' | 'call' | 'tool' | 'stock' | 'system' | 'speech'
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

/** Switch the agent on or off for one product. */
export const setProductEnabled = (product: string, enabled: boolean) =>
  request<{ enabled: boolean }>('/products/enabled', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product, enabled }),
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

/** Pretend an occasion is a few days away: a name, "none", or null for today's real date. */
export const setCalendarOccasion = (occasion: string | null) =>
  request<{ simulated: string | null }>('/calendar', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ occasion }),
  })

export interface SupplierQuote {
  supplier: string
  unit_price: number
  total_for_quantity: number
  delivery_hours: number
  available_quantity: number
  can_fill_order: boolean
  within_budget: boolean
  problem: string | null
}

/** Everything known about one product, for the details view. */
export interface ProductDetails {
  product: string
  unit: string
  enabled: boolean
  on_shelf: number
  shelf_capacity: number
  low_threshold: number
  nearly_low_mark: number
  refill_up_to: number
  is_low: boolean
  in_back_room: number
  total_in_store: number
  pack_size: number
  sold_per_day: number
  hours_until_sold_out: number | null
  forecast: {
    upcoming_occasion: string | null
    days_until_occasion: number | null
    expected_uplift_percent: number
    forecast_sold_per_day: number
    recommended_order_quantity: number
    normal_order_quantity: number
    basis: string
  }
  budget_remaining: number
  quotes: SupplierQuote[]
  cheapest_that_can_fill: string | null
  fastest_that_can_fill: string | null
  orders: ShopOrder[]
  refill_task: { bring_quantity: number } | null
  callback: { who: string; in_minutes: number } | null
}

export const getProductDetails = (product: string) =>
  request<ProductDetails>(`/products/details?product=${encodeURIComponent(product)}`)
