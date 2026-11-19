"use client";

import { Phone, PhoneCall, PhoneOff } from "lucide-react";
import dynamic from "next/dynamic";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { AgoraProvider, type CallSession, openCallSession } from "@/lib/callSession";
import { Ringtone } from "@/lib/ringtone";
import {
	type PendingCall,
	declineCall,
	endCall,
	getConfig,
	getPendingCall,
	stopAgent,
} from "@/services/api";
import type { AgoraRenewalTokens, CallRole } from "@/types/conversation";

const ConversationComponent = dynamic(() => import("@/components/ConversationComponent"), {
	ssr: false,
});

const ROLES: Record<CallRole, { title: string; blurb: string }> = {
	employee: { title: "Floor employee", blurb: "Gets calls to refill shelves from the back room." },
	supervisor: { title: "Store supervisor", blurb: "Gets calls to approve reorders." },
};

const POLL_MS = 1500;

type Stage = "idle" | "ringing" | "connecting" | "in-call";

function readRoleFromUrl(): CallRole | null {
	if (typeof window === "undefined") return null;
	const role = new URLSearchParams(window.location.search).get("role");
	return role === "employee" || role === "supervisor" ? role : null;
}

export default function PhoneApp() {
	const [role, setRole] = useState<CallRole | null>(null);
	const [stage, setStage] = useState<Stage>("idle");
	const [incoming, setIncoming] = useState<PendingCall | null>(null);
	const [session, setSession] = useState<CallSession | null>(null);
	const [activeCallId, setActiveCallId] = useState<number | null>(null);
	const [error, setError] = useState<string | null>(null);
	const ringtone = useRef<Ringtone | null>(null);
	if (!ringtone.current) ringtone.current = new Ringtone();

	// A link like /phone?role=employee skips the picker, but sound still needs a tap.
	const [urlRole, setUrlRole] = useState<CallRole | null>(null);
	useEffect(() => setUrlRole(readRoleFromUrl()), []);

	const chooseRole = (next: CallRole) => {
		ringtone.current?.unlock();
		setRole(next);
		window.history.replaceState(null, "", `?role=${next}`);
	};

	// While idle, ask the backend whether the agent is calling this phone.
	useEffect(() => {
		if (!role || (stage !== "idle" && stage !== "ringing")) return;
		let cancelled = false;
		const poll = async () => {
			try {
				const call = await getPendingCall(role);
				if (cancelled) return;
				setIncoming(call);
				setStage(call ? "ringing" : "idle");
			} catch {
				// Backend briefly unreachable: keep the current screen and try again.
			}
		};
		void poll();
		const timer = setInterval(poll, POLL_MS);
		return () => {
			cancelled = true;
			clearInterval(timer);
		};
	}, [role, stage]);

	useEffect(() => {
		if (stage === "ringing") ringtone.current?.start();
		else ringtone.current?.stop();
	}, [stage]);
	useEffect(() => () => ringtone.current?.stop(), []);

	const connect = async (callId?: number) => {
		if (!role) return;
		setError(null);
		setStage("connecting");
		try {
			const next = await openCallSession({ role, callId });
			if (!next.agentStarted) throw new Error("The agent could not join the call.");
			setSession(next);
			setActiveCallId(callId ?? null);
			setStage("in-call");
		} catch (err) {
			console.error("Call failed:", err);
			setError(err instanceof Error ? err.message : "The call could not connect.");
			setStage("idle");
		}
	};

	const decline = async () => {
		if (incoming) await declineCall(incoming.call_id).catch(() => {});
		setIncoming(null);
		setStage("idle");
	};

	const hangUp = useCallback(async () => {
		if (session?.agoraData.agentId) {
			await stopAgent(session.agoraData.agentId).catch((err) =>
				console.error("Failed to stop agent:", err),
			);
		}
		if (activeCallId !== null) await endCall(activeCallId).catch(() => {});
		session?.rtmClient.logout().catch(() => {});
		setSession(null);
		setActiveCallId(null);
		setIncoming(null);
		setStage("idle");
	}, [session, activeCallId]);

	const renewTokens = useCallback(
		async (uid: string): Promise<AgoraRenewalTokens> => {
			const channel = session?.agoraData.channel;
			if (!channel || !session) throw new Error("No active call to renew");
			const [rtc, rtm] = await Promise.all([
				getConfig({ channel, uid }),
				getConfig({ channel, uid: session.agoraData.uid }),
			]);
			return { rtcToken: rtc.token, rtmToken: rtm.token };
		},
		[session],
	);

	if (!role) {
		return (
			<Screen>
				<h1 className="text-2xl font-semibold tracking-tight">Who is using this phone?</h1>
				<p className="mt-2 text-sm text-muted-foreground">
					Asha, the stock agent, will ring this phone when it needs you.
				</p>
				<div className="mt-8 flex w-full flex-col gap-3">
					{(Object.keys(ROLES) as CallRole[]).map((key) => (
						<button
							key={key}
							type="button"
							onClick={() => chooseRole(key)}
							className={`rounded-2xl border px-5 py-4 text-left transition-colors hover:border-primary ${
								urlRole === key ? "border-primary" : "border-border"
							}`}
						>
							<span className="block text-base font-medium">{ROLES[key].title}</span>
							<span className="mt-1 block text-sm text-muted-foreground">{ROLES[key].blurb}</span>
						</button>
					))}
				</div>
			</Screen>
		);
	}

	if (stage === "in-call" && session) {
		return (
			<div className="flex h-dvh min-h-screen flex-col bg-background text-foreground">
				<Suspense fallback={<LoadingSkeleton />}>
					<ErrorBoundary>
						<AgoraProvider>
							<ConversationComponent
								agoraData={session.agoraData}
								agentMode="pipeline"
								rtmClient={session.rtmClient}
								onTokenWillExpire={renewTokens}
								onEndConversation={hangUp}
							/>
						</AgoraProvider>
					</ErrorBoundary>
				</Suspense>
			</div>
		);
	}

	if (stage === "ringing" && incoming) {
		return (
			<Screen>
				<p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
					Incoming call
				</p>
				<div className="mt-6 flex h-28 w-28 animate-pulse items-center justify-center rounded-full bg-primary/15">
					<PhoneCall className="h-12 w-12 text-primary" aria-hidden />
				</div>
				<h1 className="mt-6 text-3xl font-semibold tracking-tight">Asha</h1>
				<p className="mt-1 text-sm text-muted-foreground">FreshMart stock control</p>
				<p className="mt-4 max-w-xs text-sm text-foreground/80">About: {incoming.reason}</p>
				<div className="mt-10 flex w-full justify-center gap-10">
					<RoundButton label="Decline" tone="decline" onClick={decline}>
						<PhoneOff className="h-7 w-7" aria-hidden />
					</RoundButton>
					<RoundButton label="Answer" tone="answer" onClick={() => connect(incoming.call_id)}>
						<Phone className="h-7 w-7" aria-hidden />
					</RoundButton>
				</div>
			</Screen>
		);
	}

	return (
		<Screen>
			<p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
				{ROLES[role].title}
			</p>
			<h1 className="mt-3 text-2xl font-semibold tracking-tight">
				{stage === "connecting" ? "Connecting…" : "Waiting for calls"}
			</h1>
			<p className="mt-2 max-w-xs text-sm text-muted-foreground">
				Keep this page open. It rings when Asha needs you.
			</p>
			{error ? (
				<p className="mt-4 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>
			) : null}
			<button
				type="button"
				disabled={stage === "connecting"}
				onClick={() => connect()}
				className="mt-8 inline-flex items-center gap-2 rounded-full bg-primary px-6 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
			>
				<Phone className="h-4 w-4" aria-hidden />
				Call Asha
			</button>
			<button
				type="button"
				onClick={() => setRole(null)}
				className="mt-6 text-xs text-muted-foreground underline underline-offset-4"
			>
				Switch role
			</button>
		</Screen>
	);
}

function Screen({ children }: { children: React.ReactNode }) {
	return (
		<main className="flex h-dvh min-h-screen flex-col items-center justify-center bg-background px-6 text-center text-foreground">
			<div className="flex w-full max-w-sm flex-col items-center">{children}</div>
		</main>
	);
}

function RoundButton({
	label,
	tone,
	onClick,
	children,
}: {
	label: string;
	tone: "answer" | "decline";
	onClick: () => void;
	children: React.ReactNode;
}) {
	const colour = tone === "answer" ? "bg-emerald-600 hover:bg-emerald-500" : "bg-red-600 hover:bg-red-500";
	return (
		<button type="button" onClick={onClick} className="flex flex-col items-center gap-2">
			<span className={`flex h-16 w-16 items-center justify-center rounded-full text-white ${colour}`}>
				{children}
			</span>
			<span className="text-xs text-muted-foreground">{label}</span>
		</button>
	);
}
