import angleSpec from '@channel/graphics/angles.json';
import {MOTION} from './motion';
import {MotionProps} from './types';
import {Box} from '@channel/remotion/Mascot';

// The camera's other angles (angles.json: the rigs and the rooms' 3/4 art; motion.json
// framing.angles: when, reelline/media/motion.py plan_angles()). A shot carries its angle; none: front,
// the scene as it always was.
export const FRONT = 'front';

export type Ellipse = {cx: number; cy: number; rx: number; ry: number; rotate: number};
export type RigEye = {white: Ellipse; pupil: Ellipse; highlight: {cx: number; cy: number; r: number}; box: number[]};
export type RigSpec = {
	scene: 'front' | 'angled';
	image: string;
	size: number[];
	placement: {left: number; top: number; scale: number};
	seatY: number;
	stalks: {cutY: number; splitX: number};
	mouth: {dir: string; pngSize: number[]; center: number[]; scale: number; clipX: number[][]} | null;
	eyes: {left: RigEye; right: RigEye} | null;
	// A rig with eyes: the pupil's cover, its travel, the lid's edge and the art's colours.
	coverPx?: number;
	pupilTravel?: number;
	// Where a glance up at the TV looks (motion.json tvGlance.pupil's unit).
	tvPupil?: number[];
	lidStrokePx?: number;
	colors?: {yellow: string; outline: string; white: string};
};
export type Quad = number[][];
export type AngleArt = {file: string; frame: Quad; face: Quad};

export const RIGS = angleSpec.rigs as Record<string, RigSpec>;
const ART = angleSpec.backgrounds as unknown as Record<string, Record<string, AngleArt>>;

export const angleNames = (): string[] => [FRONT, ...Object.keys(RIGS)];

export const rigOf = (angle: string): RigSpec => {
	const rig = RIGS[angle];
	if (!rig) {
		throw new Error(`Unknown camera angle "${angle}"; expected one of: ${angleNames().join(', ')}`);
	}
	return rig;
};

// The background's art for an angled rig ("bedroom_night.png" -> angles.json backgrounds.bedroom_night).
export const angleArt = (background: string, angle: string): AngleArt => {
	const art = ART[background.replace(/\.png$/, '')]?.[angle];
	if (!art) {
		throw new Error(`No ${angle} art for the background ${background} in angles.json backgrounds`);
	}
	return art;
};

// The angle on at t (the last shot started by then); front without shots.
export const angleAt = (shots: MotionProps['shots'], t: number): string => {
	if (!MOTION.framing.enabled || !shots || shots.length === 0) {
		return FRONT;
	}
	let angle = shots[0].angle ?? FRONT;
	for (const s of shots) {
		if (s.start <= t + 1e-9) {
			angle = s.angle ?? FRONT;
		}
	}
	return angle;
};

// A rig's face at rest in output pixels (reelline/media/angles.py face_boxes()): each eye's outer box and
// the mouth image's box; none from behind.
export const angleFaceBoxes = (angle: string): {eyes: Box[]; mouth: Box | null} => {
	const rig = rigOf(angle);
	const {left, top, scale} = rig.placement;
	const box = (x0: number, y0: number, x1: number, y1: number): Box => ({
		left: left + x0 * scale,
		top: top + y0 * scale,
		right: left + x1 * scale,
		bottom: top + y1 * scale,
	});
	const eyes = rig.eyes ? [rig.eyes.left, rig.eyes.right].map((e) => box(e.box[0], e.box[1], e.box[2], e.box[3])) : [];
	const m = rig.mouth;
	const mouth = m
		? box(
				m.center[0] - (m.pngSize[0] * m.scale) / 2,
				m.center[1] - (m.pngSize[1] * m.scale) / 2,
				m.center[0] + (m.pngSize[0] * m.scale) / 2,
				m.center[1] + (m.pngSize[1] * m.scale) / 2,
			)
		: null;
	return {eyes, mouth};
};

// An angled rig's TV in every room it has art for: the frame quad's bounding box (the framing check).
export const angleTvBoxes = (angle: string): Box[] =>
	Object.entries(ART)
		.filter(([room, arts]) => room !== 'note' && arts[angle])
		.map(([, arts]) => {
			const xs = arts[angle].frame.map((p) => p[0]);
			const ys = arts[angle].frame.map((p) => p[1]);
			return {left: Math.min(...xs), top: Math.min(...ys), right: Math.max(...xs), bottom: Math.max(...ys)};
		});

// --- perspective ---------------------------------------------------------------------------

// The projective map taking the four corners `from` onto `to` (each top-left, top-right,
// bottom-right, bottom-left), as a CSS matrix3d for an element with transform-origin 0 0.
export const quadTransform = (from: Quad, to: Quad): string => {
	// Solve h (8 unknowns): u = (h0 x + h1 y + h2) / (h6 x + h7 y + 1), v = (h3 x + h4 y + h5) / (...).
	const a: number[][] = [];
	const b: number[] = [];
	for (let i = 0; i < 4; i++) {
		const [x, y] = from[i];
		const [u, v] = to[i];
		a.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
		b.push(u);
		a.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
		b.push(v);
	}
	const h = solve(a, b);
	// Column-major 4x4: x' = h0 x + h1 y + h2, y' = h3 x + h4 y + h5, w = h6 x + h7 y + 1.
	const m = [h[0], h[3], 0, h[6], h[1], h[4], 0, h[7], 0, 0, 1, 0, h[2], h[5], 0, 1];
	return `matrix3d(${m.map((v) => +v.toFixed(10)).join(', ')})`;
};

// Gaussian elimination with partial pivoting.
const solve = (a: number[][], b: number[]): number[] => {
	const n = b.length;
	const m = a.map((row, i) => [...row, b[i]]);
	for (let c = 0; c < n; c++) {
		let p = c;
		for (let r = c + 1; r < n; r++) {
			if (Math.abs(m[r][c]) > Math.abs(m[p][c])) {
				p = r;
			}
		}
		if (Math.abs(m[p][c]) < 1e-12) {
			throw new Error('quadTransform: the corners are degenerate');
		}
		[m[c], m[p]] = [m[p], m[c]];
		for (let r = 0; r < n; r++) {
			if (r !== c) {
				const f = m[r][c] / m[c][c];
				for (let k = c; k <= n; k++) {
					m[r][k] -= f * m[c][k];
				}
			}
		}
	}
	return m.map((row, i) => row[n] / row[i]);
};
