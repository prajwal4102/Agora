"use client";

import {
	Check,
	Eye,
	GitBranch,
	Headset,
	type LucideIcon,
	Minus,
	Plus,
	ShieldCheck,
	ShoppingCart,
	User,
	UserCog,
	Wrench,
	X,
} from "lucide-react";
import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ShopEvent } from "@/services/shop";

/**
 * Agent Playground — EXECUTION TRACE.
 *
 * A dark canvas where every hand-off between parts of the system is a BOX,
 * joined to the next by a curved wire. Tool calls are small CIRCLES that fan
 * out below the agent that made them and converge into the next box. A light
 * travels down each new wire, and the step being worked on pulses.
 *
 * Steps play at a steady rhythm however fast the backend fires them, so a
 * burst of five tool calls is watched, not missed.
 *
 * Only the two voice agents are language models. The others are vision models
 * and fixed rules, and each is labelled with what it really is.
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

type Meta = {
	Icon: LucideIcon;
	label: string;
	sub: string;
	kind: string;
	c: string;
	g: [string, string];
};

const META: Record<AgentKey, Meta> = {
	employee: { Icon: User, label: "Floor Employee", sub: "On phone 1 · refills shelves", kind: "HUMAN", c: "#fbbf24", g: ["#fbbf24", "#d97706"] },
	supervisor: { Icon: UserCog, label: "Store Supervisor", sub: "On phone 2 · approves purchases", kind: "HUMAN", c: "#38bdf8", g: ["#38bdf8", "#2563eb"] },
	floor_agent: { Icon: Headset, label: "Floor Agent", sub: "Voice · Hinglish · gets shelves refilled", kind: "LLM AGENT", c: "#fbbf24", g: ["#fbbf24", "#d97706"] },
	procurement_agent: { Icon: ShoppingCart, label: "Procurement Agent", sub: "Voice · compares suppliers · negotiates", kind: "LLM AGENT", c: "#38bdf8", g: ["#38bdf8", "#2563eb"] },
	dispatcher: { Icon: GitBranch, label: "Dispatcher", sub: "Decides who to call, and why", kind: "RULES", c: "#818cf8", g: ["#818cf8", "#4f46e5"] },
	shelf_watcher: { Icon: Eye, label: "Shelf Watcher", sub: "Camera · counts what is on the shelf", kind: "VISION", c: "#a78bfa", g: ["#a78bfa", "#7c3aed"] },
	order_gate: { Icon: ShieldCheck, label: "Order Gate", sub: "No order without a spoken yes", kind: "RULES", c: "#34d399", g: ["#34d399", "#059669"] },
	shop_tools: { Icon: Wrench, label: "Shop Tools · MCP", sub: "Stock · sales · 3 suppliers · budget", kind: "TOOLS", c: "#94a3b8", g: ["#94a3b8", "#475569"] },
};

const DETAILS: Record<AgentKey, { role: string; points: string[]; tools?: string[]; note?: string }> = {
	employee: {
		role: "The person on the shop floor",
		points: [
			"Gets a call in Hinglish when a shelf needs refilling",
			"Says when it is done, or that the stock is not in the back room",
		],
		note: "Cannot approve purchases.",
	},
	supervisor: {
		role: "The only source of approval",
		points: [
			"Gets a call with a recommendation, not a raw alert",
			"Can change the quantity, the supplier, or set a spending limit",
		],
		note: "Nothing is ordered until this person says yes.",
	},
	floor_agent: {
		role: "Voice agent · talks to the employee",
		points: [
			"Agora Conversational AI, speaking through Murf Falcon in Hindi-English",
			"Says what to bring from the back room and how many",
			"If the stock is missing, corrects the count and hands the case back to the Dispatcher",
		],
		tools: ["get_refill_tasks", "complete_refill_task", "report_backroom_shortage", "get_shelf_status"],
	},
	procurement_agent: {
		role: "Voice agent · talks to the supervisor",
		points: [
			"Agora Conversational AI, speaking through Murf Falcon",
			"Checks sales, compares three suppliers, recommends one order",
			"Re-prices when the supervisor pushes back and reads the total aloud",
		],
		tools: ["get_sales_rate", "get_supplier_quotes", "get_budget", "propose_order", "confirm_order", "get_order_status", "schedule_callback"],
		note: "Cannot place an order by itself: every purchase goes through the Order Gate.",
	},
	dispatcher: {
		role: "Triage · who needs to act",
		points: [
			"Low shelf, stock in the back room: ring the employee",
			"Low shelf, empty back room: ring the supervisor",
			"Stays quiet if a call is live, a refill is pending, or the product is on order",
		],
		note: "Fixed rules, not a language model, so it behaves the same every time.",
	},
	shelf_watcher: {
		role: "Perception · counts the shelf",
		points: [
			"A detector finds objects by shape from the catalogue's product names",
			"A second model reads wrapped products the detector cannot name",
			"Believes a count only after it holds steady for three frames",
			"Confirms refills and deliveries by seeing the stock return",
		],
		note: "Runs on the laptop. No cloud vision service.",
	},
	order_gate: {
		role: "The rule that guards every purchase",
		points: [
			"An order is first priced and held as a proposal",
			"It is placed only against a live proposal, with the supervisor's own words",
			"A changed quantity replaces the old proposal, so a stale one cannot be confirmed",
		],
		note: "Enforced on the server, where the model cannot talk its way past it.",
	},
	shop_tools: {
		role: "What the agents can look up",
		points: [
			"Served over MCP, called by Agora's cloud during the conversation",
			"Shelf and back-room stock, sales rate, purchase budget",
			"Price lists from three suppliers with different prices and delivery times",
		],
	},
};

const isAgent = (key: unknown): key is AgentKey => typeof key === "string" && key in META;

type Tool = { id: string; name: string; at: number; failed: boolean; summary: string | null };
type Group = {
	id: string;
	actor: AgentKey;
	color: string;
	at: number;
	title: string; // small coloured line: who → whom
	text: string; // what happened
	tools: Tool[];
	isShelf: boolean;
};

const GOOD = "#34d399";
const BAD = "#fb7185";
const WAIT = "#fbbf24";

/** Which incident an event belongs to: the product it is about. */
function productOf(event: ShopEvent): string | null {
	const detail = event.detail as Record<string, unknown>;
	const args = (detail.args ?? {}) as Record<string, unknown>;
	const result = (detail.result ?? {}) as Record<string, unknown>;
	const found = detail.product ?? result.product ?? args.product;
	return typeof found === "string" && found ? found : null;
}

function colourOf(event: ShopEvent, actor: AgentKey): string {
	const title = event.title.toLowerCase();
	if (title.includes("confirms") || title.includes("answered")) return GOOD;
	if (title.includes("declined") || title.includes("missed") || title.includes("could not")) return BAD;
	if (title.startsWith("calling") || title.startsWith("dialling")) return WAIT;
	return META[actor].c;
}

/* geometry — tops are the TOP edge of each block */
const STAGE_W = 760;
const MAIN_X = 380;
const TOP_Y = 36;
const GAP = 40;
const CARD_W = 360;
const CARD_H = 58;
const TOOL_R = 20;
const TOOL_GAP = 92;
const TOOL_BAND = 122; // extra height when a block has tools (circles + result line)
const ACTIVE_MS = 2300;
const PACE_MS = 750;

const blockH = (group: Group) => CARD_H + (group.tools.length ? TOOL_BAND : 0);

function curve(a: [number, number], b: [number, number]): string {
	if (Math.abs(b[0] - a[0]) < 10) {
		const dy = Math.max(Math.abs(b[1] - a[1]) * 0.5, 16) * (b[1] >= a[1] ? 1 : -1);
		return `M ${a[0]} ${a[1]} C ${a[0]} ${a[1] + dy}, ${b[0]} ${b[1] - dy}, ${b[0]} ${b[1]}`;
	}
	const dy = (b[1] - a[1]) * 0.6;
	return `M ${a[0]} ${a[1]} C ${a[0]} ${a[1] + dy}, ${b[0]} ${b[1] - dy}, ${b[0]} ${b[1]}`;
}

const CARD_BG = "#1a2846";
const CANVAS_BG = "radial-gradient(900px 450px at 50% -10%, #0d1830 0%, #080e1d 60%, #05080f 100%)";

type Glow = CSSProperties & { "--glow-ring"?: string; "--glow-soft"?: string };

const STYLES = `
/* Movement only, so a step is fully readable even if the browser pauses the animation. */
@keyframes pg-in { from { transform: translateY(12px) scale(0.96); } to { transform: none; } }
.pg-in { animation: pg-in 0.28s ease-out; }
@keyframes pg-spin { to { transform: rotate(360deg); } }
.pg-spinner { width: 14px; height: 14px; border-radius: 9999px; border: 2px solid #c7d2fe; border-top-color: #2563eb; animation: pg-spin 0.7s linear infinite; display: block; }
@keyframes pg-dash { to { stroke-dashoffset: -26; } }
.pg-wire-active { stroke-dasharray: 7 6; animation: pg-dash 0.6s linear infinite; }
@keyframes pg-pulse {
  0%, 100% { box-shadow: 0 0 0 0 var(--glow-ring), 0 0 20px var(--glow-soft), 0 10px 26px rgba(0,0,0,0.55); }
  50% { box-shadow: 0 0 0 9px transparent, 0 0 38px var(--glow-soft), 0 10px 26px rgba(0,0,0,0.55); }
}
.pg-pulse { animation: pg-pulse 1.3s ease-in-out infinite; }
`;

function StepCard({ group, y, active }: { group: Group; y: number; active: boolean }) {
	const actor = META[group.actor];
	const style: Glow = {
		left: MAIN_X - CARD_W / 2,
		top: y,
		width: CARD_W,
		height: CARD_H,
		background: CARD_BG,
		border: `1px solid ${active ? group.color : "rgba(148,163,184,0.26)"}`,
		"--glow-ring": `${group.color}55`,
		"--glow-soft": `${group.color}66`,
		transform: `scale(${active ? 1.04 : 1})`,
		...(active ? {} : { boxShadow: "0 8px 22px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.06)" }),
	};
	return (
		<div className={`pg-in absolute rounded-xl transition-all duration-500 ${active ? "pg-pulse" : ""}`} style={style} title={group.text}>
			<div className="flex h-full items-center gap-2.5 px-2.5">
				<div
					className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-white"
					style={{ background: `linear-gradient(135deg, ${actor.g[0]}, ${actor.g[1]})`, boxShadow: `0 3px 10px ${actor.g[1]}66` }}
				>
					<actor.Icon size={17} />
				</div>
				<div className="min-w-0 flex-1">
					<div className="truncate text-[9px] font-bold tracking-[0.12em]" style={{ color: group.color }}>
						{group.title}
					</div>
					<div className="truncate text-[12px] font-semibold leading-tight text-white">{group.text}</div>
				</div>
				<div className="shrink-0">
					{active ? (
						<span className="pg-spinner" style={{ borderColor: `${group.color}33`, borderTopColor: group.color }} />
					) : (
						<span className="grid place-items-center rounded-full" style={{ background: `${group.color}22`, color: group.color, width: 18, height: 18 }}>
							<Check size={10} strokeWidth={3} />
						</span>
					)}
				</div>
			</div>
		</div>
	);
}

function ToolCircle({ tool, actor, x, y, active }: { tool: Tool; actor: AgentKey; x: number; y: number; active: boolean }) {
	const ring: Glow = {
		width: TOOL_R * 2,
		height: TOOL_R * 2,
		background: "linear-gradient(135deg, #64748b, #334155)",
		border: `2px solid ${active ? META[actor].c : tool.failed ? BAD : "rgba(255,255,255,0.15)"}`,
		"--glow-ring": `${META[actor].c}55`,
		"--glow-soft": `${META[actor].c}66`,
		boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
	};
	return (
		<div className="pg-in absolute transition-all duration-500" style={{ left: x - 46, top: y - TOOL_R, width: 92 }} title={tool.summary ?? tool.name}>
			<div className={`mx-auto grid place-items-center rounded-full text-white ${active ? "pg-pulse" : ""}`} style={ring}>
				{active ? <span className="pg-spinner" style={{ borderColor: "#ffffff33", borderTopColor: "#fff" }} /> : <Wrench size={15} />}
			</div>
			{!active ? (
				<span
					className="absolute grid place-items-center rounded-full border-2 border-[#0a1222] text-[#07101f]"
					style={{ width: 14, height: 14, left: 46 + TOOL_R - 12, top: TOOL_R * 2 - 12, background: tool.failed ? BAD : GOOD }}
				>
					{tool.failed ? <X size={8} strokeWidth={4} /> : <Check size={8} strokeWidth={4} />}
				</span>
			) : null}
			<div className="mt-1 truncate text-center font-mono text-[8.5px] text-slate-400">{tool.name}</div>
		</div>
	);
}

type Wire = { d: string; color?: string; active?: boolean };
type Flight = { id: string; d: string; color: string };

/** Zoomable, pannable stage that follows the newest step down the page. */
function Stage({
	children,
	wires,
	flights,
	stageH,
	zoom,
	onZoom,
	resetSignal,
	followY,
}: {
	children: ReactNode;
	wires: Wire[];
	flights: Flight[];
	stageH: number;
	zoom: number;
	onZoom: (delta: number) => void;
	resetSignal: number;
	followY: number;
}) {
	const boxRef = useRef<HTMLDivElement | null>(null);
	const drag = useRef<{ x: number; y: number } | null>(null);
	const follow = useRef(true);
	const [pan, setPan] = useState({ x: 0, y: 0 });
	const [dragging, setDragging] = useState(false);

	// biome-ignore lint/correctness/useExhaustiveDependencies: recentre only when asked to
	useEffect(() => {
		const el = boxRef.current;
		if (!el) return;
		follow.current = true;
		setPan({ x: (el.clientWidth - STAGE_W * zoom) / 2, y: 12 });
	}, [resetSignal, zoom]);

	useEffect(() => {
		const el = boxRef.current;
		if (!el || !follow.current || !followY) return;
		const desired = el.clientHeight - (followY + 40) * zoom;
		setPan((p) => (desired < p.y ? { ...p, y: desired } : p));
	}, [followY, zoom]);

	useEffect(() => {
		const el = boxRef.current;
		if (!el) return;
		const onWheel = (e: WheelEvent) => {
			if (e.ctrlKey) {
				e.preventDefault();
				onZoom(e.deltaY < 0 ? 0.1 : -0.1);
				return;
			}
			follow.current = false;
			setPan((p) => {
				const minY = Math.min(12, el.clientHeight - stageH * zoom - 12);
				return { ...p, y: Math.max(Math.min(p.y - e.deltaY, 12), minY) };
			});
		};
		el.addEventListener("wheel", onWheel, { passive: false });
		return () => el.removeEventListener("wheel", onWheel);
	}, [onZoom, stageH, zoom]);

	return (
		<div
			ref={boxRef}
			className="relative min-h-0 flex-1 select-none overflow-hidden"
			style={{ cursor: dragging ? "grabbing" : "grab", background: CANVAS_BG }}
			onMouseDown={(e) => {
				e.preventDefault();
				follow.current = false;
				drag.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
				setDragging(true);
			}}
			onMouseMove={(e) => {
				if (drag.current) setPan({ x: e.clientX - drag.current.x, y: e.clientY - drag.current.y });
			}}
			onMouseUp={() => {
				drag.current = null;
				setDragging(false);
			}}
			onMouseLeave={() => {
				drag.current = null;
				setDragging(false);
			}}
		>
			<div
				className="absolute"
				style={{ width: STAGE_W, height: stageH, transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: "0 0" }}
			>
				<svg width={STAGE_W} height={stageH} viewBox={`0 0 ${STAGE_W} ${stageH}`} className="absolute inset-0" aria-hidden>
					<defs>
						<filter id="pg-glow" x="-50%" y="-50%" width="200%" height="200%">
							<feGaussianBlur stdDeviation="4" />
						</filter>
					</defs>
					{wires.map((wire, i) => (
						<path
							// biome-ignore lint/suspicious/noArrayIndexKey: wires are recomputed as a whole each render
							key={i}
							d={wire.d}
							fill="none"
							stroke={wire.color || "#2e4066"}
							strokeWidth={wire.active ? 2.5 : 2}
							opacity={wire.active ? 0.95 : 0.8}
							className={wire.active ? "pg-wire-active" : undefined}
						/>
					))}
					{flights.map((flight) => (
						<g key={flight.id}>
							<path d={flight.d} fill="none" stroke={flight.color} strokeWidth="6" opacity="0.22" filter="url(#pg-glow)" />
							<circle r="8" fill={flight.color} opacity="0.35" filter="url(#pg-glow)">
								<animateMotion dur="0.9s" fill="freeze" path={flight.d} />
							</circle>
							<circle r="3.5" fill="#fff">
								<animateMotion dur="0.9s" fill="freeze" path={flight.d} />
							</circle>
						</g>
					))}
				</svg>
				{children}
			</div>
		</div>
	);
}

/* full architecture map (overlay) — layered: people → voice agents →
   coordination → perception and the purchase rule → shared tools */
const ARCH_POS: Record<AgentKey, [number, number]> = {
	employee: [200, 62],
	supervisor: [560, 62],
	floor_agent: [200, 172],
	procurement_agent: [560, 172],
	dispatcher: [380, 282],
	shelf_watcher: [150, 398],
	order_gate: [610, 398],
	shop_tools: [380, 512],
};
const ARCH_WIRES: [AgentKey, AgentKey][] = [
	["employee", "floor_agent"],
	["supervisor", "procurement_agent"],
	["floor_agent", "dispatcher"],
	["procurement_agent", "dispatcher"],
	["dispatcher", "shelf_watcher"],
	["procurement_agent", "order_gate"],
	["shelf_watcher", "order_gate"],
	["dispatcher", "shop_tools"],
	["floor_agent", "shop_tools"],
	["procurement_agent", "shop_tools"],
];
const ARCH_LAYERS: [number, string][] = [
	[62, "PEOPLE"],
	[172, "VOICE AGENTS · LLM"],
	[282, "COORDINATION · RULES"],
	[398, "PERCEPTION · PURCHASE RULE"],
	[512, "SHARED TOOLS · MCP"],
];
const ARCH_H = 580;
const NODE_W = 196;
const NODE_H = 54;

function Architecture({ onClose, live }: { onClose: () => void; live: { from: AgentKey; to: AgentKey | null } | null }) {
	const [selected, setSelected] = useState<AgentKey | null>(null);
	const boxRef = useRef<HTMLDivElement | null>(null);
	const [scale, setScale] = useState(1);
	useEffect(() => {
		const el = boxRef.current;
		if (!el) return;
		const fit = () => setScale(Math.min(1.15, el.clientWidth / STAGE_W, el.clientHeight / ARCH_H));
		fit();
		const observer = new ResizeObserver(fit);
		observer.observe(el);
		return () => observer.disconnect();
	}, []);
	const isLive = (a: AgentKey, b: AgentKey) => !!live && ((live.from === a && live.to === b) || (live.from === b && live.to === a));
	const detail = selected ? { ...META[selected], ...DETAILS[selected] } : null;

	return (
		<div className="absolute inset-0 z-20 flex flex-col" style={{ background: CANVAS_BG }}>
			<div className="flex items-center gap-3 border-b border-white/[0.07] px-4 py-2.5">
				<span className="text-[13px] font-semibold text-slate-200">Architecture</span>
				<span className="text-[11px] text-slate-500">click any part to see what it does</span>
				<button
					type="button"
					onClick={onClose}
					className="ml-auto rounded-lg border border-white/15 bg-white/[0.06] px-3 py-1.5 text-[11.5px] font-semibold text-slate-200 transition hover:border-sky-400/60 hover:text-sky-300"
				>
					Back to trace
				</button>
			</div>
			<div className="flex min-h-0 flex-1">
				<div ref={boxRef} className="relative min-w-0 flex-1 overflow-hidden">
					<div
						className="absolute left-1/2 top-1/2"
						style={{ width: STAGE_W, height: ARCH_H, transform: `translate(-50%, -50%) scale(${scale})` }}
					>
						<svg width={STAGE_W} height={ARCH_H} className="absolute inset-0" aria-hidden>
							{ARCH_LAYERS.map(([y, label]) => (
								<g key={label}>
									<line x1={20} x2={STAGE_W - 20} y1={y + NODE_H / 2 + 22} y2={y + NODE_H / 2 + 22} stroke="rgba(148,163,184,0.07)" />
									<text x={24} y={y - NODE_H / 2 - 8} fill="#475569" fontSize="9" fontWeight="700" letterSpacing="1.6">
										{label}
									</text>
								</g>
							))}
							{ARCH_WIRES.map(([a, b]) => {
								const [top, bottom] = ARCH_POS[a][1] <= ARCH_POS[b][1] ? [a, b] : [b, a];
								const on = isLive(a, b);
								return (
									<path
										key={`${a}-${b}`}
										d={curve([ARCH_POS[top][0], ARCH_POS[top][1] + NODE_H / 2], [ARCH_POS[bottom][0], ARCH_POS[bottom][1] - NODE_H / 2])}
										fill="none"
										stroke={on && live ? META[live.from].c : "#2e4066"}
										strokeWidth={on ? 2.5 : 1.6}
										opacity={on ? 0.95 : 0.75}
										className={on ? "pg-wire-active" : undefined}
									/>
								);
							})}
						</svg>
						{(Object.keys(META) as AgentKey[]).map((key) => {
							const meta = META[key];
							const on = live?.from === key || live?.to === key;
							const style: Glow = {
								left: ARCH_POS[key][0] - NODE_W / 2,
								top: ARCH_POS[key][1] - NODE_H / 2,
								width: NODE_W,
								height: NODE_H,
								background: CARD_BG,
								border: `1px solid ${selected === key ? "#fff" : on ? meta.c : "rgba(148,163,184,0.26)"}`,
								"--glow-ring": `${meta.c}55`,
								"--glow-soft": `${meta.c}66`,
								...(on ? {} : { boxShadow: "0 8px 22px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.06)" }),
							};
							return (
								<button
									key={key}
									type="button"
									onClick={() => setSelected(selected === key ? null : key)}
									className={`absolute flex items-center gap-2.5 rounded-xl px-2.5 text-left transition-all duration-300 hover:scale-[1.04] ${on ? "pg-pulse" : ""}`}
									style={style}
								>
									<span
										className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-white"
										style={{ background: `linear-gradient(135deg, ${meta.g[0]}, ${meta.g[1]})`, boxShadow: `0 3px 10px ${meta.g[1]}66` }}
									>
										<meta.Icon size={17} />
									</span>
									<span className="min-w-0">
										<span className="block truncate text-[12px] font-semibold leading-tight text-white">{meta.label}</span>
										<span className="block truncate text-[9px] font-bold tracking-[0.12em]" style={{ color: meta.c }}>
											{meta.kind}
										</span>
									</span>
								</button>
							);
						})}
					</div>
				</div>
				{detail ? (
					<aside className="pg-in w-72 shrink-0 overflow-y-auto border-l border-white/[0.07] p-4">
						<div className="flex items-center gap-2.5">
							<span className="grid h-9 w-9 place-items-center rounded-lg text-white" style={{ background: `linear-gradient(135deg, ${detail.g[0]}, ${detail.g[1]})` }}>
								<detail.Icon size={17} />
							</span>
							<div>
								<div className="text-[13px] font-semibold text-white">{detail.label}</div>
								<div className="text-[9px] font-bold tracking-[0.12em]" style={{ color: detail.c }}>
									{detail.kind}
								</div>
							</div>
						</div>
						<p className="mt-3 text-[12px] font-semibold text-slate-200">{detail.role}</p>
						<ul className="mt-2 space-y-1.5">
							{detail.points.map((point) => (
								<li key={point} className="flex gap-2 text-[11.5px] leading-snug text-slate-400">
									<span className="mt-1.5 h-1 w-1 shrink-0 rounded-full" style={{ background: detail.c }} />
									{point}
								</li>
							))}
						</ul>
						{detail.tools ? (
							<div className="mt-3 flex flex-wrap gap-1">
								{detail.tools.map((tool) => (
									<code key={tool} className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[9.5px] text-slate-300">
										{tool}
									</code>
								))}
							</div>
						) : null}
						{detail.note ? (
							<p className="mt-3 rounded-lg border px-2.5 py-2 text-[11px] leading-snug" style={{ borderColor: `${detail.c}55`, color: detail.c, background: `${detail.c}0f` }}>
								{detail.note}
							</p>
						) : null}
					</aside>
				) : null}
			</div>
		</div>
	);
}

type Tabs = Record<string, Group[]>;

export function AgentPlayground({
	events,
	summarise,
}: {
	events: ShopEvent[];
	summarise: (detail: Record<string, unknown>) => string | null;
}) {
	const seen = useRef(0);
	const queue = useRef<ShopEvent[]>([]);
	const pacer = useRef<ReturnType<typeof setInterval> | null>(null);
	const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
	const sticky = useRef<string>("shelf"); // incident the last event belonged to
	const [tabs, setTabs] = useState<Tabs>({});
	const [order, setOrder] = useState<string[]>([]);
	const [activeTab, setActiveTab] = useState<string | null>(null);
	const [activeId, setActiveId] = useState<string | null>(null);
	const [flightIds, setFlightIds] = useState<{ id: string; group: string; color: string; tab: string }[]>([]);
	const [live, setLive] = useState<{ from: AgentKey; to: AgentKey | null } | null>(null);
	const [showArch, setShowArch] = useState(false);
	const [zoom, setZoom] = useState(1);
	const [resetSignal, setResetSignal] = useState(1);
	const [, setTick] = useState(0);
	const frameRef = useRef<HTMLDivElement | null>(null);

	const bumpZoom = useCallback((delta: number) => setZoom((z) => Math.min(1.8, Math.max(0.45, +(z + delta).toFixed(2)))), []);

	// Fit the 760-wide stage to the panel the first time it is measured.
	useEffect(() => {
		const el = frameRef.current;
		if (el) setZoom(Math.min(1.15, Math.max(0.55, +(el.clientWidth / (STAGE_W + 24)).toFixed(2))));
	}, []);

	const apply = useCallback(
		(event: ShopEvent, animate: boolean) => {
			const detail = event.detail as Record<string, unknown>;
			const actor: AgentKey = isAgent(detail.agent)
				? detail.agent
				: event.kind === "shelf"
					? "shelf_watcher"
					: event.kind === "tool"
						? "procurement_agent"
						: "dispatcher";
			const target = isAgent(detail.to) ? detail.to : null;
			const tab = productOf(event) ?? sticky.current;
			sticky.current = tab;
			const id = String(event.id);
			const at = animate ? Date.now() : 0;
			const isTool = event.kind === "tool";
			const failed = isTool && (detail.result as { ok?: boolean } | undefined)?.ok === false;
			const color = isTool ? META[actor].c : colourOf(event, actor);
			const heading = `${META[actor].label.toUpperCase()}${target ? ` → ${META[target].label.toUpperCase()}` : ""}`;

			setOrder((o) => (o.includes(tab) ? o : [...o, tab]));
			if (animate) {
				setActiveTab(tab);
				setActiveId(id);
				setLive({ from: actor, to: target });
			}
			setTabs((all) => {
				const groups = all[tab] ?? [];
				const last = groups[groups.length - 1];
				if (isTool) {
					const tool: Tool = { id, name: String(detail.tool ?? "tool"), at, failed, summary: summarise(detail) };
					const next =
						last && last.actor === actor && last.tools.length
							? [...groups.slice(0, -1), { ...last, tools: [...last.tools, tool] }]
							: [...groups, { id, actor, color: META[actor].c, at, title: `${heading} · RUNNING ACTIONS`, text: "Working with tools", tools: [tool], isShelf: false }];
					return { ...all, [tab]: next };
				}
				const isShelf = event.kind === "shelf";
				if (isShelf && last?.isShelf) {
					// A run of counts is one step that keeps updating, not a pile of boxes.
					return { ...all, [tab]: [...groups.slice(0, -1), { ...last, id, at, text: event.title }] };
				}
				return { ...all, [tab]: [...groups, { id, actor, color, at, title: heading, text: event.title, tools: [], isShelf }] };
			});
			if (animate && !isTool) {
				setFlightIds((f) => [...f, { id: `f${id}`, group: id, color, tab }]);
				timers.current.push(setTimeout(() => setFlightIds((f) => f.filter((x) => x.id !== `f${id}`)), 1300));
			}
		},
		[summarise],
	);

	// biome-ignore lint/correctness/useExhaustiveDependencies: react to the stream growing or shrinking
	useEffect(() => {
		const usable = events.filter((event) => event.kind !== "system");
		if (usable.length < seen.current) {
			// The stream shrank: the demo was reset. Start clean, with no replay.
			queue.current = [];
			if (pacer.current) clearInterval(pacer.current);
			pacer.current = null;
			sticky.current = "shelf";
			setTabs({});
			setOrder([]);
			setActiveTab(null);
			setActiveId(null);
			setFlightIds([]);
			setLive(null);
			seen.current = 0;
		}
		const fresh = usable.slice(seen.current);
		const firstLoad = seen.current === 0 && fresh.length > 6;
		seen.current = usable.length;
		if (firstLoad) {
			// Opening the page on a long history: lay it out at once, then follow live.
			for (const event of fresh) apply(event, false);
			setActiveTab(sticky.current);
			return;
		}
		queue.current.push(...fresh);
		if (!pacer.current && queue.current.length) {
			const step = () => {
				const next = queue.current.shift();
				if (!next) {
					if (pacer.current) clearInterval(pacer.current);
					pacer.current = null;
					return;
				}
				apply(next, true);
			};
			step(); // the first plays immediately
			if (queue.current.length) pacer.current = setInterval(step, PACE_MS);
		}
	}, [events.length, apply]);

	useEffect(
		() => () => {
			if (pacer.current) clearInterval(pacer.current);
			for (const timer of timers.current) clearTimeout(timer);
		},
		[],
	);

	// Re-render on a beat so "active" spinners settle into ticks.
	useEffect(() => {
		const id = setInterval(() => setTick((t) => t + 1), 900);
		return () => clearInterval(id);
	}, []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: recentre when the visible incident changes
	useEffect(() => {
		if (activeTab != null) setResetSignal((s) => s + 1);
	}, [activeTab]);

	const groups = useMemo(() => (activeTab ? (tabs[activeTab] ?? []) : []), [tabs, activeTab]);

	// ---- layout ----
	const tops: number[] = [];
	let cursor = TOP_Y;
	for (const group of groups) {
		tops.push(cursor);
		cursor += blockH(group) + GAP;
	}
	const stageH = Math.max(560, cursor + 40);
	const now = Date.now();
	const isActive = (id: string, at: number) => activeId === id && now - at < ACTIVE_MS;
	const toolPos = (gi: number, ti: number, n: number): [number, number] => [MAIN_X + (ti - (n - 1) / 2) * TOOL_GAP, tops[gi] + CARD_H + 46];

	const wires: Wire[] = [];
	groups.forEach((group, i) => {
		const next = groups[i + 1];
		const cardBottom: [number, number] = [MAIN_X, tops[i] + CARD_H];
		const nextTop: [number, number] | null = next ? [MAIN_X, tops[i + 1]] : null;
		const n = group.tools.length;
		const fresh = isActive(group.id, group.at) || group.tools.some((tool) => isActive(tool.id, tool.at));
		if (n > 0) {
			group.tools.forEach((tool, ti) => {
				const [tx, ty] = toolPos(i, ti, n);
				wires.push({ d: curve(cardBottom, [tx, ty - TOOL_R]), color: fresh ? META[group.actor].c : undefined, active: isActive(tool.id, tool.at) });
				if (nextTop) wires.push({ d: curve([tx, ty + TOOL_R + 34], nextTop) });
			});
		} else if (nextTop && next) {
			const on = isActive(next.id, next.at);
			wires.push({ d: curve(cardBottom, nextTop), color: on ? next.color : undefined, active: on });
		}
	});

	const flights: Flight[] = flightIds
		.filter((flight) => flight.tab === activeTab)
		.flatMap((flight) => {
			const index = groups.findIndex((group) => group.id === flight.group);
			if (index <= 0) return [];
			const from: [number, number] = [MAIN_X, tops[index - 1] + blockH(groups[index - 1])];
			return [{ id: flight.id, color: flight.color, d: curve(from, [MAIN_X, tops[index]]) }];
		});

	const lastGroup = groups[groups.length - 1];
	const anyActive = !!lastGroup && (now - lastGroup.at < ACTIVE_MS || lastGroup.tools.some((tool) => now - tool.at < ACTIVE_MS));
	const followY = groups.length ? tops[tops.length - 1] + blockH(lastGroup) : 0;

	return (
		<div ref={frameRef} className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#05080f] shadow-2xl">
			<style>{STYLES}</style>
			<div className="flex flex-wrap items-center gap-3 border-b border-white/[0.07] px-4 py-2.5">
				<button
					type="button"
					onClick={() => setShowArch(true)}
					className="rounded-lg border border-white/15 bg-white/[0.06] px-3 py-1.5 text-[11.5px] font-semibold text-slate-200 transition hover:border-sky-400/60 hover:text-sky-300"
				>
					Architecture
				</button>
				<span className="flex items-center gap-2 text-[13px] font-semibold text-slate-200">
					<span className={`h-2 w-2 rounded-full ${anyActive ? "animate-pulse bg-emerald-400" : "bg-slate-600"}`} />
					Execution trace
				</span>
				<span className="flex flex-wrap gap-1.5">
					{order.map((tab) => (
						<button
							key={tab}
							type="button"
							onClick={() => setActiveTab(tab)}
							className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium capitalize transition ${
								activeTab === tab ? "border-sky-400/60 bg-sky-400/10 text-sky-200" : "border-white/10 text-slate-400 hover:text-slate-200"
							}`}
						>
							{tab}
						</button>
					))}
				</span>
				<span className="ml-auto flex items-center gap-1 text-slate-400">
					<button type="button" aria-label="Zoom out" onClick={() => bumpZoom(-0.1)} className="grid h-6 w-6 place-items-center rounded-md border border-white/10 hover:text-white">
						<Minus size={12} />
					</button>
					<button type="button" onClick={() => setResetSignal((s) => s + 1)} className="rounded-md border border-white/10 px-2 py-0.5 text-[10.5px] tabular-nums hover:text-white" title="Recentre">
						{Math.round(zoom * 100)}%
					</button>
					<button type="button" aria-label="Zoom in" onClick={() => bumpZoom(0.1)} className="grid h-6 w-6 place-items-center rounded-md border border-white/10 hover:text-white">
						<Plus size={12} />
					</button>
				</span>
			</div>

			{groups.length === 0 ? (
				<div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center" style={{ background: CANVAS_BG }}>
					<span className="grid h-12 w-12 place-items-center rounded-2xl text-white" style={{ background: "linear-gradient(135deg, #a78bfa, #7c3aed)", boxShadow: "0 8px 30px #7c3aed66" }}>
						<Eye size={22} />
					</span>
					<p className="text-sm font-semibold text-slate-200">Watching the shelf</p>
					<p className="max-w-xs text-xs text-slate-500">Take a product off the shelf. Every hand-off between the agents will be drawn here as it happens.</p>
				</div>
			) : (
				<Stage wires={wires} flights={flights} stageH={stageH} zoom={zoom} onZoom={bumpZoom} resetSignal={resetSignal} followY={followY}>
					{groups.map((group, i) => {
						const n = group.tools.length;
						const lastTool = group.tools[n - 1];
						const working = group.tools.some((tool) => isActive(tool.id, tool.at));
						return (
							<div key={`${activeTab}-${i}`}>
								<StepCard group={group} y={tops[i]} active={isActive(group.id, group.at) || working} />
								{group.tools.map((tool, ti) => {
									const [x, y] = toolPos(i, ti, n);
									return <ToolCircle key={tool.id} tool={tool} actor={group.actor} x={x} y={y} active={isActive(tool.id, tool.at)} />;
								})}
								{lastTool?.summary ? (
									<div
										className="absolute truncate text-center text-[10.5px]"
										style={{ left: 20, width: STAGE_W - 40, top: tops[i] + CARD_H + 46 + TOOL_R + 22, color: lastTool.failed ? BAD : "#94a3b8" }}
										title={lastTool.summary}
									>
										{lastTool.summary}
									</div>
								) : null}
							</div>
						);
					})}
				</Stage>
			)}

			{showArch ? <Architecture onClose={() => setShowArch(false)} live={live} /> : null}
		</div>
	);
}
