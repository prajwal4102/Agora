"use client";

import type { RTMClient } from "agora-rtm";
import dynamic from "next/dynamic";
import { useRef } from "react";

import { getConfig, startAgent } from "@/services/api";
import type { AgoraTokenData, CallRole } from "@/types/conversation";

/** Wait briefly for RTM to report CONNECTED; resolve anyway after the timeout. */
function waitForRtmConnected(rtmClient: RTMClient, timeoutMs = 600): Promise<void> {
	return new Promise((resolve) => {
		let settled = false;
		const finish = () => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			rtmClient.removeEventListener("status", onStatus);
			resolve();
		};
		const onStatus = (status: { newState?: string } | { state?: string }) => {
			const state = "newState" in status ? status.newState : "state" in status ? status.state : undefined;
			if (state === "CONNECTED") finish();
		};
		rtmClient.addEventListener("status", onStatus);
		const timer = setTimeout(finish, timeoutMs);
	});
}

export type CallSession = {
	agoraData: AgoraTokenData;
	rtmClient: RTMClient;
	agentStarted: boolean;
};

/**
 * Open a voice call with the agent: fetch a channel + token, start the agent
 * for this role (or for the call being answered), and log in to RTM for
 * transcripts. The caller then renders ConversationComponent to join audio.
 */
export async function openCallSession(options: {
	role: CallRole;
	callId?: number;
}): Promise<CallSession> {
	const config = await getConfig();
	let agentStarted = true;

	const [agentId, rtmClient] = await Promise.all([
		startAgent(config.channel_name, Number(config.agent_uid), Number(config.uid), "pipeline", {
			role: options.role,
			callId: options.callId,
		}).catch((err) => {
			console.error("Failed to start the agent:", err);
			agentStarted = false;
			return undefined;
		}),
		(async () => {
			const { default: AgoraRTM } = await import("agora-rtm");
			const rtm: RTMClient = new AgoraRTM.RTM(config.app_id, config.uid);
			await rtm.login({ token: config.token });
			await waitForRtmConnected(rtm);
			await rtm.subscribe(config.channel_name);
			return rtm;
		})(),
	]);

	return {
		agentStarted,
		rtmClient,
		agoraData: {
			token: config.token,
			uid: config.uid,
			channel: config.channel_name,
			appId: config.app_id,
			agentUid: config.agent_uid,
			agentId,
		},
	};
}

/** Provides one Agora RTC client to the in-call screen. Client-only. */
export const AgoraProvider = dynamic(
	async () => {
		const { AgoraRTCProvider, default: AgoraRTC } = await import("agora-rtc-react");
		return {
			default: function AgoraProviders({ children }: { children: React.ReactNode }) {
				const clientRef = useRef<ReturnType<typeof AgoraRTC.createClient> | null>(null);
				if (!clientRef.current) {
					clientRef.current = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
				}
				return <AgoraRTCProvider client={clientRef.current}>{children}</AgoraRTCProvider>;
			},
		};
	},
	{ ssr: false },
);
