// The cold open's motion (channel docs/gags.md; remotion/src/ColdOpen.tsx draws it, the numbers are gags.json's):
// pure functions of the time, without Remotion, so the tests run them under Node.

export type GagLoop = {periodS: number; steps: number; bobPx: number; shakePx: number; tiltDeg: number; squash: number; swell: number};

export type GagCamera = {
	pushTo: number;
	origin: string;
	backOrigin: string;
	punchTo: number;
	punchInS: number;
	creepTo: number;
	punchOrigin: string;
	flashColor: string;
	flashOpacity: number;
	flashS: number;
};

export type GagTiming = {durationS: number; caughtS: number; holdFromS: number};

// The cold open's length in frames: the reel starts on the next one. reelline/media/gags.py keeps durationS
// whole frames; anything else fails the render.
export const gagFrames = (durationS: number, fps: number): number => {
	const n = Math.round(durationS * fps);
	if (n < 1 || Math.abs(durationS * fps - n) > 1e-6) {
		throw new Error(`The cold open's ${durationS}s isn't a whole number of frames at ${fps} fps`);
	}
	return n;
};

// The loop's state at t: [side (-1 for A .. 1 for B), lift (0 for A .. 1 for B)]. Two drawings
// (steps 2) swap every periodS / 2; steps 0 moves between them on a sine.
const loopPhase = (loop: GagLoop, t: number): [number, number] => {
	if (loop.steps === 2) {
		return Math.floor(t / (loop.periodS / 2)) % 2 === 1 ? [1, 1] : [-1, 0];
	}
	const a = (2 * Math.PI * t) / loop.periodS;
	return [Math.sin(a), (1 - Math.cos(a)) / 2];
};

export type LoopPose = {x: number; y: number; rotateDeg: number; scaleX: number; scaleY: number};

// The action pose at t, about its bottom middle: A shaken right and tilted left, B shaken left,
// tilted right, up bobPx, squashed (a negative squash stretches; the width goes the other way, half
// as much) and swollen.
export const loopPose = (loop: GagLoop, t: number): LoopPose => {
	const [side, lift] = loopPhase(loop, t);
	const swell = 1 + loop.swell * lift;
	return {
		x: -loop.shakePx * side,
		y: -loop.bobPx * lift,
		rotateDeg: loop.tiltDeg * side,
		scaleX: (1 + (loop.squash * lift) / 2) * swell,
		scaleY: (1 - loop.squash * lift) * swell,
	};
};

const clamp01 = (v: number): number => Math.max(0, Math.min(1, v));
const easeInOut = (p: number): number => 0.5 - 0.5 * Math.cos(Math.PI * p);
// Past 1 by about 10% on the way, then back to 1.
const easeOutBack = (p: number): number => {
	const k = 1.70158;
	return 1 + (k + 1) * (p - 1) ** 3 + k * (p - 1) ** 2;
};

// The camera at t: the action beat's push-in (about origin, backOrigin from behind), then the caught
// pose's punch onto his face and its creep until holdFromS, then still.
export const gagCamera = (cam: GagCamera, timing: GagTiming, t: number, view: 'front' | 'back'): {scale: number; origin: string} => {
	if (t < timing.caughtS) {
		return {scale: 1 + (cam.pushTo - 1) * easeInOut(clamp01(t / timing.caughtS)), origin: view === 'back' ? cam.backOrigin : cam.origin};
	}
	const dt = t - timing.caughtS;
	const punch = 1 + (cam.punchTo - 1) * easeOutBack(clamp01(dt / cam.punchInS));
	const creep = clamp01((dt - cam.punchInS) / Math.max(1e-6, timing.holdFromS - timing.caughtS - cam.punchInS));
	return {scale: punch + (cam.creepTo - cam.punchTo) * creep, origin: cam.punchOrigin};
};

// The white flash on the cut to the caught pose: flashOpacity, gone over flashS.
export const gagFlash = (cam: GagCamera, timing: GagTiming, t: number): number => {
	const dt = t - timing.caughtS;
	return dt < 0 || dt >= cam.flashS ? 0 : cam.flashOpacity * (1 - dt / cam.flashS);
};
