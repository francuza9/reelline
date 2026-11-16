import {Easing, interpolate} from 'remotion';
import motionSpec from '@channel/graphics/motion.json';
import {Glance} from './rig';
import {MotionProps} from './types';

// The motion pass (reelline/media/motion.py plans it, the `motion` prop): punchline push-ins, reaction
// poses, glances up at the TV, lean-ins, squash-and-stretch bounces and eye-stalk reactions,
// plus the TV switching on at frame 0 (the camera's shots: camera.ts). Every number is
// motion.json's; the moments come planned and seeded, so nothing here is random.
export const MOTION = motionSpec;

export type ReactionPose = {
	mouth: string;
	lid: number;
	pupil: number[];
	pupilTo: number[] | null;
	leanDeg: number;
	stalkStretch: number;
	wobbleDeg: number;
	wobbleHz: number;
	pop: number;
	popHz: number;
};

export const NO_MOTION: MotionProps = {zooms: [], reactions: [], glances: [], shots: [], leans: [], stalks: [], bounces: []};

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const smooth = Easing.inOut(Easing.cubic);

// --- punchline zoom --------------------------------------------------------------------

// The scene's extra scale at t (1 outside every zoom).
export const zoomScale = (zooms: MotionProps['zooms'], t: number): number => {
	const z = zooms.find((z) => t > z.start && t < z.end);
	if (!z) {
		return 1;
	}
	if (t <= z.peak) {
		return interpolate(t, [z.start, z.peak], [1, z.scale], {...clamp, easing: Easing.out(Easing.cubic)});
	}
	if (t <= z.holdEnd) {
		return z.scale;
	}
	return interpolate(t, [z.holdEnd, z.end], [z.scale, 1], {...clamp, easing: smooth});
};

// --- reaction poses --------------------------------------------------------------------

export type Reaction = {pose: ReactionPose; weight: number; progress: number; t: number};

// The reaction at t: its pose, how far it's in (0..1, eased in and out) and its progress
// (0..1 over its length, for the eye-roll's sweep); null outside every reaction.
export const reactionAt = (reactions: MotionProps['reactions'], t: number): Reaction | null => {
	const r = reactions.find((r) => t > r.start && t < r.end);
	if (!r) {
		return null;
	}
	const pose = (MOTION.reaction.poses as Record<string, ReactionPose>)[r.pose];
	if (!pose) {
		throw new Error(`Unknown reaction pose "${r.pose}"; expected one of: ${Object.keys(MOTION.reaction.poses).join(', ')}`);
	}
	const {inS, outS} = MOTION.reaction;
	const weight = Math.min(
		interpolate(t, [r.start, r.start + inS], [0, 1], {...clamp, easing: smooth}),
		interpolate(t, [r.end - outS, r.end], [1, 0], {...clamp, easing: smooth}),
	);
	return {pose, weight, progress: (t - r.start) / (r.end - r.start), t: t - r.start};
};

// The pose's pupil target, as a fraction of the eye white's radius.
export const reactionPupil = (r: Reaction): [number, number] => {
	const [x0, y0] = r.pose.pupil;
	if (!r.pose.pupilTo) {
		return [x0, y0];
	}
	// The eye-roll: from pupil to pupilTo over the middle of the reaction, through the top.
	const p = interpolate(r.progress, [0.15, 0.85], [0, 1], {...clamp, easing: smooth});
	const [x1, y1] = r.pose.pupilTo;
	const arc = Math.sin(Math.PI * p) * 0.35;
	return [x0 + (x1 - x0) * p, y0 + (y1 - y0) * p - arc];
};

// The stalks' shear added by the pose (degrees; the wobble alternates between the stalks).
export const reactionLean = (r: Reaction, side: 'left' | 'right'): number => {
	const wobble = r.pose.wobbleDeg * Math.sin(2 * Math.PI * r.pose.wobbleHz * r.t) * (side === 'left' ? 1 : -1);
	return r.weight * (r.pose.leanDeg + wobble);
};

// The body's giggle: a vertical stretch factor.
export const reactionPop = (r: Reaction): number =>
	1 + r.weight * r.pose.pop * Math.abs(Math.sin(2 * Math.PI * r.pose.popHz * r.t));

// --- glances up at the TV ---------------------------------------------------------------

// 0 at the camera, 1 at the TV.
export const tvGlanceAt = (glances: MotionProps['glances'], t: number): number => {
	const g = glances.find((g) => t > g.start && t < g.end);
	if (!g) {
		return 0;
	}
	const {dartS, returnS} = MOTION.tvGlance;
	return Math.min(
		interpolate(t, [g.start, g.start + dartS], [0, 1], {...clamp, easing: smooth}),
		interpolate(t, [g.end - returnS, g.end], [1, 0], {...clamp, easing: smooth}),
	);
};

// The rig's random glances (rig.ts glanceSchedule) that don't meet a planned glance, a
// reaction or a stalk reaction: the eyes have somewhere to be then.
export const freeGlances = (glances: Glance[], motion: MotionProps, settleS: number): Glance[] => {
	const busy = [...motion.glances, ...motion.reactions, ...(motion.stalks ?? [])];
	return glances.filter((g) => !busy.some((b) => g.start < b.end && g.holdEnd + settleS > b.start));
};

// --- snappy in, held, eased out ----------------------------------------------------------

// 0..1 over a move's way in, overshooting by about `overshoot` near its end (no float: a
// cubic ease-out with a bump that is gone at 1).
const snapIn = (p: number, overshoot: number): number => {
	const q = Math.min(1, Math.max(0, p));
	return (1 - Math.pow(1 - q, 3)) * (1 + overshoot * Math.sin(Math.PI * q));
};

// --- lean-in on emphasis -------------------------------------------------------------------

// How far into a lean-in the host is at t: 0 outside every one, 1 held (a little over on the way in).
export const leanAt = (leans: MotionProps['leans'], t: number): number => {
	const l = (leans ?? []).find((l) => t > l.start && t < l.end);
	if (!l) {
		return 0;
	}
	if (t <= l.peak) {
		return snapIn((t - l.start) / Math.max(l.peak - l.start, 1e-3), MOTION.lean.overshoot);
	}
	if (t <= l.holdEnd) {
		return 1;
	}
	return interpolate(t, [l.holdEnd, l.end], [1, 0], {...clamp, easing: smooth});
};

// The lean's pose: the body wider and lower (from the seat), the stalks foreshortened, the eyes bigger.
export const leanPose = (k: number) => {
	const L = MOTION.lean;
	return {
		bodyScaleX: 1 + k * L.scaleX,
		bodyScaleY: 1 + k * L.scaleY,
		stalkStretch: 1 + k * L.stalkStretch,
		eyeScale: 1 + k * L.eyeGrow,
	};
};

// --- squash-and-stretch on punchlines ------------------------------------------------------

// The body's extra height at t (negative: squashed): squash first, then a stretch, dying out.
export const bounceAt = (bounces: MotionProps['bounces'], t: number): number => {
	const B = MOTION.bounce;
	if (!B.enabled) {
		return 0;
	}
	const b = (bounces ?? []).find((b) => t >= b.at && t < b.at + B.durationS);
	if (!b) {
		return 0;
	}
	const dt = t - b.at;
	return -B.amount * Math.exp(-dt / B.tauS) * Math.sin(2 * Math.PI * B.hz * dt);
};

// --- eye-stalk reactions -------------------------------------------------------------------

export type StalkPose = {
	stretch: number;
	eyeGrow: number;
	pupilScale: number;
	lid: number;
	leanDeg: number;
	pupil: number[];
	cross: number;
};

export type StalkReaction = {pose: StalkPose; weight: number};

// The stalk reaction at t: its pose and weight (0..1, a little over on the way in); null outside.
export const stalksAt = (stalks: MotionProps['stalks'], t: number): StalkReaction | null => {
	const s = (stalks ?? []).find((s) => t > s.start && t < s.end);
	if (!s) {
		return null;
	}
	const pose = (MOTION.stalks.poses as Record<string, StalkPose>)[s.pose];
	if (!pose) {
		throw new Error(`Unknown stalk pose "${s.pose}"; expected one of: ${Object.keys(MOTION.stalks.poses).join(', ')}`);
	}
	const {inS, outS, overshoot} = MOTION.stalks;
	const weight =
		t < s.start + inS
			? snapIn((t - s.start) / inS, overshoot)
			: interpolate(t, [s.end - outS, s.end], [1, 0], {...clamp, easing: smooth});
	return {pose, weight};
};

// The stalks' shear the pose adds (degrees; positive leanDeg spreads the tops apart: the
// rig's positive angle leans a top left).
export const stalkLean = (s: StalkReaction, side: 'left' | 'right'): number => s.weight * s.pose.leanDeg * (side === 'left' ? 1 : -1);

// --- the TV switching on ----------------------------------------------------------------

// The white wash over the TV's face at t (0 once it's on).
export const powerOnWash = (t: number): number => {
	const p = MOTION.tvPowerOn;
	if (!p.enabled) {
		return 0;
	}
	return interpolate(t, [0, p.durationS], [p.from, 0], {...clamp, easing: Easing.out(Easing.quad)});
};
