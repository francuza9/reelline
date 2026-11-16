import brand from '@channel/graphics/brand.json';
import {Easing, interpolate, random} from 'remotion';
import {CaptionChunk, MouthCue, MouthShape, ScreenImage} from './types';

// All tunable numbers of the reel's first seconds: frame 0 as the fullest frame, the
// push-in and the emotional burst on the first word. Pixel values are
// output pixels (1080x1920) unless noted.
export const OPENING = {
	// Paragraph 1's picture (or the first later one) on the TV from frame 0, already
	// faded in; the idle face at frame 0 only when the reel has no pictures.
	imageFromFrame0: true,
	// The first caption chunk on screen from frame 0, no pop-in, its first word lit.
	captionFromFrame0: true,
	// The mouth on the first viseme from frame 0 instead of the rest mouth.
	mouthFromFrame0: true,
	// The whole scene (background, TV, the host; not the text) eases from `from` to 1.
	pushIn: {
		enabled: true,
		from: 1.08,
		durationS: 0.5,
		// CSS transform-origin: around the TV and the host's face.
		origin: '50% 42%',
	},
	// The opener animation on the first word (t0), one per paragraph-1 style (the
	// `animation` prop: reelline/media/script_markup.py opener_animation()). All of it settles
	// within ~0.4 s of t0. The shared timings are here, the poses in `animations`.
	burst: {
		enabled: true,
		// The held pose's release: e^(-dt/tauS) * cos(2*pi*hz*dt).
		release: {tauS: 0.09, hz: 2},
		// Body: squash-and-stretch pop from t0, e^(-dt/tauS) * sin(2*pi*hz*dt); scaleY
		// gets +pop (a negative pop squashes first), scaleX -pop * widthRatio.
		pop: {widthRatio: 0.6, tauS: 0.12, hz: 3.5},
		shake: {amplitudePx: 18, tauS: 0.08, durationS: 0.3, seed: `${brand.seedPrefix}-opening-shake-v1`},
		zoom: {amount: 0.05, tauS: 0.08, durationS: 0.3},
	},
	// Per animation, held from frame 0 until t0 and then released (the rig's moves, no
	// new art): eyeGrow (eye whites, +0.3 = 1.3x), pupilScale (pupils relative to the
	// whites), stalkStretch (stalks up, negative: pulled down), lid (eyelids 0 open .. 1
	// shut: a squint or half-lidded look), pop (the body's squash-and-stretch amount),
	// punch (the scene: 'shake', 'zoom' or 'none') at punchScale of OPENING.burst's size.
	animations: {
		// Outrage (opening-v2's burst): huge eyes, pin pupils, stalks up, a pop and a shake.
		outrage: {eyeGrow: 0.3, pupilScale: 0.6, stalkStretch: 0.14, lid: 0, pop: 0.1, punch: 'shake', punchScale: 1},
		// Disgust: a squint, stalks pulled back, the body recoils (squash first), a small shake.
		disgust: {eyeGrow: 0, pupilScale: 1, stalkStretch: -0.06, lid: 0.45, pop: -0.07, punch: 'shake', punchScale: 0.5},
		// Mock excitement: wide eyes, stalks up, a big bouncy pop, a zoom kick.
		excited: {eyeGrow: 0.2, pupilScale: 1, stalkStretch: 0.12, lid: 0, pop: 0.12, punch: 'zoom', punchScale: 1},
		// Fake shock: huge eyes, tiny pupils, stalks up, a pop, a zoom kick.
		shock: {eyeGrow: 0.3, pupilScale: 0.5, stalkStretch: 0.14, lid: 0, pop: 0.08, punch: 'zoom', punchScale: 1},
	} as Record<string, OpenerAnimation>,
};

export type OpenerAnimation = {
	eyeGrow: number;
	pupilScale: number;
	stalkStretch: number;
	lid: number;
	pop: number;
	punch: 'shake' | 'zoom' | 'none';
	punchScale: number;
};

// The opener animation for the `opening` prop: its `animation`, or "outrage" for
// opening-v2 props (burst with no name); null for none. An unknown name fails the render.
export const openerAnimation = (opening: {burst: boolean; animation?: string | null} | null): OpenerAnimation | null => {
	if (!OPENING.burst.enabled || !opening?.burst) {
		return null;
	}
	const name = opening.animation ?? 'outrage';
	const a = OPENING.animations[name];
	if (!a) {
		throw new Error(`Unknown opener animation "${name}"; expected one of: ${Object.keys(OPENING.animations).join(', ')}`);
	}
	return a;
};

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// --- frame 0 -------------------------------------------------------------------------

// The pictures with the first one on from frame 0, already faded in (its start, 0 from
// scripts/render_reel.py, moved back by the crossfade).
export const imagesFromFrame0 = (images: ScreenImage[], crossfadeS: number): ScreenImage[] =>
	OPENING.imageFromFrame0 && images.length > 0 && images[0].start > -crossfadeS
		? [{...images[0], start: -crossfadeS}, ...images.slice(1)]
		: images;

export const firstViseme = (cues: MouthCue[]): MouthCue | undefined => cues.find((c) => c.value !== 'X');

// The mouth at t: the first viseme before it starts (with mouthFromFrame0), else `mouth`.
export const openingMouth = (cues: MouthCue[], t: number, mouth: MouthShape): MouthShape => {
	const first = firstViseme(cues);
	return OPENING.mouthFromFrame0 && first && t < first.start ? first.value : mouth;
};

// When the first word starts: the first caption word, else the first viseme, else 0.
export const firstWordS = (captions: CaptionChunk[], cues: MouthCue[]): number =>
	captions[0]?.lines[0]?.[0]?.start ?? firstViseme(cues)?.start ?? 0;

// --- push-in -------------------------------------------------------------------------

export const pushInScale = (t: number): number => {
	const p = OPENING.pushIn;
	if (!p.enabled) {
		return 1;
	}
	return interpolate(t, [0, p.durationS], [p.from, 1], {...clamp, easing: Easing.out(Easing.cubic)});
};

// --- burst ---------------------------------------------------------------------------

// The held pose: 1 from frame 0 to t0, then a damped release to 0 (dipping a little below).
export const burstHold = (t: number, t0: number): number => {
	const {tauS, hz} = OPENING.burst.release;
	if (t <= t0) {
		return 1;
	}
	const dt = t - t0;
	return Math.exp(-dt / tauS) * Math.cos(2 * Math.PI * hz * dt);
};

// The body pop: 0 before t0, a damped oscillation after it (stretch first).
export const burstPop = (t: number, t0: number): number => {
	const {tauS, hz} = OPENING.burst.pop;
	if (t <= t0) {
		return 0;
	}
	const dt = t - t0;
	return Math.exp(-dt / tauS) * Math.sin(2 * Math.PI * hz * dt);
};

export type BurstPose = {
	eyeScale: number;
	pupilScale: number;
	stalkStretch: number;
	lid: number;
	bodyScaleY: number;
	bodyScaleX: number;
};

export const NEUTRAL_POSE: BurstPose = {eyeScale: 1, pupilScale: 1, stalkStretch: 1, lid: 0, bodyScaleY: 1, bodyScaleX: 1};

export const burstPose = (t: number, t0: number, a: OpenerAnimation): BurstPose => {
	const hold = burstHold(t, t0);
	const pop = burstPop(t, t0);
	return {
		eyeScale: 1 + a.eyeGrow * hold,
		pupilScale: 1 - (1 - a.pupilScale) * Math.max(0, hold),
		stalkStretch: 1 + a.stalkStretch * hold,
		lid: a.lid * Math.max(0, hold),
		bodyScaleY: 1 + a.pop * pop,
		bodyScaleX: 1 - a.pop * OPENING.burst.pop.widthRatio * pop,
	};
};

// The scene punch at t0: a translation (shake) and a scale (zoom), both decaying.
export const burstPunch = (t: number, t0: number, fps: number, an: OpenerAnimation): {x: number; y: number; scale: number} => {
	const b = OPENING.burst;
	const dt = t - t0;
	if (an.punch === 'shake' && dt >= 0 && dt < b.shake.durationS) {
		const s = b.shake;
		const k = Math.round(dt * fps);
		const a = an.punchScale * s.amplitudePx * Math.exp(-dt / s.tauS);
		return {x: a * (2 * random(`${s.seed}:x:${k}`) - 1), y: a * (2 * random(`${s.seed}:y:${k}`) - 1), scale: 1};
	}
	if (an.punch === 'zoom' && dt >= 0 && dt < b.zoom.durationS) {
		return {x: 0, y: 0, scale: 1 + an.punchScale * b.zoom.amount * Math.exp(-dt / b.zoom.tauS)};
	}
	return {x: 0, y: 0, scale: 1};
};
