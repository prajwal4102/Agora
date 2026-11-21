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

import type { AgoraSession, ShopEvent } from "@/services/shop";

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
	floor_agent: { Icon: Headset, label: "Replenishment Agent", sub: "Voice · Hinglish · gets shelves refilled", kind: "LLM AGENT", c: "#fbbf24", g: ["#fbbf24", "#d97706"] },
	procurement_agent: { Icon: ShoppingCart, label: "Procurement Agent", sub: "Voice · compares suppliers · negotiates", kind: "LLM AGENT", c: "#38bdf8", g: ["#38bdf8", "#2563eb"] },
	dispatcher: { Icon: GitBranch, label: "Orchestrator", sub: "Decides who to call, and why", kind: "RULES", c: "#818cf8", g: ["#818cf8", "#4f46e5"] },
	shelf_watcher: { Icon: Eye, label: "Shelf Watcher", sub: "Camera · counts what is on the shelf", kind: "VISION", c: "#a78bfa", g: ["#a78bfa", "#7c3aed"] },
	order_gate: { Icon: ShieldCheck, label: "Order Gate", sub: "No order without a spoken yes", kind: "RULES", c: "#34d399", g: ["#34d399", "#059669"] },
	shop_tools: { Icon: Wrench, label: "Shop Tools · MCP", sub: "Stock · forecast · 3 suppliers · budget", kind: "TOOLS", c: "#94a3b8", g: ["#94a3b8", "#475569"] },
};

/** What a voice agent is made of. Everything here is run by Agora's cloud for
 *  the length of a call; our backend only starts it and answers its tool calls. */
const VOICE_STACK = [
	"Agora Conversational AI Engine",
	"Agora RTC · live audio",
	"Agora RTM · transcripts",
	"Deepgram nova-3 · speech-to-text",
	"GPT-4o mini · reasoning",
	"Murf Falcon · voice",
	"MCP · tools called from Agora's cloud",
];

type Detail = {
	role: string;
	/** Where this part lives and how it is reached: label, value. */
	where: [string, string][];
	points: string[];
	/** Tools this part may run, each with what it does. */
	tools?: [string, string][];
	/** What it is refused, and by what. */
	denied?: string;
	note?: string;
	stack?: string[];
};

const FLOOR_TOOLS: [string, string][] = [
	["get_shelf_status", "shelf and back-room count"],
	["get_refill_tasks", "open refill jobs"],
	["complete_refill_task", "mark a shelf refilled"],
	["report_backroom_shortage", "stock was not in the back room"],
	["schedule_callback", "ring the employee back later"],
];
const PROCUREMENT_TOOLS: [string, string][] = [
	["get_shelf_status", "shelf and back-room count"],
	["get_sales_rate", "how fast it sells"],
	["get_demand_forecast", "expected demand before an occasion"],
	["get_supplier_quotes", "three suppliers, priced, in one call"],
	["get_budget", "purchase budget left"],
	["propose_order", "price an order and hold it"],
	["confirm_order", "place it, with the spoken approval"],
	["get_order_status", "orders and arrival times"],
	["schedule_callback", "ring the supervisor back later"],
];

const DETAILS: Record<AgentKey, Detail> = {
	employee: {
		role: "The person on the shop floor",
		where: [
			["Where", "Their own phone, on the /phone page"],
			["Connected by", "Agora RTC audio channel, one per call"],
			["Talks to", "Replenishment Agent only"],
		],
		points: [
			"Gets a call in Hinglish when a shelf needs refilling",
			"Says when it is done, or that the stock is not in the back room",
			"Can ring the agent to ask what is pending",
		],
		note: "Cannot approve purchases.",
	},
	supervisor: {
		role: "The only source of approval",
		where: [
			["Where", "Their own phone, on the /phone page"],
			["Connected by", "Agora RTC audio channel, one per call"],
			["Talks to", "Procurement Agent only"],
		],
		points: [
			"Gets a call with a recommendation, not a raw alert",
			"Can change the quantity, the supplier, or set a spending limit",
			"Can ring the agent to ask about stock or orders",
		],
		note: "Nothing is ordered until this person says yes.",
	},
	floor_agent: {
		role: "Voice agent · talks to the employee",
		where: [
			["Built in", "Agora Agent Studio, saved as “Floor Agent”"],
			["Runs in", "Agora's cloud, on the Conversational AI Engine"],
			["Lives for", "One call. Our backend starts it when the phone is answered"],
			["Speaks", "Murf Falcon · voice Anisha · hi-IN"],
			["Reaches tools", "Over MCP, from Agora's cloud into our backend"],
		],
		points: [
			"Says what to bring from the back room and how many",
			"Marks the refill done when the employee says so",
			"If the stock is missing, corrects the count and hands the case back to the Orchestrator",
		],
		tools: FLOOR_TOOLS,
		denied: "No supplier, price, budget or order tools. Our server refuses them on this call.",
		stack: VOICE_STACK,
	},
	procurement_agent: {
		role: "Voice agent · talks to the supervisor",
		where: [
			["Built in", "Agora Agent Studio, saved as “Procurement Agent”"],
			["Runs in", "Agora's cloud, on the Conversational AI Engine"],
			["Lives for", "One call. Our backend starts it when the phone is answered"],
			["Speaks", "Murf Falcon · voice Anisha · en-IN"],
			["Reaches tools", "Over MCP, from Agora's cloud into our backend"],
		],
		points: [
			"Forecasts demand, ordering more before Diwali, Christmas, Valentine's Day or exams",
			"Compares three suppliers against that forecast and the budget, recommends one order",
			"Re-prices when the supervisor pushes back and reads the total aloud",
			"Places the order only after a spoken yes",
		],
		tools: PROCUREMENT_TOOLS,
		denied: "No refill-task tools. Cannot place an order by itself: every purchase goes through the Order Gate.",
		stack: VOICE_STACK,
	},
	dispatcher: {
		role: "Decides who needs to act, and when",
		where: [
			["Runs in", "Our backend (FastAPI), on the store's machine"],
			["Triggered by", "Every new shelf count, and a 3-second timer for callbacks"],
			["Reads", "Stock, refill tasks and orders, straight from the shop database"],
			["Hands off by", "Queuing a call. The phone rings, then Agora starts the agent"],
		],
		points: [
			"Low shelf, stock in the back room: ring the employee",
			"Low shelf, empty back room: ring the supervisor",
			"Stays quiet if a call is live, a refill is pending, the product is on order, a callback is booked, or the product is switched off",
			"Rings a promised callback when it falls due",
			"Watches each live call: captions it, and pushes the agent on if it promises to check and goes silent",
		],
		note: "Fixed rules, not a language model, so it behaves the same every time. The two voice agents never talk to each other directly: every hand-over passes through here.",
	},
	shelf_watcher: {
		role: "Perception · counts the shelf",
		where: [
			["Runs in", "Our backend, on the store's machine"],
			["Sees through", "A webcam. The dashboard sends it frames"],
			["Models", "YOLO-World detector and a CLIP reader, both local"],
			["Reports to", "Orchestrator, with a count per product"],
		],
		points: [
			"A detector finds objects by shape from the catalogue's product names",
			"A second model reads wrapped products the detector cannot name",
			"Believes a count only after it holds steady for three frames",
			"Confirms refills and deliveries by seeing the stock return",
		],
		note: "No cloud vision service. Pictures of the shelf never leave the store.",
	},
	order_gate: {
		role: "The rule that guards every purchase",
		where: [
			["Runs in", "Our backend, inside propose_order and confirm_order"],
			["Reached by", "Procurement Agent only"],
			["Checks", "Supplier stock, budget, a live proposal, the spoken approval"],
		],
		points: [
			"An order is first priced and held as a proposal",
			"It is placed only against a live proposal, with the supervisor's own words",
			"A changed quantity replaces the old proposal, so a stale one cannot be confirmed",
			"The approval must be a clear yes: a no, a wait or an unclear answer is refused",
			"A proposal cannot be confirmed in the same breath it was made, before it can be heard",
			"Refuses a second order while one is on its way, and any order whose price changed",
		],
		note: "Enforced on the server, where the model cannot talk its way past it.",
	},
	shop_tools: {
		role: "What the agents can look up and do",
		where: [
			["Runs in", "Our backend, as an MCP server at /mcp"],
			["Called from", "Agora's cloud, over a public HTTPS address"],
			["Data", "A shop database on the store's machine (demo data)"],
			["Scope", "Each agent is limited to the tools for its job, checked on every call"],
		],
		points: [
			"12 tools in all. Five for the floor, nine for procurement, two shared",
			"Three suppliers with different prices, stock and delivery times",
			"A failed tool returns the reason as data, so the agent can explain it and offer another route",
		],
		tools: [
			...PROCUREMENT_TOOLS,
			...FLOOR_TOOLS.filter(([name]) => !PROCUREMENT_TOOLS.some(([other]) => other === name)),
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
	isSpeech: boolean; // something said on the call: shown as a caption, on two lines
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
	if (title.includes("confirms") || title.includes("answered") || title.startsWith("summary")) return GOOD;
	if (title.includes("declined") || title.includes("missed") || title.includes("could not")) return BAD;
	if (title.startsWith("calling") || title.startsWith("dialling") || title.startsWith("callback")) return WAIT;
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

const SPEECH_H = 76; // captions get a second line
const cardH = (group: Group) => (group.isSpeech ? SPEECH_H : CARD_H);
const blockH = (group: Group) => cardH(group) + (group.tools.length ? TOOL_BAND : 0);

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
		height: cardH(group),
		background: group.isSpeech ? "#152238" : CARD_BG,
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
					{group.isSpeech ? (
						<div className="line-clamp-2 text-[12px] font-medium italic leading-snug text-slate-100">“{group.text}”</div>
					) : (
						<div className="truncate text-[12px] font-semibold leading-tight text-white">{group.text}</div>
					)}
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
	const lastManual = useRef(0); // when the viewer last moved the canvas by hand
	const [pan, setPan] = useState({ x: 0, y: 0 });
	const [dragging, setDragging] = useState(false);

	// Where the canvas must sit for the newest step to be fully on screen: the
	// newest step rests a little above the bottom edge, and older steps move up
	// and out of view. A short trace simply sits at the top.
	const latestY = useCallback(
		(el: HTMLDivElement) => Math.min(12, el.clientHeight - (followY + 56) * zoom),
		[followY, zoom],
	);

	// biome-ignore lint/correctness/useExhaustiveDependencies: recentre only when asked to
	useEffect(() => {
		const el = boxRef.current;
		if (!el) return;
		follow.current = true;
		setPan({ x: (el.clientWidth - STAGE_W * zoom) / 2, y: latestY(el) });
	}, [resetSignal, zoom]);

	// Follow the action. Scrolling or dragging to look back pauses following,
	// but only briefly: a new step a few seconds later brings the view back, so
	// the latest step is never left off screen during a demo.
	useEffect(() => {
		const el = boxRef.current;
		if (!el) return;
		if (!follow.current && Date.now() - lastManual.current > 4000) follow.current = true;
		if (!follow.current) return;
		const y = latestY(el);
		setPan((p) => (p.y === y ? p : { ...p, y }));
	}, [latestY]);

	// The panel itself changing size (window resized, camera panel opening)
	// must not leave the newest step below the fold either.
	useEffect(() => {
		const el = boxRef.current;
		if (!el) return;
		const observer = new ResizeObserver(() => {
			setPan((p) => ({
				x: (el.clientWidth - STAGE_W * zoom) / 2,
				y: follow.current ? latestY(el) : p.y,
			}));
		});
		observer.observe(el);
		return () => observer.disconnect();
	}, [latestY, zoom]);

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
			lastManual.current = Date.now();
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
				lastManual.current = Date.now();
				drag.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
				setDragging(true);
			}}
			onMouseMove={(e) => {
				if (!drag.current) return;
				lastManual.current = Date.now();
				setPan({ x: e.clientX - drag.current.x, y: e.clientY - drag.current.y });
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
				style={{
					width: STAGE_W,
					height: stageH,
					transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
					transformOrigin: "0 0",
					transition: dragging ? "none" : "transform 480ms cubic-bezier(0.22, 1, 0.36, 1)",
				}}
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

/* full architecture map (overlay) — read top to bottom, in the order things
   happen: see → decide → talk → act → guard. Dashed wires are the ways back up. */
const ARCH_W = 1040;
const ARCH_H = 620;
const NODE_W = 190;
const NODE_H = 54;
const ARCH_POS: Record<AgentKey, [number, number]> = {
	shelf_watcher: [500, 70],
	dispatcher: [500, 185],
	employee: [115, 310],
	floor_agent: [365, 310],
	procurement_agent: [635, 310],
	supervisor: [885, 310],
	shop_tools: [500, 440],
	order_gate: [500, 550],
};
type ArchWire = {
	a: AgentKey;
	b: AgentKey;
	d: string;
	label: string;
	at: [number, number];
	anchor?: "start" | "middle" | "end";
	dashed?: boolean; // a way back up, not the main flow
	both?: boolean; // a conversation: arrows at both ends
};
const ARCH_WIRES: ArchWire[] = [
	{ a: "shelf_watcher", b: "dispatcher", d: "M 500 97 L 500 158", label: "count per product", at: [512, 131] },
	{ a: "dispatcher", b: "floor_agent", d: curve([470, 212], [365, 283]), label: "stock in the back room", at: [438, 232], anchor: "end" },
	{ a: "dispatcher", b: "procurement_agent", d: curve([530, 212], [635, 283]), label: "back room is empty", at: [562, 232] },
	{ a: "floor_agent", b: "employee", d: "M 270 310 L 210 310", label: "call", at: [240, 302], anchor: "middle", both: true },
	{ a: "procurement_agent", b: "supervisor", d: "M 730 310 L 790 310", label: "call", at: [760, 302], anchor: "middle", both: true },
	{ a: "floor_agent", b: "shop_tools", d: curve([365, 337], [455, 413]), label: "5 tools", at: [384, 386], anchor: "end" },
	{ a: "procurement_agent", b: "shop_tools", d: curve([635, 337], [545, 413]), label: "9 tools", at: [616, 386] },
	{ a: "shop_tools", b: "order_gate", d: "M 500 467 L 500 523", label: "every purchase", at: [512, 499] },
	{
		a: "floor_agent",
		b: "dispatcher",
		d: "M 300 283 C 300 215, 340 185, 405 185",
		label: "stock missing · hands the case back",
		at: [296, 205],
		anchor: "end",
		dashed: true,
	},
	{
		a: "order_gate",
		b: "shelf_watcher",
		d: "M 595 550 L 1000 550 Q 1016 550 1016 534 L 1016 86 Q 1016 70 1000 70 L 595 70",
		label: "stock arrives · the camera sees it and closes the case",
		at: [800, 61],
		anchor: "middle",
		dashed: true,
	},
];
const ARCH_STEPS: [number, string, string][] = [
	[70, "1 · SEE", "the camera counts the shelf"],
	[185, "2 · DECIDE", "fixed rules pick who must act"],
	[310, "3 · TALK", "an agent rings that person"],
	[440, "4 · ACT", "the agent uses its own tools"],
	[550, "5 · GUARD", "no order without a spoken yes"],
];

function Architecture({ onClose, live }: { onClose: () => void; live: { from: AgentKey; to: AgentKey | null } | null }) {
	const [selected, setSelected] = useState<AgentKey | null>(null);
	const boxRef = useRef<HTMLDivElement | null>(null);
	const [scale, setScale] = useState(1);
	useEffect(() => {
		const el = boxRef.current;
		if (!el) return;
		const fit = () => setScale(Math.min(1.15, el.clientWidth / ARCH_W, el.clientHeight / ARCH_H));
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
				<span className="text-[11px] text-slate-500">read it top to bottom · click any part to see where it runs and what it can reach</span>
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
						style={{ width: ARCH_W, height: ARCH_H, transform: `translate(-50%, -50%) scale(${scale})` }}
					>
						<svg width={ARCH_W} height={ARCH_H} className="absolute inset-0" aria-hidden>
							<defs>
								<marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
									<path d="M 0 0 L 10 5 L 0 10 z" fill="#6f8bc4" />
								</marker>
							</defs>
							{/* The only parts that run outside the store. */}
							<rect x={250} y={262} width={500} height={96} rx={16} fill="rgba(56,189,248,0.04)" stroke="rgba(56,189,248,0.35)" strokeDasharray="5 5" />
							<text x={500} y={278} textAnchor="middle" fill="#38bdf8" fontSize="8.5" fontWeight="700" letterSpacing="1.6">
								AGORA CLOUD
							</text>
							{ARCH_STEPS.map(([y, step, does]) => (
								<text key={step} x={24} y={y - NODE_H / 2 - 8} fontSize="9">
									<tspan fill="#94a3b8" fontWeight="700" letterSpacing="1.6">
										{step}
									</tspan>
									<tspan fill="#7c8aa3" dx="8">
										{does}
									</tspan>
								</text>
							))}
							{ARCH_WIRES.map((wire) => {
								const on = isLive(wire.a, wire.b);
								return (
									<g key={`${wire.a}-${wire.b}`}>
										<path
											d={wire.d}
											fill="none"
											stroke={on && live ? META[live.from].c : wire.dashed ? "#3b4f7a" : "#4a639a"}
											strokeWidth={on ? 2.5 : 1.6}
											strokeDasharray={wire.dashed && !on ? "4 5" : undefined}
											opacity={on ? 0.95 : 0.85}
											markerEnd="url(#arch-arrow)"
											markerStart={wire.both ? "url(#arch-arrow)" : undefined}
											className={on ? "pg-wire-active" : undefined}
										/>
										<text x={wire.at[0]} y={wire.at[1]} textAnchor={wire.anchor ?? "start"} fill={wire.dashed ? "#64748b" : "#8fa3c8"} fontSize="9.5" fontStyle={wire.dashed ? "italic" : undefined}>
											{wire.label}
										</text>
									</g>
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
					<aside className="pg-in w-80 shrink-0 overflow-y-auto border-l border-white/[0.07] p-4">
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
						<dl className="mt-2.5 space-y-1.5 rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
							{detail.where.map(([label, value]) => (
								<div key={label} className="flex gap-2 text-[10.5px] leading-snug">
									<dt className="w-[74px] shrink-0 font-semibold uppercase tracking-[0.06em] text-slate-500">{label}</dt>
									<dd className="min-w-0 text-slate-200">{value}</dd>
								</div>
							))}
						</dl>
						<p className="mt-3 text-[9px] font-bold tracking-[0.12em] text-slate-500">WHAT IT DOES</p>
						<ul className="mt-2 space-y-1.5">
							{detail.points.map((point) => (
								<li key={point} className="flex gap-2 text-[11.5px] leading-snug text-slate-400">
									<span className="mt-1.5 h-1 w-1 shrink-0 rounded-full" style={{ background: detail.c }} />
									{point}
								</li>
							))}
						</ul>
						{detail.tools ? (
							<div className="mt-3">
								<p className="text-[9px] font-bold tracking-[0.12em] text-slate-500">
									{selected === "shop_tools" ? "ALL TOOLS" : `TOOLS IT CAN USE · ${detail.tools.length}`}
								</p>
								<ul className="mt-1.5 space-y-1">
									{detail.tools.map(([tool, does]) => (
										<li key={tool} className="flex flex-wrap items-baseline gap-x-1.5 text-[10.5px] leading-snug">
											<code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[9.5px] text-slate-200">{tool}</code>
											<span className="text-slate-500">{does}</span>
										</li>
									))}
								</ul>
							</div>
						) : null}
						{detail.denied ? (
							<p className="mt-2 rounded-lg border border-rose-400/30 bg-rose-400/[0.06] px-2.5 py-2 text-[10.5px] leading-snug text-rose-200">
								<span className="font-bold tracking-[0.08em]">OUT OF SCOPE · </span>
								{detail.denied}
							</p>
						) : null}
						{detail.stack ? (
							<div className="mt-3">
								<p className="text-[9px] font-bold tracking-[0.12em] text-slate-500">RUNS ON</p>
								<ul className="mt-1.5 space-y-1">
									{detail.stack.map((layer) => (
										<li key={layer} className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10.5px] text-slate-300">
											{layer}
										</li>
									))}
								</ul>
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

/** A strip that says, in plain sight, what the voice runs on, and shows the
 *  real Agora session while a call is live. */
function PlatformStrip({ sessions, now }: { sessions: AgoraSession[]; now: number }) {
	const live = sessions[sessions.length - 1];
	const chip = "rounded-md border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-slate-300";
	if (!live) {
		return (
			<div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-white/[0.07] bg-[#070c18] px-4 py-1.5 text-[10.5px]">
				<span className="flex items-center gap-1.5 font-bold tracking-[0.1em] text-slate-400">
					<span className="h-1.5 w-1.5 rounded-full bg-slate-600" />
					AGORA CONVERSATIONAL AI · READY
				</span>
				<span className="text-slate-500">
					Voice agents run in Agora's cloud: Deepgram speech-to-text → GPT-4o mini → Murf Falcon, with tools called over MCP
				</span>
			</div>
		);
	}
	const seconds = Math.max(0, Math.round(now / 1000 - live.started_at));
	const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
	const agent = isAgent(live.voice_agent) ? META[live.voice_agent] : META.procurement_agent;
	return (
		<div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-white/[0.07] bg-[#06120f] px-4 py-1.5 text-[10.5px]">
			<span className="flex items-center gap-1.5 font-bold tracking-[0.1em]" style={{ color: GOOD }}>
				<span className="h-1.5 w-1.5 animate-pulse rounded-full" style={{ background: GOOD }} />
				LIVE ON AGORA CONVERSATIONAL AI
			</span>
			<span className="font-semibold" style={{ color: agent.c }}>
				{agent.label}
			</span>
			{live.source === "studio" ? (
				<span className="rounded-md border px-1.5 py-0.5 font-semibold" style={{ borderColor: `${GOOD}66`, color: GOOD }}>
					Agent Studio agent
				</span>
			) : null}
			<span className={chip}>{live.stt}</span>
			<span className="text-slate-600">→</span>
			<span className={chip}>{live.llm}</span>
			<span className="text-slate-600">→</span>
			<span className={chip}>{live.tts}</span>
			<span className="ml-auto flex items-center gap-2 font-mono text-slate-500">
				<span title="Agora agent session id">session {live.agent_id.slice(0, 10)}…</span>
				<span title="Agora RTC channel">{live.channel}</span>
				<span className="tabular-nums text-slate-300">{clock}</span>
			</span>
		</div>
	);
}

type Tabs = Record<string, Group[]>;

export function AgentPlayground({
	events,
	summarise,
	sessions = [],
}: {
	events: ShopEvent[];
	summarise: (detail: Record<string, unknown>) => string | null;
	sessions?: AgoraSession[];
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

	const userZoomed = useRef(false);
	const bumpZoom = useCallback((delta: number) => {
		userZoomed.current = true; // the viewer chose a size: stop fitting automatically
		setZoom((z) => Math.min(1.8, Math.max(0.45, +(z + delta).toFixed(2))));
	}, []);

	// Fit the 760-wide stage to the panel, and re-fit when the panel changes size
	// (a different screen, a projector, the window being resized).
	useEffect(() => {
		const el = frameRef.current;
		if (!el) return;
		const fit = () => {
			if (userZoomed.current) return;
			setZoom(Math.min(1.15, Math.max(0.45, +(el.clientWidth / (STAGE_W + 24)).toFixed(2))));
		};
		fit();
		const observer = new ResizeObserver(fit);
		observer.observe(el);
		return () => observer.disconnect();
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
							: [...groups, { id, actor, color: META[actor].c, at, title: `${heading} · RUNNING ACTIONS`, text: "Working with tools", tools: [tool], isShelf: false, isSpeech: false }];
					return { ...all, [tab]: next };
				}
				const isShelf = event.kind === "shelf";
				if (isShelf && last?.isShelf) {
					// A run of counts is one step that keeps updating, not a pile of boxes.
					return { ...all, [tab]: [...groups.slice(0, -1), { ...last, id, at, text: event.title }] };
				}
				const isSpeech = event.kind === "speech";
				return { ...all, [tab]: [...groups, { id, actor, color, at, title: isSpeech ? `${heading} · SAID` : heading, text: event.title, tools: [], isShelf, isSpeech }] };
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
	const toolPos = (gi: number, ti: number, n: number): [number, number] => [MAIN_X + (ti - (n - 1) / 2) * TOOL_GAP, tops[gi] + cardH(groups[gi]) + 46];

	const wires: Wire[] = [];
	groups.forEach((group, i) => {
		const next = groups[i + 1];
		const cardBottom: [number, number] = [MAIN_X, tops[i] + cardH(group)];
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
	// Bottom edge of the newest step, including its tool circles and result line.
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
					<button
						type="button"
						onClick={() => {
							userZoomed.current = false;
							const el = frameRef.current;
							if (el) setZoom(Math.min(1.15, Math.max(0.45, +(el.clientWidth / (STAGE_W + 24)).toFixed(2))));
							setResetSignal((s) => s + 1);
						}}
						className="rounded-md border border-white/10 px-2 py-0.5 text-[10.5px] tabular-nums hover:text-white"
						title="Fit to the panel and jump to the latest step"
					>
						{Math.round(zoom * 100)}%
					</button>
					<button type="button" aria-label="Zoom in" onClick={() => bumpZoom(0.1)} className="grid h-6 w-6 place-items-center rounded-md border border-white/10 hover:text-white">
						<Plus size={12} />
					</button>
				</span>
			</div>

			<PlatformStrip sessions={sessions} now={now} />

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
