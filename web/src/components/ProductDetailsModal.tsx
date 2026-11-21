"use client";

import { CalendarClock, Check, Truck, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { getProductDetails, type ProductDetails } from "@/services/shop";

const rupees = (value: number) => `₹${Math.round(value).toLocaleString("en-IN")}`;
const title = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const plural = (count: number, unit: string) => `${count} ${unit}${count === 1 ? "" : "s"}`;

/**
 * Everything the store knows about one product, in a pop-up over the
 * dashboard: stock on the shelf and in the back room, how fast it sells, the
 * demand forecast, what each supplier would charge, and what is in motion.
 * It reloads whenever something new happens in the shop.
 */
export function ProductDetailsModal({
	product,
	refreshKey,
	onClose,
}: {
	product: string;
	/** Changes whenever the shop logs a new event, so open details stay current. */
	refreshKey: number;
	onClose: () => void;
}) {
	const [details, setDetails] = useState<ProductDetails | null>(null);
	const [failed, setFailed] = useState(false);

	// biome-ignore lint/correctness/useExhaustiveDependencies: refreshKey is the reload trigger
	useEffect(() => {
		let live = true;
		getProductDetails(product)
			.then((data) => {
				if (!live) return;
				setDetails(data);
				setFailed(false);
			})
			.catch(() => live && setFailed(true));
		return () => {
			live = false;
		};
	}, [product, refreshKey]);

	useEffect(() => {
		const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [onClose]);

	const d = details;
	const forecast = d?.forecast;
	const status = !d
		? null
		: !d.enabled
			? { label: "Agent off", tone: "text-muted-foreground border-border" }
			: d.orders.some((o) => o.status === "placed")
				? { label: "On order", tone: "text-sky-400 border-sky-400/40" }
				: d.refill_task
					? { label: "Refill requested", tone: "text-amber-400 border-amber-400/40" }
					: d.is_low
						? { label: "Low", tone: "text-red-400 border-red-400/40" }
						: { label: "OK", tone: "text-emerald-400 border-emerald-400/40" };

	return (
		<div
			className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
			onMouseDown={(e) => e.target === e.currentTarget && onClose()}
			role="presentation"
		>
			<div
				role="dialog"
				aria-modal="true"
				aria-label={`${title(product)} details`}
				className="flex max-h-[90dvh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
			>
				<header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3.5">
					<div className="min-w-0 flex-1">
						<h2 className="truncate text-lg font-semibold">{title(product)}</h2>
						<p className="text-xs text-muted-foreground">
							{d ? `Sold by the ${d.unit} · ordered in packs of ${d.pack_size}` : "Loading…"}
						</p>
					</div>
					{status ? (
						<span className={`shrink-0 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${status.tone}`}>{status.label}</span>
					) : null}
					<button
						type="button"
						onClick={onClose}
						aria-label="Close"
						className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-border text-muted-foreground hover:bg-muted hover:text-foreground"
					>
						<X className="h-4 w-4" aria-hidden />
					</button>
				</header>

				{failed && !d ? (
					<p className="p-6 text-sm text-destructive">Could not load this product. Is the backend running?</p>
				) : !d || !forecast ? (
					<p className="p-6 text-sm text-muted-foreground">Loading…</p>
				) : (
					<div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
						<section>
							<Heading>Stock</Heading>
							<div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
								<Tile label="On the shelf" value={`${d.on_shelf}`} note={`of ${d.shelf_capacity}`} tone={d.is_low ? "text-red-400" : undefined} />
								<Tile label="Back room" value={`${d.in_back_room}`} note="warehouse stock" />
								<Tile label="Refill up to" value={`${d.refill_up_to}`} note={`80% of ${d.shelf_capacity} · room for returns`} />
								<Tile label="Low mark" value={`${d.low_threshold}`} note={`joins a call at ${d.nearly_low_mark} or below`} />
							</div>
						</section>

						<section>
							<Heading>Sales and forecast</Heading>
							<div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
								<Tile label="Normal sales" value={`${d.sold_per_day}`} note="a day" />
								<Tile
									label="Forecast"
									value={`${forecast.forecast_sold_per_day}`}
									note={forecast.expected_uplift_percent ? `a day · +${forecast.expected_uplift_percent}%` : "a day · no change"}
									tone={forecast.expected_uplift_percent ? "text-amber-400" : undefined}
								/>
								<Tile
									label="Sells out in"
									value={d.hours_until_sold_out == null ? "–" : `${d.hours_until_sold_out}h`}
									note="of trading, at normal sales"
								/>
								<Tile
									label="Order size"
									value={`${forecast.recommended_order_quantity}`}
									note={
										forecast.recommended_order_quantity !== forecast.normal_order_quantity
											? `usually ${forecast.normal_order_quantity}`
											: "recommended"
									}
									tone={forecast.expected_uplift_percent ? "text-amber-400" : undefined}
								/>
							</div>
							<p className="mt-2 flex items-start gap-2 text-xs text-muted-foreground">
								<CalendarClock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
								<span>
									{forecast.upcoming_occasion
										? `${forecast.upcoming_occasion} is ${forecast.days_until_occasion} days away. `
										: "No occasion is coming up for this product. "}
									Order size covers {forecast.basis}.
								</span>
							</p>
						</section>

						<section>
							<Heading>
								Suppliers · quoted for {forecast.recommended_order_quantity} · budget left {rupees(d.budget_remaining)}
							</Heading>
							<div className="overflow-x-auto rounded-lg border border-border">
								<table className="w-full text-left text-xs">
									<thead className="bg-muted/40 text-[10.5px] uppercase tracking-wider text-muted-foreground">
										<tr>
											<th className="px-3 py-2 font-medium">Supplier</th>
											<th className="px-3 py-2 text-right font-medium">Per {d.unit}</th>
											<th className="px-3 py-2 text-right font-medium">Total</th>
											<th className="px-3 py-2 text-right font-medium">Delivery</th>
											<th className="px-3 py-2 text-right font-medium">In stock</th>
											<th className="px-3 py-2 font-medium">Note</th>
										</tr>
									</thead>
									<tbody className="divide-y divide-border">
										{d.quotes.map((q) => {
											const best = q.supplier === d.cheapest_that_can_fill;
											const fastest = q.supplier === d.fastest_that_can_fill && !best;
											return (
												<tr key={q.supplier} className={q.can_fill_order ? "" : "text-muted-foreground"}>
													<td className="px-3 py-2 font-medium">{q.supplier}</td>
													<td className="px-3 py-2 text-right tabular-nums">{rupees(q.unit_price)}</td>
													<td className="px-3 py-2 text-right tabular-nums">{rupees(q.total_for_quantity)}</td>
													<td className="px-3 py-2 text-right tabular-nums">{q.delivery_hours}h</td>
													<td className="px-3 py-2 text-right tabular-nums">{q.available_quantity}</td>
													<td className="px-3 py-2">
														{q.problem ? (
															<span className="text-red-400">{title(q.problem)}</span>
														) : !q.within_budget ? (
															<span className="text-red-400">Over budget</span>
														) : best ? (
															<span className="inline-flex items-center gap-1 text-emerald-400">
																<Check className="h-3 w-3" aria-hidden />
																Cheapest that can fill
															</span>
														) : fastest ? (
															<span className="text-sky-400">Fastest</span>
														) : null}
													</td>
												</tr>
											);
										})}
									</tbody>
								</table>
							</div>
						</section>

						<section>
							<Heading>In motion</Heading>
							<ul className="space-y-1.5 text-xs">
								{d.orders.map((o) => (
									<li key={o.order_number} className="flex items-start gap-2">
										<Truck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-400" aria-hidden />
										<span>
											Order #{o.order_number}: {o.quantity} from {o.supplier} for {rupees(o.total)} ·{" "}
											{o.status === "placed" ? `arrives in ${o.hours_until_delivery}h` : "delivered"}
										</span>
									</li>
								))}
								{d.refill_task ? (
									<li className="text-amber-400">
										Refill requested: the employee is bringing {plural(d.refill_task.bring_quantity, d.unit)} from the back room.
									</li>
								) : null}
								{d.callback ? (
									<li className="text-muted-foreground">
										{d.callback.who === "employee" ? "Shelf check" : "Callback to the supervisor"} due in {d.callback.in_minutes} min.
									</li>
								) : null}
								{!d.enabled ? <li className="text-muted-foreground">The agent is switched off for this product: counted, never acted on.</li> : null}
								{d.orders.length === 0 && !d.refill_task && !d.callback && d.enabled ? (
									<li className="text-muted-foreground">Nothing open. The agent acts when the shelf reaches {d.low_threshold}.</li>
								) : null}
							</ul>
						</section>
					</div>
				)}
			</div>
		</div>
	);
}

function Heading({ children }: { children: ReactNode }) {
	return <h3 className="mb-2 text-[10.5px] font-medium uppercase tracking-widest text-muted-foreground">{children}</h3>;
}

function Tile({ label, value, note, tone }: { label: string; value: string; note: string; tone?: string }) {
	return (
		<div className="rounded-lg border border-border bg-background/40 px-3 py-2">
			<p className="text-[10.5px] text-muted-foreground">{label}</p>
			<p className={`text-xl font-semibold tabular-nums leading-tight ${tone ?? ""}`}>{value}</p>
			<p className="truncate text-[10.5px] text-muted-foreground">{note}</p>
		</div>
	);
}
