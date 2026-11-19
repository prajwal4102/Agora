/**
 * A phone ringtone made with Web Audio, so there is no sound file to ship.
 * Browsers only allow audio after a tap, so `unlock()` must be called from a
 * click handler (choosing a role does this) before a call can ring out loud.
 */
export class Ringtone {
	private ctx: AudioContext | null = null;
	private timer: ReturnType<typeof setInterval> | null = null;

	unlock(): void {
		if (typeof window === "undefined") return;
		if (!this.ctx) {
			const Ctor =
				window.AudioContext ??
				(window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
			if (!Ctor) return;
			this.ctx = new Ctor();
		}
		void this.ctx.resume();
	}

	private burst(): void {
		const ctx = this.ctx;
		if (!ctx) return;
		// Two short rings per cycle, each a pair of tones like a desk phone.
		for (const offset of [0, 0.45]) {
			for (const freq of [440, 480]) {
				const osc = ctx.createOscillator();
				const gain = ctx.createGain();
				osc.frequency.value = freq;
				const start = ctx.currentTime + offset;
				gain.gain.setValueAtTime(0, start);
				gain.gain.linearRampToValueAtTime(0.18, start + 0.03);
				gain.gain.setValueAtTime(0.18, start + 0.3);
				gain.gain.linearRampToValueAtTime(0, start + 0.36);
				osc.connect(gain).connect(ctx.destination);
				osc.start(start);
				osc.stop(start + 0.4);
			}
		}
		navigator.vibrate?.([300, 150, 300]);
	}

	start(): void {
		if (this.timer) return;
		this.unlock();
		this.burst();
		this.timer = setInterval(() => this.burst(), 2200);
	}

	stop(): void {
		if (this.timer) clearInterval(this.timer);
		this.timer = null;
		navigator.vibrate?.(0);
	}
}
