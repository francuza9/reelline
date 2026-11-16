import type shareSpec from './share.json';
import type {SharePlatform} from './types';

// The share arrow's shape (remotion/src/ShareArrow.tsx draws and animates it): the shaft as
// a pen would draw it, placed for a platform's share button. No Remotion, no React (type-only
// imports), so Node can run it as is (tests/test_ending.py checks every platform's arrow).

export type ShareSpec = typeof shareSpec;
export type Pt = [number, number];
export type ArrowSpec = ShareSpec['arrow'];
export type LabelSpec = ShareSpec['label'];
export type Placement = {button: number[]; angleDeg: number; gapPx: number};
type PlatformSpec = Placement & Partial<Omit<ArrowSpec, 'loop' | 'head' | 'colors' | 'wiggle' | 'exit'>> & {label?: Partial<LabelSpec>};

// The platform's placement, with its overrides of the arrow's and the label's keys.
export const platformSpec = (spec: ShareSpec, platform: SharePlatform): {arrow: ArrowSpec; label: LabelSpec; place: Placement} => {
	const p = (spec.platforms as Record<SharePlatform, PlatformSpec>)[platform];
	const {button, angleDeg, gapPx, label, ...arrow} = p;
	return {arrow: {...spec.arrow, ...arrow}, label: {...spec.label, ...(label ?? {})}, place: {button, angleDeg, gapPx}};
};

const rad = (deg: number) => (deg * Math.PI) / 180;

// How much of the loop's curvature is on at u (0..1 across loop.lengthPx): a quarter easing
// in, half at full, a quarter easing out (it integrates to 0.75), so the loop comes out round
// and joins the rest of the shaft without a kink.
const loopShape = (u: number) => (u < 0.25 ? 0.5 - 0.5 * Math.cos(4 * Math.PI * u) : u > 0.75 ? 0.5 - 0.5 * Math.cos(4 * Math.PI * (1 - u)) : 1);

// The shaft's points in its own frame, drawn the way a pen moves: its heading turns by
// turnDeg along lengthPx, plus one full turn (loop.turns, -1 the other way) over
// loop.lengthPx centred at loop.at of the way, plus a wobble of the heading (zero at both
// ends). The tip ends at (0, 0) heading along +x.
export const shaftPoints = (A: ArrowSpec): Pt[] => {
	const n = A.samples;
	const ds = A.lengthPx / n;
	const turn = rad(A.turnDeg);
	const w = A.loop.lengthPx;
	const from = A.loop.at * A.lengthPx - w / 2;
	const curvature = (s: number) =>
		turn / A.lengthPx + (A.loop.on && s > from && s < from + w ? ((2 * Math.PI * A.loop.turns) / (0.75 * w)) * loopShape((s - from) / w) : 0);
	const wobble = (s: number) => rad(A.wobbleDeg) * Math.sin((2 * Math.PI * 1.7 * s) / A.lengthPx + 0.6) * Math.sin((Math.PI * s) / A.lengthPx);
	const pts: Pt[] = [[0, 0]];
	let heading = -turn;
	let x = 0;
	let y = 0;
	for (let i = 0; i < n; i++) {
		// The midpoint rule: the heading half a step on, for the step's direction.
		const mid = heading + (curvature(i * ds) * ds) / 2 + wobble((i + 0.5) * ds);
		x += ds * Math.cos(mid);
		y += ds * Math.sin(mid);
		heading += curvature((i + 0.5) * ds) * ds;
		pts.push([x, y]);
	}
	return pts.map(([px, py]): Pt => [px - x, py - y]);
};

export const lengthOf = (pts: Pt[]) => pts.slice(1).reduce((sum, p, i) => sum + Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]), 0);
export const pathOf = (pts: Pt[]) => pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');

const crosses = (a: Pt, b: Pt, c: Pt, d: Pt): boolean => {
	const side = (p: Pt, q: Pt, r: Pt) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
	return side(a, b, c) * side(a, b, d) < 0 && side(c, d, a) * side(c, d, b) < 0;
};

// The index of the later segment where the shaft first crosses itself (the loop), or null.
export const crossing = (pts: Pt[]): number | null => {
	for (let j = 2; j < pts.length - 1; j++) {
		for (let i = 0; i < j - 2; i++) {
			if (crosses(pts[i], pts[i + 1], pts[j], pts[j + 1])) {
				return j;
			}
		}
	}
	return null;
};

export type ArrowGeometry = {
	// The shaft in output pixels, from the tail to where it ends under the head.
	points: Pt[];
	length: number;
	// The stretch of the shaft drawn again over the loop's crossing (the later pass on
	// top), as [from, to) point indices, or null without a crossing.
	over: [number, number] | null;
	// Where the shaft ends (under the head), the head's tip, the direction it points (radians).
	end: Pt;
	tip: Pt;
	angle: number;
	tail: Pt;
};

// The arrow placed for a platform: turned so it ends pointing along angleDeg, the head's
// tip gapPx short of the button's centre.
export const arrowGeometry = (A: ArrowSpec, place: Placement): ArrowGeometry => {
	const angle = rad(place.angleDeg);
	const dir: Pt = [Math.cos(angle), Math.sin(angle)];
	const tip: Pt = [place.button[0] - place.gapPx * dir[0], place.button[1] - place.gapPx * dir[1]];
	const reach = A.head.lengthPx * 0.55;
	const end: Pt = [tip[0] - reach * dir[0], tip[1] - reach * dir[1]];
	const points = shaftPoints(A).map(([x, y]): Pt => [end[0] + x * dir[0] - y * dir[1], end[1] + x * dir[1] + y * dir[0]]);
	const j = crossing(points);
	const ds = A.lengthPx / A.samples;
	const m = Math.ceil((A.strokePx + 2 * A.outlinePx) / ds) + 2;
	const over: [number, number] | null = j === null ? null : [Math.max(1, j - m), Math.min(points.length - 1, j + m + 2)];
	return {points, length: lengthOf(points), over, end, tip, angle, tail: points[0]};
};

// The head on a 1 x 1 frame pointing along +x, its tip at (0.55, 0) (scaled by lengthPx and
// widthPx): a chunky arrowhead with a notch at the back.
export const headPath = (H: ArrowSpec['head']) => {
	const l = H.lengthPx;
	const w = H.widthPx / 2;
	const back = -0.45 * l;
	return `M ${0.55 * l} 0 L ${back} ${w} L ${back + H.notch * l} 0 L ${back} ${-w} Z`;
};
