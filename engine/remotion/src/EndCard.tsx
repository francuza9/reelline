import brand from '@channel/graphics/brand.json';
import {useMemo} from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {CAPTIONS, fontFaceCss, useCaptionFonts} from './Captions';
import {FitSpec, HeadlineFit, Measure, StackBlock, fitStack} from './fit';
import outroSpec from '@channel/graphics/outro.json';
import {canvasMeasure} from './Scene';
import {EndCardFact, EndCardField, EndCardStillProps} from './types';

// The end card: the outro (reelline/media/outro.py), a still summary of the opportunity over the host's
// last line (the tag; his voice goes on under it), for viewers to pause and screenshot. A frame
// of its own: no room, no the host, no TV, no captions. It irises open over the scene, its blocks
// pop in one after another, then everything holds still. Its fields, footer and timing are in outro.json (shared with Python); the look is here.
export const OUTRO = outroSpec;

const C = {outline: '#14120C', yellow: '#FDDB0A', pink: '#FF7EB6', white: '#FFFFFF', coinSide: '#E0B000', light: '#DCDAF0'};

// All tunable card numbers. Pixel values are output pixels (1080x1920).
export const CARD = {
	// The wall's navy with a faint halftone of yellow dots.
	background: {fill: '#1F1E4A', dots: {color: 'rgba(253, 219, 10, 0.07)', radiusPx: 5, spacingPx: 46}},
	// The facts' box: below the platforms' top bar, above their bottom caption and buttons, the
	// cover's side margin; the stack is centred in it vertically. Everything must fit at the
	// blocks' minFontPx, or the render fails (layoutCard()).
	box: {left: 100, top: 250, width: 880, height: 1230},
	text: {fontWeight: CAPTIONS.fontWeight, strokeEm: CAPTIONS.strokeEm},
	// The program's name, then what it is: centred.
	name: {color: C.yellow, maxFontPx: 116, minFontPx: 60, stepPx: 4, maxLines: 2, lineHeight: 1.08},
	what: {color: C.pink, maxFontPx: 72, minFontPx: 44, stepPx: 2, maxLines: 2, lineHeight: 1.14},
	// Each other fact: a panel with its icon on the left, the text left-aligned beside it, on one
	// line down to oneLineMinFontPx, else on two; every row at the same size.
	row: {
		color: C.white,
		maxFontPx: 66,
		minFontPx: 40,
		oneLineMinFontPx: 46,
		stepPx: 2,
		maxLines: 2,
		lineHeight: 1.12,
		panel: {fill: '#34336A', outlinePx: 6, radiusPx: 30, padX: 22, padY: 18},
		icon: {sizePx: 96, gapPx: 24},
	},
	gaps: {afterName: 24, beforeRows: 48, betweenRows: 22},
	// OUTRO.footer, small, centred, its first line's centre at centerY.
	footer: {color: C.light, fontPx: 38, lineHeight: 1.25, centerY: 1560},
	// The channel's mark above the facts (the TV's idle mark, screen.json idle.mark).
	mark: {text: brand.mark, color: C.yellow, fontFamily: 'Fredoka', fontPx: 44, letterSpacingEm: 0.06, opacity: 0.9, centerY: 172},
	// The pop-in: a circle opening from (50%, originY) over durationS, then the mark, each block
	// and the footer pop, staggerS apart from firstS, springing from fromScale.
	reveal: {durationS: 0.22, originY: 0.45},
	pop: {firstS: 0.1, staggerS: 0.07, durationS: 0.3, fromScale: 0.6, damping: 11, fadeS: 0.08},
	// debugSafeArea: the facts' box, dashed.
	debug: {color: '#00FF66', strokePx: 4, dashPx: 16},
};

// When the card has settled (its last pop's end), in seconds from its start.
export const cardSettledS = (facts: number): number => CARD.pop.firstS + (facts + 1) * CARD.pop.staggerS + CARD.pop.durationS;

const cssFont = (px: number) => `${CARD.text.fontWeight} ${px}px ${CAPTIONS.fontFamily}`;

type Kind = 'name' | 'what' | 'row';
const kindOf = (field: EndCardField): Kind => (field === 'name' || field === 'what' ? field : 'row');

const specOf = (kind: Kind): FitSpec => {
	const s = CARD[kind];
	return {maxFontPx: s.maxFontPx, minFontPx: s.minFontPx, stepPx: s.stepPx, maxLines: s.maxLines, lineHeight: s.lineHeight, strokeEm: CARD.text.strokeEm};
};

const rowTextWidth = () => CARD.box.width - 2 * CARD.row.panel.padX - CARD.row.icon.sizePx - CARD.row.icon.gapPx;

export type PlacedFact = {fact: EndCardFact; kind: Kind; top: number; height: number; fit: HeadlineFit};

// The facts stacked in the box (fit.ts fitStack: shrunk together until they fit), centred
// vertically; throws when they don't fit at the smallest sizes.
export const layoutCard = (facts: EndCardFact[], measure: Measure): PlacedFact[] => {
	const blocks: StackBlock[] = facts.map((f, i) => {
		const kind = kindOf(f.field);
		const prev = i > 0 ? kindOf(facts[i - 1].field) : null;
		const gap = prev === null ? 0 : kind !== 'row' ? CARD.gaps.afterName : prev === 'row' ? CARD.gaps.betweenRows : CARD.gaps.beforeRows;
		const row = kind === 'row';
		return {
			units: f.text.split(' ').filter((u) => u.length > 0),
			spec: specOf(kind),
			width: row ? rowTextWidth() : CARD.box.width,
			padY: row ? 2 * CARD.row.panel.padY : 0,
			minHeight: row ? CARD.row.icon.sizePx + 2 * CARD.row.panel.padY : 0,
			gapBefore: gap,
			oneLineMinFontPx: row ? CARD.row.oneLineMinFontPx : undefined,
			group: row ? 'rows' : undefined,
		};
	});
	const fit = fitStack(blocks, CARD.box.height, measure);
	if (!fit) {
		throw new Error(
			`End card: the facts don't fit ${CARD.box.width}x${CARD.box.height}px even at the smallest sizes: ` +
				facts.map((f) => `${f.field} "${f.text}"`).join(', '),
		);
	}
	let y = CARD.box.top + (CARD.box.height - fit.height) / 2;
	return facts.map((fact, i) => {
		y += blocks[i].gapBefore;
		const top = y;
		y += fit.heights[i];
		return {fact, kind: kindOf(fact.field), top, height: fit.heights[i], fit: fit.blocks[i]};
	});
};

// --- icons ------------------------------------------------------------------------------

const stroke = {stroke: C.outline, strokeWidth: 5, strokeLinejoin: 'round' as const, strokeLinecap: 'round' as const};

// A flat badge of `size` with the field's glyph, drawn on a 100 x 100 grid.
const Icon: React.FC<{field: EndCardField; size: number}> = ({field, size}) => {
	const badge = field === 'deadline' || field === 'apply' ? C.yellow : C.pink;
	let glyph: React.ReactNode = null;
	if (field === 'who') {
		glyph = (
			<g>
				<path d="M 26 80 Q 26 56 50 56 Q 74 56 74 80 Z" fill={C.white} {...stroke} />
				<circle cx={50} cy={37} r={14} fill={C.white} {...stroke} />
			</g>
		);
	} else if (field === 'deadline') {
		glyph = (
			<g>
				<rect x={22} y={26} width={56} height={52} rx={7} fill={C.white} {...stroke} />
				<path d="M 22 33 Q 22 26 29 26 L 71 26 Q 78 26 78 33 L 78 42 L 22 42 Z" fill={C.pink} {...stroke} />
				{[36, 64].map((x) => (
					<line key={x} x1={x} y1={18} x2={x} y2={31} stroke={C.outline} strokeWidth={6} strokeLinecap="round" />
				))}
				{[34, 50, 66].flatMap((x) => [53, 66].map((y) => <rect key={`${x}:${y}`} x={x - 4} y={y - 4} width={8} height={8} rx={2} fill={C.outline} />))}
			</g>
		);
	} else if (field === 'cost') {
		glyph = (
			<g>
				<circle cx={50} cy={50} r={27} fill={C.yellow} {...stroke} />
				<circle cx={50} cy={50} r={17} fill="none" stroke={C.coinSide} strokeWidth={5} />
			</g>
		);
	} else if (field === 'apply') {
		glyph = (
			<g>
				<circle cx={50} cy={50} r={27} fill={C.white} {...stroke} />
				<ellipse cx={50} cy={50} rx={12} ry={27} fill="none" {...stroke} />
				<line x1={23} y1={50} x2={77} y2={50} {...stroke} />
				<path d="M 28 36 Q 50 42 72 36 M 28 64 Q 50 58 72 64" fill="none" {...stroke} strokeWidth={4} />
			</g>
		);
	}
	return (
		<svg width={size} height={size} viewBox="0 0 100 100" style={{display: 'block', overflow: 'visible'}}>
			<circle cx={50} cy={50} r={47} fill={badge} {...stroke} strokeWidth={6} />
			{glyph}
		</svg>
	);
};

// --- the card ---------------------------------------------------------------------------

const textStyle = (px: number, color: string, lineHeight: number): React.CSSProperties => ({
	font: cssFont(px),
	lineHeight,
	color,
	WebkitTextStroke: `${CARD.text.strokeEm}em ${C.outline}`,
	paintOrder: 'stroke fill',
});

const Lines: React.FC<{lines: string[]}> = ({lines}) => (
	<>
		{lines.map((line, i) => (
			<div key={i} style={{whiteSpace: 'nowrap'}}>
				{line}
			</div>
		))}
	</>
);

// Scale and opacity of a pop at `atS`, t seconds into the card.
const popAt = (t: number, atS: number, fps: number) => {
	const P = CARD.pop;
	const s = spring({frame: (t - atS) * fps, fps, config: {damping: P.damping}, durationInFrames: Math.max(1, Math.round(P.durationS * fps))});
	const opacity = interpolate(t, [atS, atS + P.fadeS], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
	return {scale: t < atS ? 0 : P.fromScale + (1 - P.fromScale) * s, opacity};
};

const Block: React.FC<{p: PlacedFact; t: number; atS: number; fps: number}> = ({p, t, atS, fps}) => {
	const {scale, opacity} = popAt(t, atS, fps);
	if (scale <= 0) {
		return null;
	}
	const box = CARD.box;
	const frame: React.CSSProperties = {position: 'absolute', left: box.left, top: p.top, width: box.width, height: p.height, opacity, transform: `scale(${scale})`, transformOrigin: '50% 50%'};
	if (p.kind !== 'row') {
		const s = CARD[p.kind];
		return (
			<div style={{...frame, textAlign: 'center', ...textStyle(p.fit.fontPx, s.color, s.lineHeight)}}>
				<Lines lines={p.fit.lines} />
			</div>
		);
	}
	const R = CARD.row;
	return (
		<div
			style={{
				...frame,
				boxSizing: 'border-box',
				backgroundColor: R.panel.fill,
				border: `${R.panel.outlinePx}px solid ${C.outline}`,
				borderRadius: R.panel.radiusPx,
				display: 'flex',
				alignItems: 'center',
				paddingLeft: R.panel.padX - R.panel.outlinePx,
			}}
		>
			<div style={{flex: 'none', marginRight: R.icon.gapPx}}>
				<Icon field={p.fact.field} size={R.icon.sizePx} />
			</div>
			<div style={{textAlign: 'left', ...textStyle(p.fit.fontPx, R.color, R.lineHeight)}}>
				<Lines lines={p.fit.lines} />
			</div>
		</div>
	);
};

// The card at t seconds from its start (Reel.tsx passes its Sequence's time; a still, a late t).
export const EndCard: React.FC<{facts: EndCardFact[]; t?: number; debugSafeArea?: boolean}> = ({facts, t: tOverride, debugSafeArea = false}) => {
	const frame = useCurrentFrame();
	const {fps, width, height} = useVideoConfig();
	const fontsReady = useCaptionFonts();
	const placed = useMemo(() => (fontsReady ? layoutCard(facts, canvasMeasure(cssFont)) : null), [fontsReady, facts]);
	const t = tOverride ?? frame / fps;

	const R = CARD.reveal;
	const open = interpolate(t, [0, R.durationS], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
	const radius = open * Math.hypot(width, height);
	const P = CARD.pop;
	const markPop = popAt(t, P.firstS, fps);
	const footerPop = popAt(t, P.firstS + (facts.length + 1) * P.staggerS, fps);
	const B = CARD.background;
	const M = CARD.mark;
	const F = CARD.footer;
	return (
		<AbsoluteFill
			style={{
				backgroundColor: B.fill,
				backgroundImage: `radial-gradient(circle, ${B.dots.color} ${B.dots.radiusPx}px, transparent ${B.dots.radiusPx + 1}px)`,
				backgroundSize: `${B.dots.spacingPx}px ${B.dots.spacingPx}px`,
				clipPath: open >= 1 ? undefined : `circle(${radius}px at 50% ${R.originY * 100}%)`,
			}}
		>
			<style>{fontFaceCss}</style>
			{markPop.scale > 0 ? (
				<div
					style={{
						position: 'absolute',
						left: 0,
						width,
						top: M.centerY - M.fontPx / 2,
						textAlign: 'center',
						fontFamily: M.fontFamily,
						fontWeight: CARD.text.fontWeight,
						fontSize: M.fontPx,
						lineHeight: 1,
						letterSpacing: `${M.letterSpacingEm}em`,
						color: M.color,
						opacity: M.opacity * markPop.opacity,
						transform: `scale(${markPop.scale})`,
					}}
				>
					{M.text}
				</div>
			) : null}
			{placed ? placed.map((p, i) => <Block key={i} p={p} t={t} atS={P.firstS + (i + 1) * P.staggerS} fps={fps} />) : null}
			{footerPop.scale > 0 ? (
				<div
					style={{
						position: 'absolute',
						left: 0,
						width,
						top: F.centerY - (F.fontPx * F.lineHeight) / 2,
						textAlign: 'center',
						...textStyle(F.fontPx, F.color, F.lineHeight),
						opacity: footerPop.opacity,
						transform: `scale(${footerPop.scale})`,
					}}
				>
					<Lines lines={OUTRO.footer} />
				</div>
			) : null}
			{debugSafeArea ? (
				<div
					style={{
						position: 'absolute',
						left: CARD.box.left,
						top: CARD.box.top,
						width: CARD.box.width,
						height: CARD.box.height,
						boxSizing: 'border-box',
						border: `${CARD.debug.strokePx}px dashed ${CARD.debug.color}`,
					}}
				/>
			) : null}
		</AbsoluteFill>
	);
};

// The card as it holds, for a design check (scripts/render_endcard_preview.py).
export const EndCardStill: React.FC<EndCardStillProps> = ({facts, debugSafeArea}) => (
	<EndCard facts={facts} t={cardSettledS(facts.length) + 1} debugSafeArea={debugSafeArea} />
);
