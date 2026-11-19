import type { RTMClient } from 'agora-rtm';

export type AgentMode = 'pipeline' | 'realtime';

/** Who is on the phone: decides the agent's language and what it may do. */
export type CallRole = 'employee' | 'supervisor';

/** Session bootstrap from GET /api/get_config (channel + tokens + agent identity). */
export interface AgoraTokenData {
  token: string;
  uid: string;
  channel: string;
  agentId?: string;
  appId?: string; // `app_id` returned by backend
  agentUid?: string; // `NEXT_PUBLIC_AGENT_UID`
}

export interface AgoraRenewalTokens {
  rtcToken: string;
  rtmToken: string;
}

export interface ConversationComponentProps {
  agoraData: AgoraTokenData;
  agentMode: AgentMode;
  rtmClient: RTMClient;
  onTokenWillExpire: (uid: string) => Promise<AgoraRenewalTokens>;
  onEndConversation: () => void;
}
