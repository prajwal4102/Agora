"use client";

import {
	Check,
	Eye,
	GitBranch,
	Headset,
	type LucideIcon,
	MessagesSquare,
	ShieldCheck,
	ShoppingCart,
	User,
	UserCog,
	Wrench,
	X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { ShopEvent } from "@/services/shop";

/**
 * Agent Playground: the system at work, as it happens.
 *
 *  - Trace: each hand-off between parts of the system is a card; tool calls fan
 *    out as circles under the agent that made them; older steps shrink to
 *    breadcrumbs.
 *  - Architecture: every part and how they connect, with the live hand-off lit
 *    up. Click a part to see what it does.
 *
 * Only the two voice agents are language models. The others are vision models
 * and plain rules, and each is labelled with what it really is.
 */

type AgentKey =
	| "employee"
	| "supervisor"
	| "floor_agent"
	| "procurement_agent"
	| "dispatcher"
	| "shelf_watcher"
	| "order_gate"
	| "shop_tools";

type AgentMeta = {
	label: string;
	sub: string;
	kind: "Human" | "LLM agent" | "Rules" | "Vision" | "Tools";
	Icon: LucideIcon;
	colour: string;
	gradient: [string, string];
	role: string;
	points: string[];
	tools?: string[];
};

const AGENTS: Record<AgentKey, AgentMeta> = {
	employee: {
		label: "Floor employee",
		sub: "Phone 1 · refills shelves",
		kind: "Human",
		Icon: User,
		colour: "#fbbf24",
		gradient: ["#fbbf24", "#d97706"],
		role: "The person on the shop floor",
		points: [
			"Gets a call in Hinglish when a shelf needs refilling",
			"Says when it is done, or that the stock is not in the back room",
			"Cannot approve purchases",
		],
	},
	supervisor: {
		label: "Store supervisor",
		sub: "Phone 2 · approves purchases",
		kind: "Human",
		Icon: UserCog,
		colour: "#38bdf8",
		gradient: ["#38bdf8", "#2563eb"],
		role: "The only person who can approve an order",
		points: [
			"Gets a call with a recommendation, not a raw alert",
			"Can change the quantity, the supplier or set a spending limit",
			"A spoken yes is what releases the order",
		],
	},
	floor_agent: {
		label: "Floor Agent",
		sub: "Voice · Hinglish",
		kind: "LLM agent",
		Icon: Headset,
		colour: "#fbbf24",
		gradient: ["#fbbf24", "#d97706"],
		role: "Voice agent that talks to the employee",
		points: [
			"Runs on Agora Conversational AI and speaks through Murf Falcon in Hindi-English",
			"Says what to bring from the back room and how many",
			"If the stock is missing, corrects the count and hands the case to the Dispatcher",
		],
		tools: ["get_refill_tasks", "complete_refill_task", "report_backroom_shortage", "get_shelf_status"],
	},
	procurement_agent: {
		label: "Procurement Agent",
		sub: "Voice · English",
		kind: "LLM agent",
		Icon: ShoppingCart,
		colour: "#38bdf8",
		gradient: ["#38bdf8", "#2563eb"],
		role: "Voice agent that talks to the supervisor",
		points: [
			"Runs on Agora Conversational AI and speaks through Murf Falcon",
			"Checks sales, compares three suppliers and recommends one order",
			"Re-prices when the supervisor pushes back, and reads the total aloud",
			"Cannot place an order itself: it must go through the Order Gate",
		],
		tools: [
			"get_shelf_status",
			"get_sales_rate",
			"get_supplier_quotes",
			"get_budget",
			"propose_order",
			"confirm_order",
			"get_order_status",
			"schedule_callback",
		],
	},
	dispatcher: {
		label: "Dispatcher",
		sub: "Decides who to call",
		kind: "Rules",
		Icon: GitBranch,
		colour: "#818cf8",
		gradient: ["#818cf8", "#4f46e5"],
		role: "Triage: who needs to act, and why",
		points: [
			"Low shelf and stock in the back room: ring the employee",
			"Low shelf and an empty back room: ring the supervisor",
			"Stays quiet if a call is live, a refill is pending or the product is on order",
			"Plain rules, not a language model, so it behaves the same every time",
		],
	},
	shelf_watcher: {
		label: "Shelf Watcher",
		sub: "Camera · counts products",
		kind: "Vision",
		Icon: Eye,
		colour: "#a78bfa",
		gradient: ["#a78bfa", "#7c3aed"],
		role: "Watches the shelf and counts what is on it",
		points: [
			"A detector finds objects by shape from the catalogue's product names",
			"A second model reads wrapped products the detector cannot name",
			"Believes a count only after it holds steady for three frames",
			"Confirms refills and deliveries by seeing the stock return",
			"Runs on the laptop, with no cloud service",
		],
	},
	order_gate: {
		label: "Order Gate",
		sub: "No order without a yes",
		kind: "Rules",
		Icon: ShieldCheck,
		colour: "#34d399",
		gradient: ["#34d399", "#059669"],
		role: "The rule that guards every purchase",
		points: [
			"An order is first priced and held as a proposal",
			"It is placed only against a live proposal, with the supervisor's words of approval",
			"A changed quantity replaces the old proposal, so a stale one cannot be confirmed",
			"Checks supplier stock and budget again at the moment of placing",
		],
	},
	shop_tools: {
		label: "Shop Tools · MCP",
		sub: "Stock · sales · suppliers · budget",
		kind: "Tools",
		Icon: Wrench,
		colour: "#94a3b8",
		gradient: ["#94a3b8", "#475569"],
		role: "What the agents can look up and do",
		points: [
			"Served over MCP, which Agora's cloud calls during the conversation",
			"Shelf and back-room stock, sales rate, the purchase budget",
			"Price lists from three suppliers with different prices and delivery times",
		],
	},
};

const isAgent = (key: unknown): key is AgentKey => typeof key === "string" && key in AGENTS;

/** Where each event came from and went, falling back for events logged before tagging. */
function route(event: ShopEvent): { agent: AgentKey; to: AgentKey | null } {
	const { agent, to } = event.detail as { agent?: unknown; to?: unknown };
	if (isAgent(agent)) return { agent, to: isAgent(to) ? to : null };
	if (event.kind === "shelf") return { agent: "shelf_watcher", to: "dispatcher" };
	if (event.kind === "tool") return { agent: "procurement_agent", to: "shop_tools" };
	return { agent: "dispatcher", to: null };
}

type ToolDot = { id: number; name: string; failed: boolean; summary: string | null };
type Step = {
	id: number;
	agent: AgentKey;
	to: AgentKey | null;
	title: string;
	tools: ToolDot[];
	repeats: number;
};

/** Fold the event stream into steps: tool calls gather under their agent, and
 *  a run of shelf counts becomes one step showing the latest. */
function buildSteps(events: ShopEvent[], summarise: (detail: Record<string, unknown>) => string | null): Step[] {
	const steps: Step[] = [];
	for (const event of events) {
		if (event.kind === "system") continue;
		const { agent, to } = route(event);
		const last = steps[steps.length - 1];
		if (event.kind === "tool") {
			const result = (event.detail.result ?? {}) as { ok?: boolean };
			const dot: ToolDot = {
				id: event.id,
				name: String(event.detail.tool ?? "tool"),
				failed: result.ok === false,
				summary: summarise(event.detail),
			};
			if (last && last.tools.length && last.agent === agent) last.tools.push(dot);
			else steps.push({ id: event.id, agent, to, title: "Working with tools", tools: [dot], repeats: 1 });
			continue;
		}
		if (event.kind === "shelf" && last && last.agent === agent && !last.tools.length && last.title.startsWith("Shelf count")) {
			last.title = event.title;
			last.id = event.id;
			last.repeats += 1;
			continue;
		}
		steps.push({ id: event.id, agent, to, title: event.title, tools: [], repeats: 1 });
	}
	return steps;
}

const KEEP_FULL = 4;

export function AgentPlayground({
	events,
	summarise,
}: {
	events: ShopEvent[];
	summarise: (detail: Record<string, unknown>) => string | null;
}) {
	const [view, setView] = useState<"trace" | "map">("trace");
	const steps = useMemo(() => buildSteps(events, summarise), [events, summarise]);
	const latest = steps[steps.length - 1];

	return (
		<div className="flex min-h-0 flex-1 flex-col rounded-xl border border-border bg-card">
			<style>{`
				/* Movement only: if the browser pauses the animation, the card is still fully readable. */
				@keyframes playground-in { from { transform: translateY(10px) scale(0.98); } to { transform: none; } }
				@keyframes playground-flow { to { stroke-dashoffset: -24; } }
				.playground-in { animation: playground-in 320ms ease-out; }
				.playground-flow { stroke-dasharray: 6 6; animation: playground-flow 900ms linear infinite; }
			`}</style>
			<div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2">
				<div className="flex rounded-lg bg-muted p-0.5 text-xs">
					{(["trace", "map"] as const).map((key) => (
						<button
							key={key}
							type="button"
							onClick={() => setView(key)}
							className={`rounded-md px-3 py-1 font-medium transition-colors ${
								view === key ? "bg-background text-foreground shadow" : "text-muted-foreground"
							}`}
						>
							{key === "trace" ? "Live trace" : "Architecture"}
						</button>
					))}
				</div>
				{latest ? (
					<span className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
						<span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full" style={{ background: AGENTS[latest.agent].colour }} />
						<span className="truncate">{AGENTS[latest.agent].label}</span>
					</span>
				) : null}
			</div>
			{view === "trace" ? <Trace steps={steps} /> : <ArchitectureMap latest={latest} steps={steps} />}
		</div>
	);
}

function Trace({ steps }: { steps: Step[] }) {
	const endRef = useRef<HTMLDivElement | null>(null);
	const marker = steps.length ? `${steps[steps.length - 1].id}-${steps[steps.length - 1].tools.length}` : "";
	// biome-ignore lint/correctness/useExhaustiveDependencies: scroll whenever the newest step changes
	useEffect(() => {
		endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
	}, [marker]);

	if (!steps.length) {
		return (
			<div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
				<MessagesSquare className="h-6 w-6" aria-hidden />
				Nothing yet. Take a product off the shelf and watch the agents hand the work to each other.
			</div>
		);
	}
	const older = steps.slice(0, -KEEP_FULL).slice(-10);
	const recent = steps.slice(-KEEP_FULL);
	return (
		<div className="min-h-0 flex-1 overflow-y-auto p-3">
			{older.length ? (
				<ol className="mb-3 flex flex-wrap gap-1.5">
					{older.map((step) => (
						<li
							key={step.id}
							title={step.title}
							className="flex max-w-full items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
						>
							<span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: AGENTS[step.agent].colour }} />
							<span className="truncate">{step.tools.length ? `${step.tools.length} tool calls` : step.title}</span>
						</li>
					))}
				</ol>
			) : null}
			<ol className="flex flex-col">
				{recent.map((step, index) => (
					<li key={`${step.id}-${step.tools.length ? "t" : "s"}`} className="playground-in flex flex-col items-center">
						{index > 0 ? <span className="h-5 w-px bg-border" aria-hidden /> : null}
						<StepCard step={step} newest={index === recent.length - 1} />
					</li>
				))}
			</ol>
			<div ref={endRef} />
		</div>
	);
}

function StepCard({ step, newest }: { step: Step; newest: boolean }) {
	const agent = AGENTS[step.agent];
	const target = step.to ? AGENTS[step.to] : null;
	const lastTool = step.tools[step.tools.length - 1];
	return (
		<div
			className="w-full rounded-xl border bg-background/60 p-3"
			style={{ borderColor: newest ? agent.colour : "hsl(var(--border))", boxShadow: newest ? `0 0 0 1px ${agent.colour}33, 0 8px 24px -12px ${agent.colour}` : undefined }}
		>
			<div className="flex items-center gap-3">
				<span
					className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-white"
					style={{ background: `linear-gradient(135deg, ${agent.gradient[0]}, ${agent.gradient[1]})` }}
				>
					<agent.Icon className="h-4.5 w-4.5" aria-hidden />
				</span>
				<div className="min-w-0 flex-1">
					<p className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide" style={{ color: agent.colour }}>
						{agent.label}
						<span className="rounded border border-current px-1 text-[9px] opacity-80">{agent.kind}</span>
					</p>
					<p className="text-sm font-medium leading-snug">{step.title}</p>
					{target ? (
						<p className="text-xs text-muted-foreground">
							→ {target.label}
							{step.repeats > 1 ? ` · ${step.repeats} updates` : ""}
						</p>
					) : null}
				</div>
			</div>
			{step.tools.length ? (
				<div className="mt-3 border-t border-border pt-3">
					<ul className="flex flex-wrap justify-center gap-x-3 gap-y-2">
						{step.tools.map((tool) => (
							<li key={tool.id} className="playground-in flex w-[4.75rem] flex-col items-center gap-1" title={tool.summary ?? tool.name}>
								<span
									className="flex h-9 w-9 items-center justify-center rounded-full border-2"
									style={{
										borderColor: tool.failed ? "#f87171" : agent.colour,
										color: tool.failed ? "#f87171" : agent.colour,
										background: `${tool.failed ? "#f87171" : agent.colour}1a`,
									}}
								>
									{tool.failed ? <X className="h-4 w-4" aria-hidden /> : <Check className="h-4 w-4" aria-hidden />}
								</span>
								<span className="w-full truncate text-center text-[10px] text-muted-foreground">
									{tool.name.replace(/^get_/, "").replace(/_/g, " ")}
								</span>
							</li>
						))}
					</ul>
					{lastTool?.summary ? (
						<p className={`mt-2 text-center text-xs ${lastTool.failed ? "text-red-400" : "text-muted-foreground"}`}>
							{lastTool.summary}
						</p>
					) : null}
				</div>
			) : null}
		</div>
	);
}

/* Layout of the architecture map, on a 760 × 500 canvas: people, their voice
   agents, the dispatcher between them, then perception and the purchase rule,
   with the shared tools at the bottom. */
const MAP_W = 760;
const MAP_H = 500;
const POSITION: Record<AgentKey, [number, number]> = {
	employee: [170, 50],
	supervisor: [590, 50],
	floor_agent: [170, 160],
	procurement_agent: [590, 160],
	dispatcher: [380, 255],
	shelf_watcher: [130, 355],
	order_gate: [630, 355],
	shop_tools: [380, 445],
};
const WIRES: [AgentKey, AgentKey][] = [
	["employee", "floor_agent"],
	["supervisor", "procurement_agent"],
	["floor_agent", "dispatcher"],
	["procurement_agent", "dispatcher"],
	["shelf_watcher", "dispatcher"],
	["dispatcher", "shop_tools"],
	["floor_agent", "shop_tools"],
	["procurement_agent", "shop_tools"],
	["procurement_agent", "order_gate"],
	["shelf_watcher", "order_gate"],
];

function ArchitectureMap({ latest, steps }: { latest?: Step; steps: Step[] }) {
	const [selected, setSelected] = useState<AgentKey | null>(null);
	const busy = useMemo(() => {
		const counts: Partial<Record<AgentKey, number>> = {};
		for (const step of steps) counts[step.agent] = (counts[step.agent] ?? 0) + Math.max(1, step.tools.length);
		return counts;
	}, [steps]);
	const live = (a: AgentKey, b: AgentKey) =>
		!!latest && ((latest.agent === a && latest.to === b) || (latest.agent === b && latest.to === a));
	const detail = selected ? AGENTS[selected] : null;

	return (
		<div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3">
			<div className="relative w-full shrink-0" style={{ aspectRatio: `${MAP_W} / ${MAP_H}` }}>
				<svg viewBox={`0 0 ${MAP_W} ${MAP_H}`} className="absolute inset-0 h-full w-full" aria-hidden>
					{WIRES.map(([a, b]) => {
						const active = live(a, b);
						return (
							<line
								key={`${a}-${b}`}
								x1={POSITION[a][0]}
								y1={POSITION[a][1]}
								x2={POSITION[b][0]}
								y2={POSITION[b][1]}
								stroke={active && latest ? AGENTS[latest.agent].colour : "hsl(var(--border))"}
								strokeWidth={active ? 2.5 : 1.25}
								className={active ? "playground-flow" : undefined}
							/>
						);
					})}
				</svg>
				{(Object.keys(AGENTS) as AgentKey[]).map((key) => {
					const agent = AGENTS[key];
					const active = latest?.agent === key || latest?.to === key;
					return (
						<button
							key={key}
							type="button"
							onClick={() => setSelected(selected === key ? null : key)}
							className="absolute flex w-[23%] min-w-[5.5rem] -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1 rounded-xl border bg-background px-1.5 py-1.5 text-center transition-shadow"
							style={{
								left: `${(POSITION[key][0] / MAP_W) * 100}%`,
								top: `${(POSITION[key][1] / MAP_H) * 100}%`,
								borderColor: active || selected === key ? agent.colour : "hsl(var(--border))",
								boxShadow: active ? `0 0 18px -4px ${agent.colour}` : undefined,
							}}
						>
							<span
								className="flex h-7 w-7 items-center justify-center rounded-lg text-white"
								style={{ background: `linear-gradient(135deg, ${agent.gradient[0]}, ${agent.gradient[1]})` }}
							>
								<agent.Icon className="h-4 w-4" aria-hidden />
							</span>
							<span className="w-full truncate text-[11px] font-medium leading-tight">{agent.label}</span>
							<span className="text-[9px] uppercase tracking-wide" style={{ color: agent.colour }}>
								{agent.kind}
								{busy[key] ? ` · ${busy[key]}` : ""}
							</span>
						</button>
					);
				})}
			</div>

			{detail ? (
				<div className="playground-in mt-3 rounded-xl border p-3" style={{ borderColor: detail.colour }}>
					<p className="text-[11px] font-medium uppercase tracking-wide" style={{ color: detail.colour }}>
						{detail.label} · {detail.kind}
					</p>
					<p className="mt-0.5 text-sm font-medium">{detail.role}</p>
					<ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted-foreground">
						{detail.points.map((point) => (
							<li key={point}>{point}</li>
						))}
					</ul>
					{detail.tools ? (
						<p className="mt-2 flex flex-wrap gap-1">
							{detail.tools.map((tool) => (
								<code key={tool} className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
									{tool}
								</code>
							))}
						</p>
					) : null}
				</div>
			) : (
				<p className="mt-3 text-center text-xs text-muted-foreground">
					Two voice agents are language models. The Dispatcher and Order Gate are fixed rules, and the Shelf
					Watcher is a pair of vision models. Click any part to see what it does.
				</p>
			)}
		</div>
	);
}
