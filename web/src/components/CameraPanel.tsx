"use client";

import { Camera, CameraOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { type VisionResult, resetVision, sendVisionFrame } from "@/services/shop";

const FRAME_WIDTH = 640; // frames are shrunk to this before upload
const PAUSE_MS = 250; // rest between one answer and the next frame
const COLOURS = ["#38bdf8", "#f59e0b", "#34d399", "#f472b6", "#a78bfa", "#fb7185"];

const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * The shelf camera. Sends webcam frames to the backend detector, draws what it
 * found over the video, and shows each product's count. Counts that hold
 * steady become the shelf's count on the server, which may start a call.
 */
export function CameraPanel() {
	const videoRef = useRef<HTMLVideoElement | null>(null);
	const overlayRef = useRef<HTMLCanvasElement | null>(null);
	const grabRef = useRef<HTMLCanvasElement | null>(null);
	const streamRef = useRef<MediaStream | null>(null);
	const runningRef = useRef(false);

	const [running, setRunning] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [result, setResult] = useState<VisionResult | null>(null);
	const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
	const [deviceId, setDeviceId] = useState<string>("");

	const colourFor = useCallback((product: string, known: string[]) => {
		const index = Math.max(0, known.indexOf(product));
		return COLOURS[index % COLOURS.length];
	}, []);

	const draw = useCallback(
		(seen: VisionResult) => {
			const canvas = overlayRef.current;
			const video = videoRef.current;
			if (!canvas || !video) return;
			canvas.width = video.clientWidth;
			canvas.height = video.clientHeight;
			const ctx = canvas.getContext("2d");
			if (!ctx) return;
			ctx.clearRect(0, 0, canvas.width, canvas.height);
			const known = Object.keys(seen.frame_counts);
			ctx.lineWidth = 2;
			ctx.font = "12px ui-sans-serif, system-ui, sans-serif";
			for (const item of seen.boxes) {
				const [x1, y1, x2, y2] = item.box;
				const x = x1 * canvas.width;
				const y = y1 * canvas.height;
				const w = (x2 - x1) * canvas.width;
				const h = (y2 - y1) * canvas.height;
				const colour = colourFor(item.product, known);
				ctx.strokeStyle = colour;
				ctx.strokeRect(x, y, w, h);
				const label = `${item.product} ${Math.round(item.confidence * 100)}%`;
				const labelWidth = ctx.measureText(label).width + 8;
				ctx.fillStyle = colour;
				ctx.fillRect(x, Math.max(0, y - 16), labelWidth, 16);
				ctx.fillStyle = "#0b1220";
				ctx.fillText(label, x + 4, Math.max(11, y - 4));
			}
		},
		[colourFor],
	);

	const loop = useCallback(async () => {
		const video = videoRef.current;
		if (!runningRef.current || !video) return;
		if (video.videoWidth) {
			const grab = grabRef.current ?? document.createElement("canvas");
			grabRef.current = grab;
			grab.width = FRAME_WIDTH;
			grab.height = Math.round((video.videoHeight / video.videoWidth) * FRAME_WIDTH);
			grab.getContext("2d")?.drawImage(video, 0, 0, grab.width, grab.height);
			const blob = await new Promise<Blob | null>((resolve) =>
				grab.toBlob(resolve, "image/jpeg", 0.75),
			);
			if (blob && runningRef.current) {
				try {
					const seen = await sendVisionFrame(blob);
					if (runningRef.current) {
						setResult(seen);
						draw(seen);
						setError(null);
					}
				} catch (err) {
					setError(err instanceof Error ? err.message : "The detector did not answer.");
				}
			}
		}
		if (runningRef.current) setTimeout(loop, PAUSE_MS);
	}, [draw]);

	const stop = useCallback(() => {
		runningRef.current = false;
		setRunning(false);
		for (const track of streamRef.current?.getTracks() ?? []) track.stop();
		streamRef.current = null;
		const canvas = overlayRef.current;
		canvas?.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
	}, []);

	const start = useCallback(
		async (chosenDevice?: string) => {
			setError(null);
			try {
				for (const track of streamRef.current?.getTracks() ?? []) track.stop();
				const stream = await navigator.mediaDevices.getUserMedia({
					audio: false,
					video: chosenDevice
						? { deviceId: { exact: chosenDevice }, width: { ideal: 1280 } }
						: { width: { ideal: 1280 }, facingMode: "environment" },
				});
				streamRef.current = stream;
				const video = videoRef.current;
				if (video) {
					video.srcObject = stream;
					await video.play();
				}
				const all = await navigator.mediaDevices.enumerateDevices();
				setDevices(all.filter((d) => d.kind === "videoinput"));
				setDeviceId(stream.getVideoTracks()[0]?.getSettings().deviceId ?? "");
				await resetVision().catch(() => {});
				if (!runningRef.current) {
					runningRef.current = true;
					setRunning(true);
					void loop();
				}
			} catch (err) {
				stop();
				setError(
					err instanceof Error && err.name === "NotAllowedError"
						? "Camera permission was refused. Allow the camera for this page and try again."
						: "Could not open the camera.",
				);
			}
		},
		[loop, stop],
	);

	useEffect(() => stop, [stop]);

	const known = result ? Object.keys(result.frame_counts) : [];
	const tracked = result?.tracked ?? [];

	return (
		<article className="rounded-xl border border-border bg-card p-3">
			<div className="flex items-center justify-between gap-2">
				<h3 className="flex items-center gap-2 text-sm font-medium">
					<Camera className="h-4 w-4" aria-hidden />
					Shelf camera
				</h3>
				<div className="flex items-center gap-2">
					{running && devices.length > 1 ? (
						<select
							aria-label="Camera"
							value={deviceId}
							onChange={(e) => void start(e.target.value)}
							className="max-w-[10rem] rounded-md border border-border bg-background px-2 py-1 text-xs"
						>
							{devices.map((d, i) => (
								<option key={d.deviceId} value={d.deviceId}>
									{d.label || `Camera ${i + 1}`}
								</option>
							))}
						</select>
					) : null}
					<button
						type="button"
						onClick={() => (running ? stop() : void start())}
						className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted"
					>
						{running ? <CameraOff className="h-3.5 w-3.5" aria-hidden /> : <Camera className="h-3.5 w-3.5" aria-hidden />}
						{running ? "Stop" : "Start camera"}
					</button>
				</div>
			</div>

			<div className={`relative mt-3 overflow-hidden rounded-lg bg-black ${running ? "" : "hidden"}`}>
				{/* biome-ignore lint/a11y/useMediaCaption: live camera preview, no speech */}
				<video ref={videoRef} playsInline muted className="block w-full" />
				<canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" />
			</div>

			{error ? <p className="mt-2 text-xs text-red-400">{error}</p> : null}

			{running ? (
				<ul className="mt-3 flex flex-wrap gap-2">
					{tracked.length === 0 ? (
						<li className="text-xs text-muted-foreground">
							Looking for products… put them in view of the camera.
						</li>
					) : null}
					{tracked.map((product) => {
						const now = result?.frame_counts[product] ?? 0;
						const steady = result?.stable_counts[product];
						return (
							<li
								key={product}
								className="flex items-center gap-2 rounded-full border border-border px-2.5 py-1 text-xs"
							>
								<span
									className="h-2 w-2 rounded-full"
									style={{ background: colourFor(product, known) }}
									aria-hidden
								/>
								{title(product)}
								<strong className="tabular-nums">{steady ?? "…"}</strong>
								{steady !== undefined && now !== steady ? (
									<span className="text-muted-foreground">(seeing {now})</span>
								) : null}
							</li>
						);
					})}
				</ul>
			) : (
				<p className="mt-2 text-xs text-muted-foreground">
					Start the camera to count products automatically. The buttons below still work without it.
				</p>
			)}
		</article>
	);
}
