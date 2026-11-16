import {useMemo} from 'react';
import {AbsoluteFill, Easing, useCurrentFrame, useVideoConfig} from 'remotion';
import {CAPTIONS, useCaptionFonts} from './Captions';
import {apply, steps} from './fx/actions';
import {age, money, places, prize} from './fx/counts';
import {deadline, duration, start} from './fx/dates';
import {FX, TemplateDef, cssFont, lerp, pop, ramp} from './fx/kit';
import {certificate, condition, free} from './fx/stamps';
import {abroad, format, location} from './fx/where';
import {Drift, canvasMeasure, driftTransform, faceRect} from './Scene';
import {Anchors, FxData, FxEvent, FxTemplate, ScreenSpec} from './types';
import {Box, faceBoxes} from '@channel/remotion/Mascot';

// Animated fact graphics: one template per kind of fact (remotion/src/fx/), each a short
// motion around it, every value a prop (FxData), then a hold. Two placements: popped out
// of the TV in front of the scene (FxPopout, with FxBackdrop dimming the room behind
// it), the chosen one; or on the wall TV's face (FxOnTv, inside <Screen>), kept but not
// previewed. All tunables are in fx.json.
export {FX};

type Templates = {[K in FxTemplate]: TemplateDef<Extract<FxData, {template: K}>>};

const TEMPLATES: Templates = {
	deadline,
	age,
	money,
	places,
	condition,
	free,
	location,
	abroad,
	duration,
	start,
	certificate,
	prize,
	apply,
	format,
	steps,
};

const templateOf = (data: FxData): TemplateDef<FxData> => {
	const def = (TEMPLATES as Record<string, TemplateDef<FxData> | undefined>)[data.template];
	if (!def) {
		throw new Error(`Unknown fx template "${data.template}"; expected one of: ${Object.keys(TEMPLATES).join(', ')}`);
	}
	return def;
};

// When the template's last motion settles, in seconds from its start.
export const templateEndS = (data: FxData): number => templateOf(data).endS(data);

// Fails loudly on data a template can't show or a timeline that runs past its durationS.
const checkTemplate = (data: FxData) => {
	const def = templateOf(data);
	def.check?.(data);
	const end = def.endS(data);
	const durationS = FX.templates[data.template].durationS;
	if (end > durationS + 1e-6) {
		throw new Error(`Fx ${data.template}: its animation settles at ${end.toFixed(2)}s, after its durationS ${durationS}s (fx.json)`);
	}
};

// The template for `data`, at t seconds from its start (nothing before it), fitted into `box`.
const FxContent: React.FC<{data: FxData; t: number; box: {width: number; height: number}; maxScale: number}> = ({data, t, box, maxScale}) => {
	const {fps} = useVideoConfig();
	const fontsReady = useCaptionFonts();
	const measure = useMemo(() => (fontsReady ? canvasMeasure(cssFont) : null), [fontsReady]);
	useMemo(() => checkTemplate(data), [data]);
	if (!measure || t < 0) {
		return null;
	}
	const {View} = templateOf(data);
	return <View data={data} c={{t, fps}} box={box} maxScale={maxScale} measure={measure} />;
};

// --- placement A: on the TV ------------------------------------------------------------

// Inside <Screen> (over its picture, under the glare): the face switches on like a CRT
// (a bright line springing open), the template plays, and the face switches off at the end.
export const FxOnTv: React.FC<{event: FxEvent; spec: ScreenSpec}> = ({event, spec}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const T = FX.placements.tv;
	const t = frame / fps;
	if (event.placement !== 'tv' || t < event.start || t >= event.end) {
		return null;
	}
	const face = faceRect(spec);
	const c = {t: t - event.start, fps};
	const exitAt = event.end - event.start - T.exit.durationS;
	const open = pop(c, 0, T.enter.durationS, T.enter.damping);
	const scaleY =
		c.t < exitAt
			? lerp(T.enter.fromScaleY, 1, open)
			: lerp(1, T.exit.toScaleY, ramp(c, exitAt, T.exit.durationS, Easing.in(Easing.cubic)));
	// The line is bright while it opens.
	const flash = c.t < exitAt ? T.enter.flash.opacity * Math.max(0, 1 - open) : 0;
	const {scanlines} = spec.idle;
	const pad = T.paddingPx;
	return (
		<div
			style={{
				position: 'absolute',
				left: face.x,
				top: face.y,
				width: face.width,
				height: face.height,
				borderRadius: spec.screen.faceRadius,
				overflow: 'hidden',
				backgroundColor: T.fill,
				transform: `scaleY(${Math.max(0, scaleY)})`,
				transformOrigin: '50% 50%',
			}}
		>
			<div
				style={{
					position: 'absolute',
					inset: 0,
					backgroundImage: `repeating-linear-gradient(to bottom, ${scanlines.color} 0px, ${scanlines.color} ${scanlines.lineHeightPx}px, transparent ${scanlines.lineHeightPx}px, transparent ${scanlines.periodPx}px)`,
				}}
			/>
			<div style={{position: 'absolute', left: pad, top: pad, width: face.width - 2 * pad, height: face.height - 2 * pad}}>
				<FxContent
					data={event.data}
					t={t - event.start - T.leadS}
					box={{width: face.width - 2 * pad, height: face.height - 2 * pad}}
					maxScale={T.contentScale}
				/>
			</div>
			{flash > 0 ? <div style={{position: 'absolute', inset: 0, backgroundColor: T.enter.flash.color, opacity: flash}} /> : null}
		</div>
	);
};

// --- placement B: popped out -------------------------------------------------------------

const PO = FX.placements.popout;

// The settled card in output pixels: widthRatio of the frame wide, centred on x and centerY.
const popoutBox = (frameWidth: number) => {
	const width = frameWidth * PO.widthRatio;
	return {x: (frameWidth - width) / 2, y: PO.centerY - PO.height / 2, width, height: PO.height};
};

// The settled card's bounding box, its tilt included.
export const popoutBounds = (frameWidth: number): Box => {
	const b = popoutBox(frameWidth);
	const a = (Math.abs(PO.tiltDeg) * Math.PI) / 180;
	const hw = (b.width / 2) * Math.cos(a) + (b.height / 2) * Math.sin(a);
	const hh = (b.height / 2) * Math.cos(a) + (b.width / 2) * Math.sin(a);
	const cx = b.x + b.width / 2;
	const cy = b.y + b.height / 2;
	return {left: cx - hw, top: cy - hh, right: cx + hw, bottom: cy + hh};
};

// Throws unless the settled card is inside PO.safe: below the top bar, inside the side
// margins and above the caption block at captionMaxLines lines; with `anchors`, also
// above the host's eyes (their outer circles at rest).
const checkPopoutBox = (frameWidth: number, frameHeight: number, anchors?: Anchors): void => {
	const s = PO.safe;
	const b = popoutBounds(frameWidth);
	const eyesTop = anchors ? Math.min(...faceBoxes(anchors, frameWidth, frameHeight).eyes.map((e) => e.top)) : Infinity;
	const captionTop = CAPTIONS.bottomY - s.captionMaxLines * CAPTIONS.fontSizePx * CAPTIONS.lineHeight;
	const limits: [string, boolean][] = [
		[`top ${b.top.toFixed(1)} < safe.topPx ${s.topPx}`, b.top < s.topPx],
		[`left ${b.left.toFixed(1)} < safe.sideMarginPx ${s.sideMarginPx}`, b.left < s.sideMarginPx],
		[`right ${b.right.toFixed(1)} > ${frameWidth - s.sideMarginPx} (width minus safe.sideMarginPx)`, b.right > frameWidth - s.sideMarginPx],
		[
			`bottom ${b.bottom.toFixed(1)} > ${(captionTop - s.captionClearancePx).toFixed(1)} (a ${s.captionMaxLines}-line caption's top minus safe.captionClearancePx)`,
			b.bottom > captionTop - s.captionClearancePx,
		],
		[`bottom ${b.bottom.toFixed(1)} > the host's eyes' top ${eyesTop.toFixed(1)}`, b.bottom > eyesTop],
	];
	const bad = limits.filter(([, fails]) => fails).map(([what]) => what);
	if (bad.length > 0) {
		throw new Error(`Fx popout card (fx.json placements.popout) is outside the safe area: ${bad.join('; ')}`);
	}
};

const OPENING = FX.opening;

// The frame-0 card (paragraph 1's, fx.json opening): out and settled from its start, so the
// fact reads on the first frame.
const isFrame0 = (event: FxEvent): boolean => OPENING.fromFrame0 && event.frame0 === true;

// How far out the card is at t: 0 on the TV's face, 1 settled (a little over 1 in the
// spring's overshoot); null when the event isn't showing. The frame-0 card is at 1 until it
// flies back.
const popoutProgress = (event: FxEvent, t: number, fps: number): number | null => {
	if (event.placement !== 'popout' || t < event.start || t >= event.end) {
		return null;
	}
	const c = {t: t - event.start, fps};
	const backAt = event.end - event.start - PO.back.durationS;
	if (c.t >= backAt) {
		return 1 - ramp(c, backAt, PO.back.durationS, Easing.in(Easing.cubic));
	}
	return isFrame0(event) ? 1 : pop(c, 0, PO.out.durationS, PO.out.damping);
};

// The frame-0 card's settle: its extra scale, from settle.fromScale to 1 (1 for other cards).
const settleScale = (event: FxEvent, t: number, fps: number): number => {
	const s = OPENING.settle;
	return isFrame0(event) ? lerp(s.fromScale, 1, pop({t: t - event.start, fps}, 0, s.durationS, s.damping)) : 1;
};

// The template's clock: from the card's lead after its start; the frame-0 card's template
// is at its end from the first frame.
const contentT = (event: FxEvent, t: number): number =>
	isFrame0(event) ? templateEndS(event.data) + (t - event.start) : t - event.start - PO.leadS;

// Over the room (after <Screen>, before the host): dims the background and the TV while the
// card is out, following the card's spring out and its flight back.
export const FxBackdrop: React.FC<{event: FxEvent}> = ({event}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const p = popoutProgress(event, frame / fps, fps);
	if (p === null || PO.dim.opacity <= 0) {
		return null;
	}
	return <AbsoluteFill style={{backgroundColor: PO.dim.color, opacity: PO.dim.opacity * Math.min(1, Math.max(0, p))}} />;
};

// In front of the scene (after the host, before the captions): the card springs out of the
// TV (from out.fromScale of the face, centred on it, squashed to the face's shape) to its
// box, tilting; the template plays on it, and at the end it flies back in the same way.
// The frame-0 card is already out, its template at its end, and only settles. Rides the
// camera drift like the TV. `camera` (remotion/src/camera.ts: p on screen = (x, y) + scale *
// (p - (x, y))) is the shot the scene is in: the card leaves and goes back into the TV's face
// where the shot shows it (the planner never cuts during a flight).
export const FxPopout: React.FC<{
	event: FxEvent;
	spec: ScreenSpec;
	drift: Drift;
	anchors?: Anchors;
	camera?: {scale: number; x: number; y: number};
}> = ({event, spec, drift, anchors, camera}) => {
	const frame = useCurrentFrame();
	const {fps, width, height} = useVideoConfig();
	useMemo(() => checkPopoutBox(width, height, anchors), [width, height, anchors]);
	const t = frame / fps;
	const p = popoutProgress(event, t, fps);
	if (p === null) {
		return null;
	}
	const b = popoutBox(width);
	const tv = faceRect(spec);
	const c = camera ?? {scale: 1, x: 0, y: 0};
	const face = {x: c.x + c.scale * (tv.x - c.x), y: c.y + c.scale * (tv.y - c.y), width: c.scale * tv.width, height: c.scale * tv.height};
	const lift = Math.min(1, Math.max(0, p));
	const from = PO.out.fromScale;
	const settle = settleScale(event, t, fps);
	const transform = [
		`translate(${lerp(face.x + face.width / 2 - (b.x + b.width / 2), 0, p)}px, ${lerp(face.y + face.height / 2 - (b.y + b.height / 2), 0, p)}px)`,
		`rotate(${lerp(0, PO.tiltDeg, p)}deg)`,
		`scale(${settle * lerp((from * face.width) / b.width, 1, p)}, ${settle * lerp((from * face.height) / b.height, 1, p)})`,
	].join(' ');
	const rim = PO.outlinePx + PO.border.px;
	const content = {width: b.width - 2 * (rim + PO.paddingPx), height: b.height - 2 * (rim + PO.paddingPx)};
	return (
		<div style={{position: 'absolute', inset: 0, transform: driftTransform(drift), transformOrigin: '50% 50%'}}>
			<div style={{position: 'absolute', left: b.x, top: b.y, width: b.width, height: b.height, transform, transformOrigin: '50% 50%'}}>
				<div
					style={{
						position: 'absolute',
						inset: 0,
						borderRadius: PO.radius,
						backgroundColor: PO.shadow.color,
						transform: `translate(${PO.shadow.dx * lift}px, ${PO.shadow.dy * lift}px)`,
					}}
				/>
				<div style={{position: 'absolute', inset: 0, borderRadius: PO.radius, backgroundColor: FX.colors.outline}} />
				{PO.border.px > 0 ? (
					<div
						style={{position: 'absolute', inset: PO.outlinePx, borderRadius: PO.radius - PO.outlinePx, backgroundColor: PO.border.color}}
					/>
				) : null}
				<div
					style={{
						position: 'absolute',
						inset: rim,
						borderRadius: PO.radius - rim,
						backgroundColor: PO.fill,
						boxShadow: PO.border.px > 0 ? `0 0 0 ${PO.border.innerOutlinePx}px ${FX.colors.outline}` : undefined,
						overflow: 'hidden',
					}}
				>
					<div style={{position: 'absolute', left: PO.paddingPx, top: PO.paddingPx, ...content}}>
						<FxContent data={event.data} t={contentT(event, t)} box={content} maxScale={PO.contentScale} />
					</div>
				</div>
			</div>
		</div>
	);
};
