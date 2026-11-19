import type { AgentMode, CallRole } from '../types/conversation'

const API_BASE_URL = '/api'

export interface GetConfigResponse {
  app_id: string
  token: string
  uid: string
  channel_name: string
  agent_uid: string
}

export async function getConfig(options?: { channel?: string; uid?: string | number }): Promise<GetConfigResponse> {
  const params = new URLSearchParams()
  if (options?.channel !== undefined && options.channel !== '') {
    params.set('channel', options.channel)
  }
  if (options?.uid !== undefined && options.uid !== '') {
    params.set('uid', String(options.uid))
  }

  const query = params.toString()
  const response = await fetch(`${API_BASE_URL}/get_config${query ? `?${query}` : ''}`, {
    method: 'GET',
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  const result = await response.json()
  if (result.code !== 0 || !result.data) {
    throw new Error(result.msg || 'Failed to get configuration')
  }
  return result.data
}

export async function startAgent(
  channelName: string,
  rtcUid: number,
  userUid: number,
  agentMode: AgentMode = 'pipeline',
  call: { role?: CallRole; callId?: number } = {},
): Promise<string> {
  const payload = { channelName, rtcUid, userUid, agentMode, ...call }

  const response = await fetch(`${API_BASE_URL}/startAgent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  const result = await response.json()
  if (result.code !== 0 || !result.data?.agent_id) {
    throw new Error(result.msg || 'Failed to start agent')
  }
  return result.data.agent_id
}

export async function stopAgent(agentId: string): Promise<void> {
  if (!agentId) return

  const response = await fetch(`${API_BASE_URL}/stopAgent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agentId }),
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || `HTTP ${response.status}`)
  }
}

/** A call the agent has placed to one of the phones. */
export interface PendingCall {
  call_id: number
  role: CallRole
  product: string | null
  reason: string
  greeting: string
  status: string
}

/** The call ringing for this role right now, or null. */
export async function getPendingCall(role: CallRole): Promise<PendingCall | null> {
  const response = await fetch(`${API_BASE_URL}/calls/pending?role=${role}`, { cache: 'no-store' })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const result = await response.json()
  return result.data ?? null
}

async function postCallAction(callId: number, action: 'decline' | 'end'): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/calls/${callId}/${action}`, { method: 'POST' })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
}

export const declineCall = (callId: number) => postCallAction(callId, 'decline')
export const endCall = (callId: number) => postCallAction(callId, 'end')
