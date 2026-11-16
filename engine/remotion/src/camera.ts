import {FRONT, angleArt, angleFaceBoxes, angleTvBoxes, rigOf} from './angles';
import {CAPTIONS} from './Captions';
import {popoutBounds} from './Fx';
import {MOTION} from './motion';
import {Anchors, FxEvent, MotionProps, ScreenSpec, ShareProps} from './types';
import {Box, faceBoxes} from '@channel/remotion/Mascot';

// The camera (motion.json framing; reelline/media/motion.py plan_shots() plans the cuts): hard cuts
// between shots, each a crop of the composed scene (background, TV, the host): scale about an
// origin in output pixels. The captions, the fact cards and the end card stay where they are.
export type ShotSpec = {scale: number; origin: number[]; close: boolean};
export const SHOTS = MOTION.framing.shots as Record<string, ShotSpec>;

// What the shot does to the scene: p on screen = origin + scale * (p - origin).
export type Camera = {scale: number; x: number; y: number};
export const NO_CAMERA: Camera = {scale: 1, x: 540, y: 960};

const shotSpec = (name: string): ShotSpec => {
	const spec = SHOTS[name];
	if (!spec) {
		throw new Error(`Unknown shot "${name}"; expected one of: ${Object.keys(SHOTS).join(', ')}`);
	}
	return spec;
};

export const cameraOf = (name: string): Camera => {
	const spec = shotSpec(name);
	return {scale: spec.scale, x: spec.origin[0], y: spec.origin[1]};
};

// The shot on at t (the last one started by then); no shots (props from before the framing,
// or framing off): no camera.
export const shotAt = (shots: MotionProps['shots'], t: number): string | null => {
	if (!MOTION.framing.enabled || !shots || shots.length === 0) {
		return null;
	}
	let name = shots[0].shot;
	for (const s of shots) {
		if (s.start <= t + 1e-9) {
			name = s.shot;
		}
	}
	return name;
};

export const cameraAt = (shots: MotionProps['shots'], t: number): Camera => {
	const name = shotAt(shots, t);
	return name === null ? NO_CAMERA : cameraOf(name);
};

export const cameraTransform = (c: Camera): {transform: string; transformOrigin: string} => ({
	transform: `scale(${c.scale})`,
	transformOrigin: `${c.x}px ${c.y}px`,
});

export const onCamera = (c: Camera, x: number, y: number): [number, number] => [c.x + c.scale * (x - c.x), c.y + c.scale * (y - c.y)];

// --- checks ------------------------------------------------------------------------------

// A CSS transform origin ("50% 60%") in output pixels.
const percentOrigin = (origin: string, width: number, height: number): [number, number] => {
	const [x, y] = origin.split(/\s+/).map((p) => parseFloat(p) / 100);
	return [x * width, y * height];
};

// The largest push-in a shot can take (reelline/media/motion.py max_push): none in a close shot.
const maxPush = (spec: ShotSpec): number => (spec.close ? 1 : Math.max(MOTION.zoom.scale[1], MOTION.beats.zoomScale[1]));

// Where a shot breaks framing.safe (reelline/media/motion.py framing_problems(), the same rules): the host's
// eyes and mouth (rigPadPx bigger all round), with no push-in and with the largest it can take,
// below the caption block's bottom, above bottomPx and inside the side margins; in cardShots,
// below the settled card; a shot that isn't close keeps the whole TV in the frame. `angle`: the
// shot in another camera angle, its rig's face (angles.ts angleFaceBoxes(); none from behind)
// and for an angled rig the TV in every room's 3/4 art; the card's rule only in cardAngles.
export const framingProblems = (
	name: string,
	anchors: Anchors,
	screen: ScreenSpec,
	width: number,
	height: number,
	angle: string = FRONT,
): string[] => {
	const spec = shotSpec(name);
	const {safe, cardShots} = MOTION.framing;
	const label = angle === FRONT ? name : `${name} (${angle})`;
	const cam = cameraOf(name);
	const [zx, zy] = percentOrigin(MOTION.zoom.origin, width, height);
	const pad = safe.rigPadPx;
	const captions = CAPTIONS.bottomY + safe.captionClearancePx;
	const cardBottom = popoutBounds(width).bottom;
	const face = angle === FRONT ? faceBoxes(anchors, width, height) : angleFaceBoxes(angle);
	const boxes: [string, Box][] = [
		...face.eyes.map((b, i): [string, Box] => [i === 0 ? 'left eye' : 'right eye', b]),
		...(face.mouth ? [['mouth', face.mouth] as [string, Box]] : []),
	];
	const cardsOut = MOTION.framing.angles.cardAngles.includes(angle);
	const problems: string[] = [];
	for (const push of [...new Set([1, maxPush(spec)])]) {
		const at = push === 1 ? '' : ` at a ${push}x push-in`;
		const map = (x: number, y: number) => onCamera(cam, zx + push * (x - zx), zy + push * (y - zy));
		for (const [what, b] of boxes) {
			const [left, top] = map(b.left - pad, b.top - pad);
			const [right, bottom] = map(b.right + pad, b.bottom + pad);
			if (top < captions) {
				problems.push(`${label}: the host's ${what} top ${top.toFixed(0)} is above the captions' bottom ${captions}${at}`);
			}
			if (bottom > safe.bottomPx) {
				problems.push(`${label}: the host's ${what} bottom ${bottom.toFixed(0)} is below bottomPx ${safe.bottomPx}${at}`);
			}
			if (left < safe.sideMarginPx || right > width - safe.sideMarginPx) {
				problems.push(`${label}: the host's ${what} (${left.toFixed(0)}-${right.toFixed(0)}) is within sideMarginPx ${safe.sideMarginPx} of a side${at}`);
			}
			if (cardShots.includes(name) && cardsOut && push === 1 && top < cardBottom) {
				problems.push(`${label}: the host's ${what} top ${top.toFixed(0)} is under the card (bottom ${cardBottom.toFixed(0)})`);
			}
		}
	}
	if (!spec.close) {
		const s = screen.screen;
		const tvs: Box[] =
			angle === FRONT || rigOf(angle).scene === 'front'
				? [{left: s.x, top: s.y, right: s.x + s.width, bottom: s.y + s.height}]
				: angleTvBoxes(angle);
		for (const tv of tvs) {
			const [left, top] = onCamera(cam, tv.left, tv.top);
			const [right, bottom] = onCamera(cam, tv.right, tv.bottom);
			if (left < 0 || top < 0 || right > width || bottom > height) {
				problems.push(`${label}: the TV (${[left, top, right, bottom].map(Math.round).join(', ')}) is cut off in a shot that isn't close`);
			}
		}
	}
	return problems;
};

// Throws on shots Remotion can't draw as planned: an unknown or repeated shot (the same framing
// in the same angle), out of order, not from 0, a shot that isn't in cardShots (or is close) or
// an angle outside cardAngles while a card is out, an angle outside arrowAngles while the share
// arrow is up, an angled rig the background has no art for, or any shot breaking framing.safe.
export const checkShots = (
	shots: MotionProps['shots'],
	fx: FxEvent[],
	anchors: Anchors,
	screen: ScreenSpec,
	width: number,
	height: number,
	share: ShareProps | null = null,
	background: string | null = null,
): void => {
	if (!MOTION.framing.enabled || !shots || shots.length === 0) {
		return;
	}
	const {cardAngles, arrowAngles} = MOTION.framing.angles;
	const problems: string[] = [];
	if (shots[0].start !== 0) {
		problems.push(`the first shot starts at ${shots[0].start}s, not 0`);
	}
	shots.forEach((s, i) => {
		const prev = shots[i - 1];
		const angle = s.angle ?? FRONT;
		const label = angle === FRONT ? s.shot : `${s.shot} (${angle})`;
		const same = prev !== undefined && s.shot === prev.shot && angle === (prev.angle ?? FRONT);
		if (prev && (s.start <= prev.start || same)) {
			problems.push(`shot ${i} (${label} at ${s.start}s) ${same ? 'repeats' : 'is out of order after'} ${prev.shot} at ${prev.start}s`);
		}
		const end = shots[i + 1]?.start ?? Infinity;
		const spec = shotSpec(s.shot);
		if (angle !== FRONT) {
			const rig = rigOf(angle);
			if (rig.scene === 'angled' && background !== null) {
				angleArt(background, angle);
			}
		}
		const card = fx.find((e) => e.start < end && e.end > s.start);
		if (card && (spec.close || !MOTION.framing.cardShots.includes(s.shot) || !cardAngles.includes(angle))) {
			problems.push(`the ${label} shot at ${s.start}s runs while the card at ${card.start}s is out`);
		}
		if (share && share.platform !== null && share.start < end && share.end > s.start && !arrowAngles.includes(angle)) {
			problems.push(`the ${label} shot at ${s.start}s runs while the share arrow is up (${share.start}-${share.end}s)`);
		}
	});
	const seen = new Set<string>();
	for (const s of shots) {
		const angle = s.angle ?? FRONT;
		if (!seen.has(`${s.shot}|${angle}`)) {
			seen.add(`${s.shot}|${angle}`);
			problems.push(...framingProblems(s.shot, anchors, screen, width, height, angle));
		}
	}
	if (problems.length > 0) {
		throw new Error(`Framing (motion.json framing): ${problems.join('; ')}`);
	}
};
