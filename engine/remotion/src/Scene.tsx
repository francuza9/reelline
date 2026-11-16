import brand from '@channel/graphics/brand.json';
import {useEffect, useMemo, useState} from 'react';
import {
	AbsoluteFill,
	Easing,
	Img,
	cancelRender,
	continueRender,
	delayRender,
	interpolate,
	random,
	spring,
	staticFile,
	useCurrentFrame,
	useVideoConfig,
} from 'remotion';
import {AngleArt, quadTransform} from './angles';
import {CAPTIONS, fontFaceCss} from './Captions';
import {HeadlineFit, Measure, fitHeadlineOrThrow} from './fit';
import {Card, HeadlineCard, ScreenImage, ScreenSpec} from './types';

// All tunable scene numbers. Pixel values are output pixels (1080x1920).
export const SCENE = {
	// Background images live in the public dir under this folder.
	backgroundDir: 'backgrounds',
	drift: {
		seed: `${brand.seedPrefix}-scene-v1`,
		// Scale at the zoomed-out end of the move; the overscan keeps the image edges
		// out of frame while panning.
		baseScale: 1.02,
		// Scale change over the whole reel (zoom in or out, seeded).
		zoom: [0.01, 0.02],
		// Total pan distance over the reel, in a seeded direction. Clamped per frame
		// to what the current overscan allows.
		panPx: [8, 16],
	},
	// The TV's look. Its geometry (outer bezel box x, y, width, height, outline
	// included; corner radius, bezel and face radius), the screen-image tunables and the
	// idle face come from the `screen` prop: remotion/src/screen.json with the
	// background's sidecar merged over it (reelline/media/backgrounds.py), which the Python image
	// pipeline also reads (face size -> minimum image resolution).
	tv: {
		outlinePx: 9,
		faceOutlinePx: 6,
		outline: '#14120C',
		bezel: '#23212B',
		// A touch lighter than the three walls (#1C1B40, #25254E, #171B4C).
		face: '#34336A',
		// Flat diagonal glare band across the face. White at low opacity over the image, so
		// it can only lift pixels slightly, never darken them.
		glare: {color: '#FFFFFF', opacity: 0.05, widthRatio: 0.18, offsetRatio: 0.62},
		// Flat shadow on the wall, down and right of the TV.
		shadow: {dx: 10, dy: 14, color: 'rgba(0, 0, 0, 0.35)'},
		// Wall plate behind the TV, peeking out above its top edge.
		bracket: {width: 170, peekPx: 22, radius: 8},
		// Whether the TV rides the background's camera drift (it's mounted on that wall).
		followsDrift: true,
	},
	// Headline cards (@card) on the TV. Off: the screen shows paragraph images, or the
	// idle face between them. The full-screen card stays available for later use (number
	// and deadline cards); its fit check only runs when this is on.
	showCards: false,
	headline: {
		fontFamily: CAPTIONS.fontFamily,
		fontWeight: CAPTIONS.fontWeight,
		fill: '#FF7EB6',
		stroke: '#14120C',
		strokeEm: 0.09,
		lineHeight: 1.15,
		maxLines: 3,
		// Largest size tried first, then shrunk in steps until the text fits the face.
		maxFontPx: 76,
		minFontPx: 36,
		stepPx: 2,
		// Inner padding of the text box inside the screen face.
		paddingPx: 30,
		in: {durationS: 0.2, fromScale: 0.8, fromYPx: 24, damping: 12},
		out: {durationS: 0.15},
	},
};

// The TV's geometry (from the spec) plus its look (SCENE.tv).
const tvOf = (spec: ScreenSpec) => ({...spec.screen, ...SCENE.tv});

// --- background --------------------------------------------------------------------

export type Drift = {scale: number; x: number; y: number};

// Seeded, eased camera drift over the reel: t in seconds, durationS the reel length.
const driftAt = (seed: string, t: number, durationS: number, width: number, height: number): Drift => {
	const {baseScale, zoom, panPx} = SCENE.drift;
	const amount = zoom[0] + (zoom[1] - zoom[0]) * random(`${seed}:zoom`);
	const zoomIn = random(`${seed}:direction`) < 0.5;
	const [scaleFrom, scaleTo] = zoomIn ? [baseScale, baseScale + amount] : [baseScale + amount, baseScale];
	const angle = 2 * Math.PI * random(`${seed}:pan-angle`);
	const pan = panPx[0] + (panPx[1] - panPx[0]) * random(`${seed}:pan`);

	const p = interpolate(t, [0, Math.max(durationS, 1e-3)], [0, 1], {
		easing: Easing.inOut(Easing.sin),
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});
	const scale = scaleFrom + (scaleTo - scaleFrom) * p;
	// Pan from -pan/2 to +pan/2 along the seeded direction.
	const along = (p - 0.5) * pan;
	const slackX = ((scale - 1) * width) / 2;
	const slackY = ((scale - 1) * height) / 2;
	const clamp = (v: number, m: number) => Math.max(-m, Math.min(m, v));
	return {scale, x: clamp(Math.cos(angle) * along, slackX), y: clamp(Math.sin(angle) * along, slackY)};
};

export const driftTransform = (d: Drift): string => `translate(${d.x}px, ${d.y}px) scale(${d.scale})`;

// Bottom of the stack: cover-fit scene image plus the seeded camera drift. `file`: a public-dir
// path instead of backgrounds/<src> (a room's 3/4 art, angles.json).
export const Background: React.FC<{src: string; drift: Drift; file?: string}> = ({src, drift, file}) => (
	<AbsoluteFill style={{transform: driftTransform(drift), transformOrigin: '50% 50%'}}>
		<Img
			src={staticFile(file ?? `${SCENE.backgroundDir}/${src}`)}
			style={{width: '100%', height: '100%', objectFit: 'cover'}}
		/>
	</AbsoluteFill>
);

export const useDrift = (background: string): Drift => {
	const frame = useCurrentFrame();
	const {fps, width, height, durationInFrames} = useVideoConfig();
	return driftAt(`${SCENE.drift.seed}:${background}`, frame / fps, durationInFrames / fps, width, height);
};

// --- screen prop ---------------------------------------------------------------------

// The face rectangle in output pixels (inside the bezel).
export const faceRect = (spec: ScreenSpec) => {
	const s = spec.screen;
	return {
		x: s.x + s.bezelPx,
		y: s.y + s.bezelPx,
		width: s.width - 2 * s.bezelPx,
		height: s.height - 2 * s.bezelPx,
	};
};

// Behind the screen image: shadow, wall plate, bezel and the empty face.
const TvBack: React.FC<{width: number; height: number; spec: ScreenSpec}> = ({width, height, spec}) => {
	const s = tvOf(spec);
	const face = faceRect(spec);
	// Strokes are centred on the path, so inset outlined shapes by half the stroke.
	const o = s.outlinePx / 2;
	const b = s.bracket;
	return (
		<svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{position: 'absolute', left: 0, top: 0}}>
			<rect
				x={s.x + s.shadow.dx}
				y={s.y + s.shadow.dy}
				width={s.width}
				height={s.height}
				rx={s.radius}
				fill={s.shadow.color}
			/>
			<rect
				x={width / 2 - b.width / 2 + o}
				y={s.y - b.peekPx + o}
				width={b.width - 2 * o}
				height={b.peekPx + s.radius}
				rx={b.radius}
				fill={s.bezel}
				stroke={s.outline}
				strokeWidth={s.outlinePx}
			/>
			<rect
				x={s.x + o}
				y={s.y + o}
				width={s.width - 2 * o}
				height={s.height - 2 * o}
				rx={s.radius - o}
				fill={s.bezel}
				stroke={s.outline}
				strokeWidth={s.outlinePx}
			/>
			<rect x={face.x} y={face.y} width={face.width} height={face.height} rx={s.faceRadius} fill={s.face} />
		</svg>
	);
};

// In front of the screen image: glare band and the face outline.
const TvFront: React.FC<{width: number; height: number; spec: ScreenSpec}> = ({width, height, spec}) => {
	const s = tvOf(spec);
	const face = faceRect(spec);
	const fo = s.faceOutlinePx / 2;
	const g = s.glare;
	const glareW = face.width * g.widthRatio;
	const glareX = face.x + face.width * g.offsetRatio;
	const slant = face.height * 0.35;
	return (
		<svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{position: 'absolute', left: 0, top: 0}}>
			<defs>
				<clipPath id="screen-face">
					<rect x={face.x} y={face.y} width={face.width} height={face.height} rx={s.faceRadius} />
				</clipPath>
			</defs>
			<polygon
				clipPath="url(#screen-face)"
				points={[
					[glareX + slant, face.y],
					[glareX + slant + glareW, face.y],
					[glareX + glareW - slant, face.y + face.height],
					[glareX - slant, face.y + face.height],
				]
					.map((p) => p.join(','))
					.join(' ')}
				fill={g.color}
				opacity={g.opacity}
			/>
			<rect
				x={face.x + fo}
				y={face.y + fo}
				width={face.width - 2 * fo}
				height={face.height - 2 * fo}
				rx={s.faceRadius - fo}
				fill="none"
				stroke={s.outline}
				strokeWidth={s.faceOutlinePx}
			/>
		</svg>
	);
};

// --- headline card -------------------------------------------------------------------

const cssFont = (sizePx: number) => `${SCENE.headline.fontWeight} ${sizePx}px ${SCENE.headline.fontFamily}`;

// the host + Fredoka (captions' fonts), for the idle mark and, when shown, headline cards.
const useScreenFonts = (): boolean => {
	const [handle] = useState(() => delayRender('Loading screen fonts'));
	const [ready, setReady] = useState(false);
	useEffect(() => {
		Promise.all(
			CAPTIONS.fonts.map(async (f) => {
				const faces = await document.fonts.load(`${SCENE.headline.fontWeight} ${SCENE.headline.maxFontPx}px '${f.family}'`, f.sample);
				if (faces.length === 0) {
					throw new Error(`Screen font ${f.family} (${f.file}) did not load`);
				}
			}),
		)
			.then(() => {
				setReady(true);
				continueRender(handle);
			})
			.catch((err) => cancelRender(err));
	}, [handle]);
	return ready;
};

type HeadlineLayout = HeadlineFit;

// Text box of the card: the face minus padding.
const cardBox = (spec: ScreenSpec) => {
	const face = faceRect(spec);
	const pad = SCENE.headline.paddingPx;
	return {left: face.x + pad, top: face.y + pad, width: face.width - 2 * pad, height: face.height - 2 * pad};
};

// Width of `text` at `fontPx` in the headline font, measured on a canvas.
export const canvasMeasure = (fontFor: (fontPx: number) => string): Measure => {
	const ctx = document.createElement('canvas').getContext('2d');
	if (!ctx) {
		throw new Error('No 2D canvas context for measuring headlines');
	}
	return (text, fontPx) => {
		ctx.font = fontFor(fontPx);
		return ctx.measureText(text).width;
	};
};

// Largest font size (stepping down from maxFontPx) at which the card's units wrap
// into maxLines lines that fit the card box.
const layoutHeadline = (card: HeadlineCard, spec: ScreenSpec): HeadlineLayout =>
	fitHeadlineOrThrow(card.units ?? card.text.trim().split(/\s+/), cardBox(spec), SCENE.headline, canvasMeasure(cssFont), 'Headline card');

const Headline: React.FC<{card: HeadlineCard; layout: HeadlineLayout; spec: ScreenSpec}> = ({card, layout, spec}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const h = SCENE.headline;
	const t = frame / fps;

	const pop = spring({
		frame: frame - Math.ceil(card.start * fps),
		fps,
		config: {damping: h.in.damping},
		durationInFrames: Math.max(1, Math.round(h.in.durationS * fps)),
	});
	const fadeIn = interpolate(t, [card.start, card.start + h.in.durationS / 2], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});
	const fadeOut = interpolate(t, [card.end - h.out.durationS, card.end], [1, 0], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});
	const scale = h.in.fromScale + (1 - h.in.fromScale) * pop;
	const dy = h.in.fromYPx * (1 - pop);

	return (
		<div
			style={{
				position: 'absolute',
				...cardBox(spec),
				display: 'flex',
				flexDirection: 'column',
				justifyContent: 'center',
				alignItems: 'center',
				textAlign: 'center',
				font: cssFont(layout.fontPx),
				lineHeight: h.lineHeight,
				color: h.fill,
				WebkitTextStroke: `${h.strokeEm}em ${h.stroke}`,
				paintOrder: 'stroke fill',
				opacity: Math.min(fadeIn, fadeOut),
				transform: `translateY(${dy}px) scale(${scale})`,
			}}
		>
			{layout.lines.map((line, i) => (
				<div key={i} style={{whiteSpace: 'nowrap'}}>
					{line}
				</div>
			))}
		</div>
	);
};

const CardView: React.FC<{card: Card; layout: HeadlineLayout; spec: ScreenSpec}> = ({card, layout, spec}) => {
	switch (card.type) {
		case 'headline':
			return <Headline card={card} layout={layout} spec={spec} />;
	}
};

// --- screen image --------------------------------------------------------------------

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

// A box exactly over the screen face, with its rounded corners.
const faceBox = (spec: ScreenSpec): React.CSSProperties => {
	const face = faceRect(spec);
	return {
		position: 'absolute',
		left: face.x,
		top: face.y,
		width: face.width,
		height: face.height,
		borderRadius: spec.screen.faceRadius,
		overflow: 'hidden',
	};
};

// Opacity of images[i] at t: fades in over crossfadeFrames from its start. When the
// next image starts where this one ends, it stays opaque underneath while the next
// fades in (a crossfade); otherwise it fades out over crossfadeFrames after its end.
const imageOpacity = (images: ScreenImage[], i: number, t: number, fps: number, spec: ScreenSpec): number => {
	const cf = spec.image.crossfadeFrames / fps;
	const image = images[i];
	const next = images[i + 1];
	if (t < image.start) {
		return 0;
	}
	const fadeIn = interpolate(t, [image.start, image.start + cf], [0, 1], clamp);
	const adjacent = next !== undefined && Math.abs(next.start - image.end) < 1 / fps;
	const fadeOut = adjacent ? (t < image.end + cf ? 1 : 0) : interpolate(t, [image.end, image.end + cf], [1, 0], clamp);
	return Math.min(fadeIn, fadeOut);
};

// Face shown when no paragraph image is on: a dark panel with faint scanlines and a
// small idle mark. Always drawn under the images, so their fades crossfade with it.
const IdleFace: React.FC<{spec: ScreenSpec}> = ({spec}) => {
	const {panel, scanlines, mark} = spec.idle;
	return (
		<div style={{...faceBox(spec), backgroundColor: panel, display: 'flex', justifyContent: 'center', alignItems: 'center'}}>
			<div
				style={{
					position: 'absolute',
					inset: 0,
					backgroundImage: `repeating-linear-gradient(to bottom, ${scanlines.color} 0px, ${scanlines.color} ${scanlines.lineHeightPx}px, transparent ${scanlines.lineHeightPx}px, transparent ${scanlines.periodPx}px)`,
				}}
			/>
			<div
				style={{
					position: 'relative',
					fontFamily: mark.fontFamily,
					fontWeight: mark.fontWeight,
					fontSize: mark.fontPx,
					letterSpacing: `${mark.letterSpacingEm}em`,
					color: mark.color,
					opacity: mark.opacity,
					whiteSpace: 'nowrap',
				}}
			>
				{mark.text || brand.mark}
			</div>
		</div>
	);
};

// Photo: fills the face with the manifest crop, slow seeded Ken Burns zoom toward the
// focal point. Logo: centred on its panel colour, padded, a very small zoom.
const ImageFace: React.FC<{image: ScreenImage; opacity: number; t: number; spec: ScreenSpec}> = ({image, opacity, t, spec}) => {
	const face = faceRect(spec);
	const cfg = spec.image;
	const p = interpolate(t, [image.start, Math.max(image.end, image.start + 1e-3)], [0, 1], {...clamp, easing: Easing.inOut(Easing.sin)});
	let content: React.ReactNode;
	if (image.mode === 'cover') {
		const {crop, focal} = image;
		if (!crop || !focal) {
			throw new Error(`Screen image ${image.src}: cover mode needs crop and focal`);
		}
		const s = Math.max(face.width / (crop.w * image.width), face.height / (crop.h * image.height));
		const kb = cfg.kenBurns;
		const ox = clamp01(focal.x + (2 * random(`${kb.seed}:${image.seed}:x`) - 1) * kb.originJitter);
		const oy = clamp01(focal.y + (2 * random(`${kb.seed}:${image.seed}:y`) - 1) * kb.originJitter);
		content = (
			<div
				style={{
					position: 'absolute',
					inset: 0,
					transform: `scale(${1 + (kb.zoomTo - 1) * p})`,
					transformOrigin: `${ox * 100}% ${oy * 100}%`,
				}}
			>
				<Img
					src={staticFile(image.src)}
					style={{
						position: 'absolute',
						left: -crop.x * image.width * s,
						top: -crop.y * image.height * s,
						width: image.width * s,
						height: image.height * s,
						maxWidth: 'none',
					}}
				/>
			</div>
		);
	} else {
		if (!image.panelColor) {
			throw new Error(`Screen image ${image.src}: contain mode needs a panel colour`);
		}
		const pad = cfg.logo.paddingPx;
		content = (
			<div style={{position: 'absolute', inset: 0, backgroundColor: image.panelColor}}>
				<div style={{position: 'absolute', inset: pad, transform: `scale(${1 + (cfg.logo.zoomTo - 1) * p})`}}>
					<Img src={staticFile(image.src)} style={{width: '100%', height: '100%', objectFit: 'contain'}} />
				</div>
			</div>
		);
	}
	const v = cfg.vignette;
	return (
		<div style={{...faceBox(spec), opacity}}>
			<div style={{position: 'absolute', inset: 0, filter: `brightness(${cfg.brightness}) saturate(${cfg.saturate})`}}>{content}</div>
			<div style={{position: 'absolute', inset: 0, boxShadow: `inset 0 0 ${v.blurPx}px ${v.spreadPx}px ${v.color}`}} />
		</div>
	);
};

// A wash of `color` over the TV's face at `opacity` (the TV switching on at frame 0,
// remotion/src/motion.ts powerOnWash); a child of Screen, so under the glare.
export const FaceWash: React.FC<{spec: ScreenSpec; color: string; opacity: number}> = ({spec, color, opacity}) =>
	opacity > 0 ? <div style={{...faceBox(spec), backgroundColor: color, opacity}} /> : null;

// TV on the wall: the idle face, the paragraph images crossfading over it, and (only
// with SCENE.showCards) the active headline card on top. Sits in front of the
// background, behind the host and the captions. `children` go over the images, under the
// glare and the face outline (an fx on the TV, remotion/src/Fx.tsx).
export const Screen: React.FC<{
	cards: Card[];
	images: ScreenImage[];
	drift: Drift;
	spec: ScreenSpec;
	children?: React.ReactNode;
}> = ({cards, images, drift, spec, children}) => {
	const frame = useCurrentFrame();
	const {fps, width, height} = useVideoConfig();
	const fontsReady = useScreenFonts();

	// Cards are measured (and fail loudly if they don't fit) only when they're shown.
	const layouts = useMemo(
		(): HeadlineLayout[] | null => (SCENE.showCards && fontsReady ? cards.map((c) => layoutHeadline(c, spec)) : null),
		[fontsReady, cards, spec],
	);

	const t = frame / fps;
	const opacities = images.map((_, i) => imageOpacity(images, i, t, fps, spec));
	const index = cards.findIndex((c) => t >= c.start && t < c.end);

	return (
		<AbsoluteFill
			style={SCENE.tv.followsDrift ? {transform: driftTransform(drift), transformOrigin: '50% 50%'} : undefined}
		>
			<style>{fontFaceCss}</style>
			<TvBack width={width} height={height} spec={spec} />
			<IdleFace spec={spec} />
			{images.map((image, i) =>
				opacities[i] > 0 ? <ImageFace key={i} image={image} opacity={opacities[i]} t={t} spec={spec} /> : null,
			)}
			{children}
			<TvFront width={width} height={height} spec={spec} />
			{layouts && index >= 0 ? <CardView card={cards[index]} layout={layouts[index]} spec={spec} /> : null}
		</AbsoluteFill>
	);
};

// The wall TV in a room's 3/4 art (angles.json backgrounds): its frame quad in the TV's outline
// colour, and the front TV's face (the idle face, the pictures crossfading over it, `children`,
// the glare and the face outline) mapped onto the face quad in perspective.
export const AngledScreen: React.FC<{
	images: ScreenImage[];
	drift: Drift;
	spec: ScreenSpec;
	art: AngleArt;
	children?: React.ReactNode;
}> = ({images, drift, spec, art, children}) => {
	const frame = useCurrentFrame();
	const {fps, width, height} = useVideoConfig();
	useScreenFonts();
	const t = frame / fps;
	const f = faceRect(spec);
	const corners = [
		[f.x, f.y],
		[f.x + f.width, f.y],
		[f.x + f.width, f.y + f.height],
		[f.x, f.y + f.height],
	];
	return (
		<AbsoluteFill
			style={SCENE.tv.followsDrift ? {transform: driftTransform(drift), transformOrigin: '50% 50%'} : undefined}
		>
			<style>{fontFaceCss}</style>
			<svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{position: 'absolute', left: 0, top: 0}}>
				<polygon points={art.frame.map((p) => p.join(',')).join(' ')} fill={SCENE.tv.outline} />
			</svg>
			<AbsoluteFill style={{transform: quadTransform(corners, art.face), transformOrigin: '0 0'}}>
				<IdleFace spec={spec} />
				{images.map((image, i) => {
					const opacity = imageOpacity(images, i, t, fps, spec);
					return opacity > 0 ? <ImageFace key={i} image={image} opacity={opacity} t={t} spec={spec} /> : null;
				})}
				{children}
				<TvFront width={width} height={height} spec={spec} />
			</AbsoluteFill>
		</AbsoluteFill>
	);
};

// The TV frozen for a still (the cover): `image` at its first frame (no fade, Ken
// Burns at its start), or the idle face without one; `dim` (a CSS colour, or null)
// laid over the face under the glare, to keep text over the TV legible.
export const TvStill: React.FC<{image: ScreenImage | null; dim: string | null; drift: Drift; spec: ScreenSpec}> = ({
	image,
	dim,
	drift,
	spec,
}) => {
	const {width, height} = useVideoConfig();
	// Holds the render until the idle mark's font is in.
	useScreenFonts();
	return (
		<AbsoluteFill
			style={SCENE.tv.followsDrift ? {transform: driftTransform(drift), transformOrigin: '50% 50%'} : undefined}
		>
			<style>{fontFaceCss}</style>
			<TvBack width={width} height={height} spec={spec} />
			<IdleFace spec={spec} />
			{image ? <ImageFace image={image} opacity={1} t={image.start} spec={spec} /> : null}
			{dim ? <div style={{...faceBox(spec), backgroundColor: dim}} /> : null}
			<TvFront width={width} height={height} spec={spec} />
		</AbsoluteFill>
	);
};
