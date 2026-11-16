import brand from '@channel/graphics/brand.json';
import {Easing, interpolate, random} from 'remotion';
import {MouthShape, MouthCue} from './types';

// All tunable motion numbers. Pixel values are asset-canvas pixels.
export const RIG = {
	seed: `${brand.seedPrefix}-rig-v1`,
	breath: {
		// Vertical stretch from 1.0 up to 1 + amplitude and back.
		amplitude: 0.0138,
		periodS: 3,
	},
	blink: {
		minGapS: 2.5,
		maxGapS: 5,
		// Eyelid coverage (0 = open, 1 = closed) per frame of a blink.
		curve: [0.5, 1, 1, 0.5],
	},
	sway: {
		// Per stalk, picked once from these ranges (seeded).
		amplitudeDeg: [1.725, 2.875],
		periodS: [4, 6],
		// Right stalk's phase relative to the left one (radians).
		rightPhase: Math.PI,
		clampDeg: 12,
	},
	glance: {
		firstAfterS: 2,
		minGapS: 3,
		maxGapS: 6,
		dartS: 0.1,
		minHoldS: 0.3,
		maxHoldS: 0.8,
		// Return: past centre by `overshoot` of the offset in returnS, then settle in settleS.
		returnS: 0.1,
		overshoot: 0.15,
		settleS: 0.1,
		// Horizontal pupil offset as a fraction of the eye-white radius; vertical is a
		// random fraction of that.
		offsetRatio: 0.45,
		maxVerticalRatio: 0.3,
		blinkChance: 1 / 3,
	},
	jitter: {
		// Pupil drift while centred: sum of two slow sines per axis.
		amplitudePx: 1.5,
		frequencyHz: [0.4, 1.3],
	},
	bounce: {
		// Extra vertical squash at the clip's loudest talking moment.
		squash: 0.00345,
		// Loudness envelope follower time constants (rise / fall).
		attackS: 0.08,
		releaseS: 0.25,
		// Critically damped spring (natural frequency, Hz) that fades the bounce in
		// and out when talking starts and stops, instead of switching per frame.
		gateSpringHz: 4,
	},
};

// --- breathing and talking bounce --------------------------------------------------

export const breathScale = (t: number): number =>
	1 + RIG.breath.amplitude * 0.5 * (1 - Math.cos((2 * Math.PI * t) / RIG.breath.periodS));

// Attack/release envelope follower over per-frame loudness (0..1).
export const smoothEnvelope = (loudness: number[], fps: number): number[] => {
	const attack = Math.exp(-1 / (RIG.bounce.attackS * fps));
	const release = Math.exp(-1 / (RIG.bounce.releaseS * fps));
	const out: number[] = [];
	let env = 0;
	for (const value of loudness) {
		const k = value > env ? attack : release;
		env = k * env + (1 - k) * value;
		out.push(env);
	}
	return out;
};

// Critically damped spring following `target` per frame, starting at rest at 0.
const springFollow = (target: number[], fps: number, hz: number): number[] => {
	const w = 2 * Math.PI * hz;
	const substeps = 8;
	const dt = 1 / fps / substeps;
	const out: number[] = [];
	let x = 0;
	let v = 0;
	for (const goal of target) {
		for (let i = 0; i < substeps; i++) {
			v += (w * w * (goal - x) - 2 * w * v) * dt;
			x += v * dt;
		}
		out.push(x);
	}
	return out;
};

// Per-frame bounce amount (0..1): smoothed loudness times a spring-eased "talking"
// gate (mouth not X), scaled so the clip's peak is 1.
export const bounceCurve = (loudness: number[], cues: MouthCue[], fps: number): number[] => {
	const envelope = smoothEnvelope(loudness, fps);
	const talking = envelope.map((_, frame) => (mouthAt(cues, frame / fps) === 'X' ? 0 : 1));
	const gate = springFollow(talking, fps, RIG.bounce.gateSpringHz);
	const curve = envelope.map((env, i) => env * Math.min(Math.max(gate[i], 0), 1));
	const peak = Math.max(0, ...curve);
	return peak > 0 ? curve.map((x) => x / peak) : curve;
};

export const bounceScale = (curve: number[], frame: number): number =>
	1 - RIG.bounce.squash * (curve[Math.min(frame, curve.length - 1)] ?? 0);

// --- mouth ---------------------------------------------------------------------

export const mouthAt = (cues: MouthCue[], t: number): MouthShape => {
	for (const cue of cues) {
		if (t >= cue.start && t < cue.end) {
			return cue.value;
		}
	}
	return 'X';
};

// --- blinks --------------------------------------------------------------------

const between = (key: string, [lo, hi]: readonly number[]): number =>
	lo + random(`${RIG.seed}-${key}`) * (hi - lo);

export const blinkStarts = (durationInFrames: number, fps: number): number[] => {
	const starts: number[] = [];
	const gap = (i: number) => between(`blink-${i}`, [RIG.blink.minGapS, RIG.blink.maxGapS]);
	let t = gap(0);
	for (let i = 1; t * fps < durationInFrames; i++) {
		starts.push(Math.round(t * fps));
		t += gap(i);
	}
	return starts;
};

export const blinkAmount = (starts: number[], frame: number): number => {
	let amount = 0;
	for (const start of starts) {
		const k = frame - start;
		if (k >= 0 && k < RIG.blink.curve.length) {
			amount = Math.max(amount, RIG.blink.curve[k]);
		}
	}
	return amount;
};

// --- stalk sway ----------------------------------------------------------------

export type Side = 'left' | 'right';

export const stalkAngleDeg = (side: Side, t: number): number => {
	const amplitude = between(`sway-amp-${side}`, RIG.sway.amplitudeDeg);
	const period = between(`sway-period-${side}`, RIG.sway.periodS);
	const phase = side === 'right' ? RIG.sway.rightPhase : 0;
	const angle = amplitude * Math.sin((2 * Math.PI * t) / period + phase);
	return Math.min(Math.max(angle, -RIG.sway.clampDeg), RIG.sway.clampDeg);
};

// Horizontal shift that skewX(angle) around a pivot on row `pivotY` gives row `y`.
export const shearOffset = (angleDeg: number, y: number, pivotY: number): number =>
	Math.tan((angleDeg * Math.PI) / 180) * (y - pivotY);

// --- eye glances ---------------------------------------------------------------

export type Glance = {
	start: number; // seconds; the dart begins
	holdEnd: number; // seconds; the return begins
	dx: number; // offset as a fraction of the eye-white radius
	dy: number;
	blink: boolean; // returns with a blink
};

export const glanceSchedule = (durationS: number): Glance[] => {
	const g = RIG.glance;
	const glances: Glance[] = [];
	let t = g.firstAfterS;
	for (let i = 0; ; i++) {
		t += between(`glance-gap-${i}`, [g.minGapS, g.maxGapS]);
		if (t >= durationS) {
			break;
		}
		const hold = between(`glance-hold-${i}`, [g.minHoldS, g.maxHoldS]);
		const side = random(`${RIG.seed}-glance-side-${i}`) < 0.5 ? -1 : 1;
		glances.push({
			start: t,
			holdEnd: t + g.dartS + hold,
			dx: side * g.offsetRatio,
			dy: between(`glance-dy-${i}`, [-g.maxVerticalRatio, g.maxVerticalRatio]) * g.offsetRatio,
			blink: random(`${RIG.seed}-glance-blink-${i}`) < g.blinkChance,
		});
	}
	return glances;
};

// 0 = centred, 1 = fully at the glance target; negative = overshoot past centre.
const glanceProgress = (glance: Glance, t: number): number => {
	const g = RIG.glance;
	const dartEnd = glance.start + g.dartS;
	const returnEnd = glance.holdEnd + g.returnS;
	const settleEnd = returnEnd + g.settleS;
	if (t <= glance.start || t >= settleEnd) {
		return 0;
	}
	if (t < dartEnd) {
		return interpolate(t, [glance.start, dartEnd], [0, 1], {easing: Easing.inOut(Easing.cubic)});
	}
	if (t < glance.holdEnd) {
		return 1;
	}
	if (t < returnEnd) {
		return interpolate(t, [glance.holdEnd, returnEnd], [1, -g.overshoot], {
			easing: Easing.inOut(Easing.cubic),
		});
	}
	return interpolate(t, [returnEnd, settleEnd], [-g.overshoot, 0], {easing: Easing.inOut(Easing.sin)});
};

const jitter = (axis: 'x' | 'y', t: number): number => {
	const [f1, f2] = RIG.jitter.frequencyHz;
	const p1 = random(`${RIG.seed}-jitter-${axis}-1`) * 2 * Math.PI;
	const p2 = random(`${RIG.seed}-jitter-${axis}-2`) * 2 * Math.PI;
	return (RIG.jitter.amplitudePx / 2) * (Math.sin(2 * Math.PI * f1 * t + p1) + Math.sin(2 * Math.PI * f2 * t + p2));
};

// Pupil offset in pixels for an eye white of radius `whiteRadius`. Jitter fades out
// as a glance moves away from centre, so there's no jump at either end of a glance.
export const pupilOffset = (glances: Glance[], t: number, whiteRadius: number): [number, number] => {
	let x = 0;
	let y = 0;
	let p = 0;
	for (const glance of glances) {
		p = glanceProgress(glance, t);
		if (p !== 0) {
			x = p * glance.dx * whiteRadius;
			y = p * glance.dy * whiteRadius;
			break;
		}
	}
	const still = 1 - Math.min(1, Math.abs(p));
	return [x + still * jitter('x', t), y + still * jitter('y', t)];
};

// Blink start frames for glances that return with a blink (timing only; the lid
// animation itself is RIG.blink.curve).
export const glanceBlinkStarts = (glances: Glance[], fps: number): number[] =>
	glances.filter((g) => g.blink).map((g) => Math.round(g.holdEnd * fps));
