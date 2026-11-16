import {Easing, interpolate, random, spring} from 'remotion';
import {CAPTIONS} from '../Captions';
import {Measure} from '../fit';
import fxSpec from '@channel/graphics/fx.json';

// Shared pieces of the fact-graphic templates (remotion/src/Fx.tsx): the tunables, the
// motion helpers, caption-style text and the fit-to-box layout.
export const FX = fxSpec;
export const C = FX.colors;
export const OUTLINE = FX.shapeOutlinePx;

export type Size = {width: number; height: number};

// A template's clock: t in seconds from its start (negative before it).
export type Clock = {t: number; fps: number};

export type TemplateProps<D> = {data: D; c: Clock; box: Size; maxScale: number; measure: Measure};

// A template: its view, when its last motion settles (seconds from its start; at most
// its durationS in fx.json) and, optionally, a check that fails loudly on data it can't show.
export type TemplateDef<D> = {
	View: React.FC<TemplateProps<D>>;
	endS: (data: D) => number;
	check?: (data: D) => void;
};

export const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
export const lerp = (a: number, b: number, p: number) => a + (b - a) * p;

// --- motion --------------------------------------------------------------------------

// 0 -> 1 from atS, springing over durationS with a slight overshoot (the caption pop's
// feel; a lower damping overshoots more). 0 before atS.
export const pop = (c: Clock, atS: number, durationS: number, damping: number = FX.damping): number =>
	spring({
		frame: (c.t - atS) * c.fps,
		fps: c.fps,
		config: {damping},
		durationInFrames: Math.max(1, Math.round(durationS * c.fps)),
	});

// 0 -> 1 from atS over durationS, clamped, eased.
export const ramp = (c: Clock, atS: number, durationS: number, easing: (x: number) => number = Easing.out(Easing.cubic)): number =>
	interpolate(c.t, [atS, atS + Math.max(durationS, 1e-3)], [0, 1], {...clamp, easing});

// A snap for something landing: jumps to `scale` at atS and springs back to 1.
export const punch = (c: Clock, atS: number, durationS: number, scale: number): number =>
	c.t < atS ? 1 : lerp(scale, 1, pop(c, atS, durationS));

// A stamp slamming down at atS: it falls from FX.slam.fromScale to 1 over fallS,
// accelerating, squashes on impact and springs back. null before atS.
export const slam = (c: Clock, atS: number): {scaleX: number; scaleY: number} | null => {
	const S = FX.slam;
	if (c.t < atS) {
		return null;
	}
	const s = lerp(S.fromScale, 1, ramp(c, atS, S.fallS, Easing.in(Easing.quad)));
	const hitS = atS + S.fallS;
	const squash = c.t < hitS ? 0 : S.squash * (1 - pop(c, hitS, S.settleS));
	return {scaleX: s * (1 + squash), scaleY: s * (1 - squash)};
};

export const slamHitS = (atS: number) => atS + FX.slam.fallS;
export const slamEndS = (atS: number) => slamHitS(atS) + Math.max(FX.slam.settleS, FX.shake.durationS, FX.sparks.durationS);

// The impact shake from atS: seeded offsets decaying to 0 over FX.shake.durationS.
export const shake = (c: Clock, atS: number, seed: string): {x: number; y: number} => {
	const S = FX.shake;
	if (c.t < atS || c.t >= atS + S.durationS) {
		return {x: 0, y: 0};
	}
	const k = (1 - (c.t - atS) / S.durationS) * S.amplitudePx;
	const f = Math.round(c.t * c.fps);
	return {x: (2 * random(`${S.seed}:${seed}:x:${f}`) - 1) * k, y: (2 * random(`${S.seed}:${seed}:y:${f}`) - 1) * k};
};

// --- text ----------------------------------------------------------------------------

export const cssFont = (px: number) => `${FX.text.fontWeight} ${px}px ${CAPTIONS.fontFamily}`;
export const lineH = (px: number) => px * FX.text.lineHeight;
// Width of `text` at px, its outline included.
export const textWidth = (m: Measure, text: string, px: number) => m(text, px) + FX.text.strokeEm * px;

// Caption-style text (fill, dark outline under it); strokeEm 0 for plain dark text.
export const textStyle = (px: number, color: string, strokeEm: number = FX.text.strokeEm): React.CSSProperties => ({
	font: cssFont(px),
	lineHeight: FX.text.lineHeight,
	color,
	WebkitTextStroke: strokeEm > 0 ? `${strokeEm}em ${C.outline}` : undefined,
	paintOrder: 'stroke fill',
	whiteSpace: 'nowrap',
});

// One line of text centred on (cx, top), scaled about its centre.
export const Label: React.FC<{
	cx: number;
	top: number;
	px: number;
	color: string;
	scale?: number;
	strokeEm?: number;
	children: React.ReactNode;
}> = ({cx, top, px, color, scale = 1, strokeEm, children}) => {
	const w = 2400;
	return (
		<div
			style={{
				position: 'absolute',
				left: cx - w / 2,
				top,
				width: w,
				height: lineH(px),
				textAlign: 'center',
				...textStyle(px, color, strokeEm),
				transform: `scale(${Math.max(0, scale)})`,
				transformOrigin: '50% 50%',
			}}
		>
			{children}
		</div>
	);
};

// Whole numbers, grouped in threes from FX.numbers.groupFrom up ("5 000").
export const formatNumber = (n: number): string => {
	const s = String(Math.round(n));
	return Math.abs(n) < FX.numbers.groupFrom ? s : s.replace(/\B(?=(\d{3})+(?!\d))/g, FX.numbers.groupSeparator);
};

// --- layout --------------------------------------------------------------------------

// Content laid out at its natural size (design pixels), scaled to fit `box` (at most
// maxScale) and centred in it; `offset` moves it (an impact shake).
export const Fit: React.FC<{
	box: Size;
	maxScale: number;
	width: number;
	height: number;
	offset?: {x: number; y: number};
	children: React.ReactNode;
}> = ({box, maxScale, width, height, offset = {x: 0, y: 0}, children}) => {
	const s = Math.min(maxScale, box.width / width, box.height / height);
	return (
		<div
			style={{
				position: 'absolute',
				left: (box.width - width * s) / 2 + offset.x,
				top: (box.height - height * s) / 2 + offset.y,
				width,
				height,
				transform: `scale(${s})`,
				transformOrigin: '0 0',
			}}
		>
			{children}
		</div>
	);
};

// An absolutely placed box, scaled (and turned) about its centre; nothing at scale 0.
export const Pop: React.FC<{
	x: number;
	y: number;
	width: number;
	height: number;
	scale?: number;
	scaleX?: number;
	scaleY?: number;
	rotate?: number;
	origin?: string;
	style?: React.CSSProperties;
	children?: React.ReactNode;
}> = ({x, y, width, height, scale = 1, scaleX = 1, scaleY = 1, rotate = 0, origin = '50% 50%', style, children}) =>
	scale <= 0 ? null : (
		<div
			style={{
				position: 'absolute',
				left: x,
				top: y,
				width,
				height,
				transform: `rotate(${rotate}deg) scale(${scale * scaleX}, ${scale * scaleY})`,
				transformOrigin: origin,
				...style,
			}}
		>
			{children}
		</div>
	);

// Short lines shooting out from the edge of a w x h box centred on (cx, cy), from atS
// for FX.sparks.durationS.
export const Sparks: React.FC<{c: Clock; atS: number; cx: number; cy: number; w: number; h: number}> = ({c, atS, cx, cy, w, h}) => {
	const S = FX.sparks;
	if (c.t < atS || c.t >= atS + S.durationS || S.count === 0) {
		return null;
	}
	const p = ramp(c, atS, S.durationS);
	const len = S.lengthPx * (1 - p);
	const lines = Array.from({length: S.count}, (_, k) => {
		const a = (2 * Math.PI * (k + 0.5)) / S.count;
		const dx = Math.cos(a);
		const dy = Math.sin(a);
		const rx = w / 2 + S.marginPx + S.travelPx * p;
		const ry = h / 2 + S.marginPx + S.travelPx * p;
		return {x1: cx + dx * rx, y1: cy + dy * ry, x2: cx + dx * (rx + len), y2: cy + dy * (ry + len)};
	});
	return (
		<svg style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}} width={1} height={1}>
			{lines.map((l, i) => (
				<g key={i}>
					<line {...l} stroke={C.outline} strokeWidth={S.widthPx + OUTLINE} strokeLinecap="round" />
					<line {...l} stroke={S.color} strokeWidth={S.widthPx} strokeLinecap="round" />
				</g>
			))}
		</svg>
	);
};

// An SVG canvas over a template's whole box (shapes in design pixels).
export const Canvas: React.FC<{width: number; height: number; children: React.ReactNode}> = ({width, height, children}) => (
	<svg width={width} height={height} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
		{children}
	</svg>
);
