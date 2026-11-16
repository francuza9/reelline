import {useVideoConfig} from 'remotion';
import type {Side} from '@engine/rig';
import type {Anchors} from '@engine/types';

// The generic host, drawn from the rig's anchors.json alone (colors, face placement, mouth anchor): flat
// SVG shapes on the rig's asset canvas, no image files. A channel with its own drawings replaces this file
// by remotion/Mascot.tsx in its own folder (the same exports; Remotion resolves @channel there first).

// Placement in output pixels.
const BODY_WIDTH = 900;
const WHEELS_ABOVE_BOTTOM = 150;

// Eyes, relative to radius_inner.
export const EYE_WHITE_RATIO = 0.78;
const PUPIL_RATIO = 0.42;
const STROKE = 8;

export const restMouth = (anchors: Anchors, mood: string): string => {
	const name = mood === 'note' ? undefined : anchors.mouth.rest_by_mood[mood];
	if (!name) {
		const moods = Object.keys(anchors.mouth.rest_by_mood).filter((m) => m !== 'note');
		throw new Error(`Unknown mood "${mood}"; expected one of: ${moods.join(', ')}`);
	}
	return name;
};

// Where the rig's asset canvas lands in the output: top-left corner and scale.
export const rigPlacement = (anchors: Anchors, width: number, height: number) => {
	const scale = BODY_WIDTH / anchors.canvas.width;
	return {
		left: (width - BODY_WIDTH) / 2,
		top: height - WHEELS_ABOVE_BOTTOM - anchors.chair.wheel_bottom_y * scale,
		scale,
	};
};

export type Box = {left: number; top: number; right: number; bottom: number};

// The face in output pixels, at rest: each eye's outer circle, and the mouth's box.
export const faceBoxes = (anchors: Anchors, width: number, height: number): {eyes: Box[]; mouth: Box} => {
	const {left, top, scale} = rigPlacement(anchors, width, height);
	const box = (x0: number, y0: number, x1: number, y1: number): Box => ({
		left: left + x0 * scale,
		top: top + y0 * scale,
		right: left + x1 * scale,
		bottom: top + y1 * scale,
	});
	const eyes = (['left', 'right'] as const).map((side) => {
		const {center, radius_outer: r} = anchors.eyes[side];
		return box(center[0] - r, center[1] - r, center[0] + r, center[1] + r);
	});
	const [mw, mh] = anchors.mouth.png_size;
	const [mx, my] = anchors.mouth.anchor;
	return {eyes, mouth: box(mx - mw / 2, my - mh / 2, mx + mw / 2, my + mh / 2)};
};

type Colors = Anchors['colors'] & {belly?: string; chair?: string; tongue?: string};

// One mouth shape (a lip-sync code A-H, the rest X, or a mood's rest X_<mood>) centred on (0, 0).
const Mouth: React.FC<{code: string; colors: Colors}> = ({code, colors}) => {
	const ink = colors.outline;
	const tongue = colors.tongue ?? '#E9707F';
	const line = {stroke: ink, strokeWidth: STROKE, strokeLinecap: 'round', strokeLinejoin: 'round'} as const;
	const open = (rx: number, ry: number, teeth = false, tongueUp = false) => (
		<g>
			<ellipse cx={0} cy={0} rx={rx} ry={ry} fill={ink} />
			{tongueUp ? <ellipse cx={0} cy={ry * 0.45} rx={rx * 0.55} ry={ry * 0.4} fill={tongue} /> : null}
			{teeth ? <rect x={-rx * 0.7} y={-ry} width={rx * 1.4} height={ry * 0.32} rx={6} fill="#FFFFFF" /> : null}
		</g>
	);
	switch (code) {
		case 'A': // M, B, P: closed
			return <path d="M -50 0 H 50" fill="none" {...line} />;
		case 'B': // S, T, K, EE: a wide slit
			return <path d="M -70 -6 Q 0 14 70 -6 Q 0 -2 -70 -6 Z" fill={ink} {...line} />;
		case 'C': // EH, AE
			return open(64, 34, true);
		case 'D': // AA: wide open
			return open(58, 62, true, true);
		case 'E': // AO, ER: rounded
			return open(42, 48, false, true);
		case 'F': // OO, W: small round
			return open(26, 28);
		case 'G': // F, V: teeth on the lip
			return (
				<g>
					<path d="M -60 -10 Q 0 24 60 -10" fill="none" {...line} />
					<rect x={-40} y={-14} width={80} height={20} rx={6} fill="#FFFFFF" stroke={ink} strokeWidth={5} />
				</g>
			);
		case 'H': // L: tongue up
			return open(50, 40, true, true);
		case 'X_happy':
			return <path d="M -70 -14 Q 0 60 70 -14 Z" fill={ink} {...line} />;
		case 'X_surprised':
			return open(22, 32);
		default: // X: the neutral rest
			return <path d="M -54 0 Q 0 22 54 0" fill="none" {...line} />;
	}
};

const Eye: React.FC<{cx: number; cy: number; radiusInner: number; lid: number; pupil: [number, number]; colors: Colors; scale: number; pupilScale: number; id: string}> = ({
	cx,
	cy,
	radiusInner,
	lid,
	pupil,
	colors,
	scale,
	pupilScale,
	id,
}) => {
	const r = radiusInner * EYE_WHITE_RATIO * scale;
	return (
		<g>
			<defs>
				<clipPath id={id}>
					<circle cx={cx} cy={cy} r={r} />
				</clipPath>
			</defs>
			<circle cx={cx} cy={cy} r={r} fill="#FFFFFF" />
			<circle cx={cx + pupil[0] * scale} cy={cy + pupil[1] * scale} r={r * PUPIL_RATIO * pupilScale} fill={colors.outline} clipPath={`url(#${id})`} />
			{lid > 0 ? <rect x={cx - r} y={cy - r} width={2 * r} height={lid * 2 * r} fill={colors.yellow} clipPath={`url(#${id})`} /> : null}
			<circle cx={cx} cy={cy} r={r} fill="none" stroke={colors.outline} strokeWidth={STROKE} />
		</g>
	);
};

// The host in one pose: `bodyScaleY` is the breathing + talking bounce (pivoting on the seat line),
// `lid` the eyelid (0 open, 1 closed), `pupil` the pupil offset in canvas pixels. The opening burst's
// extras are all 1 at rest. Props the generic host has no use for (the stalks' sway) are accepted and
// ignored, so the reel can pass every host the same props.
export const Host: React.FC<{
	anchors: Anchors;
	mouthImage: string;
	bodyScaleY: number;
	angle?: Record<Side, number>;
	lid: number;
	pupil: [number, number];
	bodyScaleX?: number;
	stalkStretch?: number;
	eyeScale?: number;
	pupilScale?: number;
	pupilCross?: number;
}> = ({anchors, mouthImage, bodyScaleY, lid, pupil, bodyScaleX = 1, eyeScale = 1, pupilScale = 1, pupilCross = 0}) => {
	const {width, height} = useVideoConfig();
	const canvas = anchors.canvas;
	const {left, top, scale} = rigPlacement(anchors, width, height);
	const colors = anchors.colors as Colors;
	const seatY = anchors.chair.seat_line_y;
	const cx = anchors.chair.center_x;
	const wheelY = anchors.chair.wheel_bottom_y;
	const chair = colors.chair ?? '#3A4468';
	const [mx, my] = anchors.mouth.anchor;
	return (
		<div style={{position: 'absolute', left, top, width: canvas.width, height: canvas.height, transform: `scale(${scale})`, transformOrigin: '0 0'}}>
			<svg width={canvas.width} height={canvas.height} viewBox={`0 0 ${canvas.width} ${canvas.height}`} style={{position: 'absolute', left: 0, top: 0}}>
				{/* The chair: a post, a base on wheels, a seat. */}
				<rect x={cx - 24} y={seatY} width={48} height={wheelY - seatY - 70} fill={chair} />
				<path d={`M ${cx - 260} ${wheelY - 50} H ${cx + 260}`} stroke={chair} strokeWidth={28} strokeLinecap="round" />
				{[-260, 0, 260].map((dx) => (
					<circle key={dx} cx={cx + dx} cy={wheelY - 28} r={28} fill={colors.outline} />
				))}
				<rect x={cx - 250} y={seatY - 22} width={500} height={56} rx={28} fill={chair} />
				{/* The body: breathing and the talking bounce pivot on the seat line. */}
				<g transform={`translate(${cx} ${seatY}) scale(${bodyScaleX} ${bodyScaleY}) translate(${-cx} ${-seatY})`}>
					<path
						d={`M ${cx - 260} ${seatY} C ${cx - 300} ${seatY - 260} ${cx - 290} ${anchors.eyes.left.center[1] - 200} ${cx} ${anchors.eyes.left.center[1] - 200} C ${cx + 290} ${anchors.eyes.left.center[1] - 200} ${cx + 300} ${seatY - 260} ${cx + 260} ${seatY} Z`}
						fill={colors.yellow}
						stroke={colors.outline}
						strokeWidth={STROKE}
						strokeLinejoin="round"
					/>
					<ellipse cx={cx} cy={seatY - 150} rx={170} ry={130} fill={colors.belly ?? colors.yellow} opacity={0.55} />
					{(['left', 'right'] as const).map((side) => {
						const eye = anchors.eyes[side];
						return (
							<Eye
								key={side}
								id={`eye-${side}`}
								cx={eye.center[0]}
								cy={eye.center[1]}
								radiusInner={eye.radius_inner}
								lid={lid}
								pupil={[pupil[0] + (side === 'left' ? pupilCross : -pupilCross), pupil[1]]}
								colors={colors}
								scale={eyeScale}
								pupilScale={pupilScale}
							/>
						);
					})}
					<g transform={`translate(${mx} ${my})`}>
						<Mouth code={mouthImage} colors={colors} />
					</g>
				</g>
			</svg>
		</div>
	);
};
