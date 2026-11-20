"use client";

import {
	Bot,
	Boxes,
	Minus,
	PackageCheck,
	PhoneCall,
	Plus,
	RotateCcw,
	ScanLine,
	Wrench,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AgentPlayground } from "@/components/AgentPlayground";
import { CameraPanel } from "@/components/CameraPanel";
import {
	type ShelfProduct,
	type ShopCall,
	type ShopEvent,
	type ShopState,
	getShopEvents,
	getShopState,
	resetDemo,
	stepShelfCount,
} from "@/services/shop";

const POLL_MS = 1000;
const rupees = (n: number) => `₹${Math.round(n).toLocaleString("en-IN")}`;
const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function Dashboard() {
	const [state, setState] = useState<ShopState | null>(null);
	const [events, setEvents] = useState<ShopEvent[]>([]);
	const [offline, setOffline] = useState(false);
	const [showLog, setShowLog] = useState(false);
	const lastEventId = useRef(0);

	const refreshing = useRef(false);

	const refresh = useCallback(async () => {
		if (refreshing.current) return; // never run two polls at once
		refreshing.current = true;
		try {
			const [nextState, fresh] = await Promise.all([
				getShopState(),
				getShopEvents(lastEventId.current),
			]);
			setState(nextState);
			if (nextState.last_event_id < lastEventId.current) {
				// The demo was reset from another page or phone: start our trail again.
				lastEventId.current = 0;
				setEvents([]);
				return;
			}
			const unseen = fresh.filter((event) => event.id > lastEventId.current);
			if (unseen.length) {
				lastEventId.current = unseen[unseen.length - 1].id;
				setEvents((prev) => [...prev, ...unseen].slice(-200));
			}
			setOffline(false);
		} catch {
			setOffline(true);
		} finally {
			refreshing.current = false;
		}
	}, []);

	useEffect(() => {
		void refresh();
		const timer = setInterval(refresh, POLL_MS);
		return () => clearInterval(timer);
	}, [refresh]);

	const changeShelf = async (product: ShelfProduct, step: number) => {
		await stepShelfCount(product.product, step).catch(() => {});
		void refresh();
	};

	const reset = async () => {
		await resetDemo().catch(() => {});
		lastEventId.current = 0;
		setEvents([]);
		void refresh();
	};

	const liveCall = state?.calls.find((c) => c.status === "ringing" || c.status === "answered");

	return (
		<div className="flex h-dvh min-h-screen flex-col bg-background text-foreground">
			<header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3">
				<div className="flex items-center gap-3">
					<span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/15 text-primary">
						<Bot className="h-5 w-5" aria-hidden />
					</span>
					<div>
						<h1 className="text-base font-semibold leading-tight">Asha · FreshMart stock agent</h1>
						<p className="text-xs text-muted-foreground">
							Watches the shelf, decides who to call, and reorders on approval
						</p>
					</div>
				</div>
				<div className="flex items-center gap-4 text-sm">
					{offline ? <span className="text-destructive">Backend unreachable</span> : null}
					<span className="text-muted-foreground">
						Budget left{" "}
						<strong className="text-foreground">{state ? rupees(state.budget_remaining) : "…"}</strong>
					</span>
					<a href="/phone" target="_blank" rel="noreferrer" className="text-primary underline-offset-4 hover:underline">
						Open phone page
					</a>
					<button
						type="button"
						onClick={reset}
						className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted"
					>
						<RotateCcw className="h-3.5 w-3.5" aria-hidden />
						Reset demo
					</button>
				</div>
			</header>

			<div className="grid min-h-0 flex-1 gap-4 p-4 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.7fr)]">
				<section className="flex min-h-0 flex-col gap-3 overflow-y-auto">
					<SectionTitle icon={<ScanLine className="h-4 w-4" aria-hidden />}>Shelf</SectionTitle>
					<CameraPanel />
					{state?.products.map((product) => (
						<ShelfCard key={product.product} product={product} onChange={changeShelf} />
					))}
					<SectionTitle icon={<PhoneCall className="h-4 w-4" aria-hidden />}>Calls</SectionTitle>
					<CallPanel call={liveCall} recent={state?.calls ?? []} />
					<SectionTitle icon={<PackageCheck className="h-4 w-4" aria-hidden />}>Orders</SectionTitle>
					<OrdersPanel state={state} />
				</section>

				<section className="flex min-h-0 flex-col gap-3">
					<div className="flex shrink-0 items-center justify-between">
						<SectionTitle icon={<Bot className="h-4 w-4" aria-hidden />}>Agent playground</SectionTitle>
						<button
							type="button"
							onClick={() => setShowLog((value) => !value)}
							className="text-[11px] text-muted-foreground underline-offset-4 hover:underline"
						>
							{showLog ? "Show playground" : "Show raw log"}
						</button>
					</div>
					{showLog ? <Trail events={events} /> : <AgentPlayground events={events} summarise={toolSummary} sessions={state?.agora?.sessions ?? []} />}
				</section>

			</div>
		</div>
	);
}

function SectionTitle({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
	return (
		<h2 className="flex shrink-0 items-center gap-2 text-xs font-medium uppercase tracking-widest text-muted-foreground">
			{icon}
			{children}
		</h2>
	);
}

function ShelfCard({
	product,
	onChange,
}: {
	product: ShelfProduct;
	onChange: (product: ShelfProduct, step: number) => void;
}) {
	const pct = (product.on_shelf / product.shelf_capacity) * 100;
	const thresholdPct = (product.low_threshold / product.shelf_capacity) * 100;
	const status = product.on_order
		? { label: "On order", tone: "text-sky-400 border-sky-400/40" }
		: product.refill_pending
			? { label: "Refill requested", tone: "text-amber-400 border-amber-400/40" }
			: product.is_low
				? { label: "Low", tone: "text-red-400 border-red-400/40" }
				: { label: "OK", tone: "text-emerald-400 border-emerald-400/40" };
	return (
		<article className="rounded-xl border border-border bg-card p-4">
			<div className="flex items-start justify-between gap-3">
				<div>
					<h3 className="text-sm font-medium">{title(product.product)}</h3>
					<p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
						<Boxes className="h-3.5 w-3.5" aria-hidden />
						Back room: {product.in_back_room}
					</p>
				</div>
				<span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${status.tone}`}>
					{status.label}
				</span>
			</div>
			<div className="mt-3 flex items-center gap-3">
				<StepButton label={`Remove one ${product.product}`} onClick={() => onChange(product, -1)}>
					<Minus className="h-4 w-4" aria-hidden />
				</StepButton>
				<div className="min-w-0 flex-1">
					<div className="flex items-baseline justify-between">
						<span className="text-2xl font-semibold tabular-nums">{product.on_shelf}</span>
						<span className="text-xs text-muted-foreground">
							of {product.shelf_capacity} · low at {product.low_threshold}
						</span>
					</div>
					<div className="relative mt-1.5 h-2 rounded-full bg-muted">
						<div
							className={`h-full rounded-full transition-all ${product.is_low ? "bg-red-500" : "bg-emerald-500"}`}
							style={{ width: `${pct}%` }}
						/>
						<span
							className="absolute top-[-3px] h-[14px] w-px bg-foreground/60"
							style={{ left: `${thresholdPct}%` }}
							aria-hidden
						/>
					</div>
				</div>
				<StepButton label={`Add one ${product.product}`} onClick={() => onChange(product, 1)}>
					<Plus className="h-4 w-4" aria-hidden />
				</StepButton>
			</div>
		</article>
	);
}

function StepButton({
	label,
	onClick,
	children,
}: {
	label: string;
	onClick: () => void;
	children: React.ReactNode;
}) {
	return (
		<button
			type="button"
			aria-label={label}
			title={label}
			onClick={onClick}
			className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border hover:bg-muted"
		>
			{children}
		</button>
	);
}

const KIND_STYLE: Record<ShopEvent["kind"], { label: string; dot: string }> = {
	shelf: { label: "Shelf", dot: "bg-slate-400" },
	decision: { label: "Decision", dot: "bg-violet-400" },
	call: { label: "Call", dot: "bg-sky-400" },
	tool: { label: "Tool", dot: "bg-amber-400" },
	stock: { label: "Stock", dot: "bg-orange-400" },
	system: { label: "System", dot: "bg-slate-500" },
	speech: { label: "Said", dot: "bg-teal-400" },
};

/** One line of plain English for what a tool call returned. */
function toolSummary(detail: Record<string, unknown>): string | null {
	const result = (detail.result ?? {}) as Record<string, unknown>;
	if (result.ok === false) return `Problem: ${String(result.message ?? result.error)}`;
	switch (detail.tool) {
		case "get_shelf_status":
			return result.product
				? `${result.on_shelf} on shelf, ${result.in_back_room} in back room`
				: "All products";
		case "get_sales_rate":
			return `${result.sold_per_day} sold per day · suggest ordering ${result.suggested_order_quantity}`;
		case "get_supplier_quotes": {
			const quotes = (result.quotes ?? []) as Record<string, unknown>[];
			return quotes
				.map((q) => `${q.supplier}: ${q.problem ?? `${rupees(Number(q.total_for_quantity))}, ${q.delivery_hours}h`}`)
				.join(" · ");
		}
		case "get_budget":
			return `${rupees(Number(result.budget_remaining))} left`;
		case "propose_order":
			return `${result.quantity} × ${result.product} from ${result.supplier} = ${rupees(Number(result.total))}. Waiting for approval.`;
		case "confirm_order":
			return `Order #${result.order_number} placed · ${rupees(Number(result.total))} · arrives in ${result.delivery_hours}h · approved with “${result.approved_with}”`;
		case "get_order_status":
			return `${result.count} order(s)`;
		case "schedule_callback":
			return `Will ring the ${result.will_call ?? "supervisor"} back in ${result.in_minutes} min`;
		case "get_refill_tasks":
			return `${result.count} open task(s)`;
		case "complete_refill_task":
			return `${result.moved_to_shelf} moved to shelf · now ${result.on_shelf_now} on shelf`;
		case "report_backroom_shortage":
			return result.escalated_to_supervisor
				? "Back room is empty. Supervisor is being called."
				: `Back room corrected to ${result.in_back_room_now}`;
		default:
			return null;
	}
}

function Trail({ events }: { events: ShopEvent[] }) {
	const endRef = useRef<HTMLDivElement | null>(null);
	const count = events.length;
	// biome-ignore lint/correctness/useExhaustiveDependencies: scroll when a new event arrives
	useEffect(() => {
		endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
	}, [count]);

	if (!events.length) {
		return (
			<div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
				Nothing yet. Lower a shelf count to below its threshold and watch what the agent does.
			</div>
		);
	}
	return (
		<ol className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-xl border border-border bg-card p-3">
			{events.map((event) => {
				const style = KIND_STYLE[event.kind] ?? KIND_STYLE.system;
				const summary = event.kind === "tool" ? toolSummary(event.detail) : null;
				const failed = event.kind === "tool" && (event.detail.result as { ok?: boolean })?.ok === false;
				return (
					<li key={event.id} className="flex gap-3 rounded-lg px-2 py-1.5">
						<span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${failed ? "bg-red-500" : style.dot}`} aria-hidden />
						<div className="min-w-0 flex-1">
							<p className="flex items-center gap-2 text-sm">
								{event.kind === "tool" ? <Wrench className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden /> : null}
								<span className="font-medium">{event.title}</span>
								<span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">
									{new Date(event.ts * 1000).toLocaleTimeString("en-IN", { hour12: false })}
								</span>
							</p>
							{summary ? (
								<p className={`mt-0.5 text-xs ${failed ? "text-red-400" : "text-muted-foreground"}`}>{summary}</p>
							) : null}
						</div>
					</li>
				);
			})}
			<div ref={endRef} />
		</ol>
	);
}

const CALL_STATUS: Record<ShopCall["status"], string> = {
	ringing: "Ringing…",
	answered: "On the call",
	ended: "Ended",
	declined: "Declined",
	missed: "Missed",
};

function CallPanel({ call, recent }: { call?: ShopCall; recent: ShopCall[] }) {
	return (
		<div className="rounded-xl border border-border bg-card p-4">
			{call ? (
				<div className="flex items-center gap-3">
					<span
						className={`flex h-10 w-10 items-center justify-center rounded-full bg-sky-500/15 text-sky-400 ${
							call.status === "ringing" ? "animate-pulse" : ""
						}`}
					>
						<PhoneCall className="h-5 w-5" aria-hidden />
					</span>
					<div className="min-w-0">
						<p className="text-sm font-medium">
							{title(call.role)} · {CALL_STATUS[call.status]}
						</p>
						<p className="truncate text-xs text-muted-foreground">{call.reason}</p>
					</div>
				</div>
			) : (
				<p className="text-sm text-muted-foreground">No call in progress.</p>
			)}
			{recent.filter((c) => c !== call).length ? (
				<ul className="mt-3 space-y-1 border-t border-border pt-3 text-xs text-muted-foreground">
					{recent
						.filter((c) => c !== call)
						.slice(0, 4)
						.map((c) => (
							<li key={c.call_id} className="flex justify-between gap-2">
								<span className="truncate">
									{title(c.role)}: {c.product ?? "call"}
								</span>
								<span className="shrink-0">{CALL_STATUS[c.status]}</span>
							</li>
						))}
				</ul>
			) : null}
		</div>
	);
}

function OrdersPanel({ state }: { state: ShopState | null }) {
	const proposals = state?.proposals ?? [];
	const orders = state?.orders ?? [];
	if (!proposals.length && !orders.length) {
		return (
			<p className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">No orders yet.</p>
		);
	}
	return (
		<ul className="space-y-2">
			{proposals.map((p) => (
				<li key={`p-${p.id}`} className="rounded-xl border border-amber-400/40 bg-card p-4">
					<p className="text-[11px] font-medium uppercase tracking-wide text-amber-400">Waiting for spoken approval</p>
					<p className="mt-1 text-sm font-medium">
						{p.quantity} × {p.product}
					</p>
					<p className="text-xs text-muted-foreground">
						{p.supplier} · {rupees(p.total)}
					</p>
				</li>
			))}
			{orders.map((o) => (
				<li key={`o-${o.order_number}`} className="rounded-xl border border-emerald-400/40 bg-card p-4">
					<p className="text-[11px] font-medium uppercase tracking-wide text-emerald-400">
						Order #{o.order_number} · {o.status}
					</p>
					<p className="mt-1 text-sm font-medium">
						{o.quantity} × {o.product}
					</p>
					<p className="text-xs text-muted-foreground">
						{o.supplier} · {rupees(o.total)} · arrives in {o.hours_until_delivery}h
					</p>
				</li>
			))}
		</ul>
	);
}
