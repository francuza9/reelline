import {useEffect, useMemo, useState} from 'react';
import {AbsoluteFill, cancelRender, continueRender, delayRender, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {captionLineHeights} from './fit';
import captionFonts from '@channel/graphics/captions.json';
import {CaptionChunk, CaptionWord} from './types';

// All tunable caption numbers. Pixel values are output pixels (1080x1920).
export const CAPTIONS = {
	// The channel's caption fonts (graphics/captions.json), loaded from the Remotion public dir. A face
	// is declared for its whole weight range, which stops Chrome synthesizing bold when fontWeight is
	// applied; sizeAdjust (CSS size-adjust) scales a fallback face to the first one's letter height.
	fontFamily: captionFonts.fontFamily,
	fonts: captionFonts.fonts,
	fontWeight: 600,
	fontSizePx: 96,
	lineHeight: 1.15,
	fill: '#FF7EB6',
	// The currently spoken word.
	activeFill: '#FDDB0A',
	stroke: '#14120C',
	// Centred on the glyph outline, so half of it lies outside the glyph.
	strokeEm: 0.09,
	// Bottom edge of the caption block. the host's eyes start at y ~1000 (a bit higher at
	// full breath), so the block grows upwards from just above them.
	bottomY: 960,
	// Same margin on both sides keeps the block centred while clearing the platform
	// UI on the right. A chunk whose widest line doesn't fit is scaled down.
	sideMarginPx: 120,
	pop: {fromScale: 0.85, durationS: 0.15, damping: 12},
	// A word stays highlighted across a gap shorter than this before the next word,
	// so the highlight doesn't flicker off between words.
	holdGapS: 0.15,
};

// Shared with the headline cards (Scene.tsx), which use the same fonts.
export const fontFaceCss = CAPTIONS.fonts
	.map(
		(f) =>
			`@font-face{font-family:'${f.family}';src:url('${staticFile(f.file)}') format('truetype');font-weight:${f.weight};size-adjust:${f.sizeAdjust};}`,
	)
	.join('\n');

const cssFont = (sizePx: number) => `${CAPTIONS.fontWeight} ${sizePx}px ${CAPTIONS.fontFamily}`;

export const useCaptionFonts = (): boolean => {
	const [handle] = useState(() => delayRender('Loading caption fonts'));
	const [ready, setReady] = useState(false);
	useEffect(() => {
		Promise.all(
			CAPTIONS.fonts.map(async (f) => {
				const faces = await document.fonts.load(`${CAPTIONS.fontWeight} ${CAPTIONS.fontSizePx}px '${f.family}'`, f.sample);
				if (faces.length === 0) {
					throw new Error(`Caption font ${f.family} (${f.file}) did not load`);
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

// Scale factor (<= 1) per block of lines so its widest line at fontPx, stroke
// included, fits between the side margins.
const fitScales = (blocks: string[][], fontPx: number, maxWidth: number): number[] => {
	const ctx = document.createElement('canvas').getContext('2d');
	if (!ctx) {
		throw new Error('No 2D canvas context for measuring captions');
	}
	ctx.font = cssFont(fontPx);
	const strokePx = CAPTIONS.strokeEm * fontPx;
	return blocks.map((lines) => {
		const widest = Math.max(...lines.map((line) => ctx.measureText(line).width));
		return Math.min(1, maxWidth / (widest + strokePx));
	});
};

// The caption pop-in: scale from CAPTIONS.pop.fromScale to 1, springing from startFrame.
const popScale = (frame: number, startFrame: number, fps: number): number => {
	const pop = spring({
		frame: frame - startFrame,
		fps,
		config: {damping: CAPTIONS.pop.damping},
		durationInFrames: Math.max(1, Math.round(CAPTIONS.pop.durationS * fps)),
	});
	return CAPTIONS.pop.fromScale + (1 - CAPTIONS.pop.fromScale) * pop;
};

// The caption block: centred between the side margins, bottom edge at bottomY.
const blockStyle = (fontPx: number, lineHeight: number | 'normal', scale: number, height: number): React.CSSProperties => ({
	position: 'absolute',
	left: CAPTIONS.sideMarginPx,
	right: CAPTIONS.sideMarginPx,
	bottom: height - CAPTIONS.bottomY,
	textAlign: 'center',
	// Longhands, not the `font` shorthand: it would reset lineHeight (fit.ts captionLineHeights()).
	fontFamily: CAPTIONS.fontFamily,
	fontWeight: CAPTIONS.fontWeight,
	fontSize: fontPx,
	lineHeight,
	color: CAPTIONS.fill,
	WebkitTextStroke: `${CAPTIONS.strokeEm}em ${CAPTIONS.stroke}`,
	paintOrder: 'stroke fill',
	transform: `scale(${scale})`,
	transformOrigin: '50% 50%',
});

// Index into `words` of the word being spoken at t, or -1.
const activeWord = (words: CaptionWord[], t: number): number =>
	words.findIndex((w, i) => {
		const next = words[i + 1];
		const end = next && next.start - w.end < CAPTIONS.holdGapS ? next.start : w.end;
		return t >= w.start && t < end;
	});

// `fromFrame0`: the first chunk is on from frame 0 at full size (no pop-in), its first
// word lit until it's spoken (the opening, remotion/src/opening.ts).
export const Captions: React.FC<{chunks: CaptionChunk[]; fromFrame0?: boolean}> = ({chunks, fromFrame0 = false}) => {
	const frame = useCurrentFrame();
	const {fps, width, height} = useVideoConfig();
	const fontsReady = useCaptionFonts();

	const maxWidth = width - 2 * CAPTIONS.sideMarginPx;
	const scales = useMemo(
		() =>
			fontsReady
				? fitScales(
						chunks.map((c) => c.lines.map((line) => line.map((w) => w.text).join(' '))),
						CAPTIONS.fontSizePx,
						maxWidth,
					)
				: null,
		[fontsReady, chunks, maxWidth],
	);

	const heights = useMemo(
		() => (scales ? captionLineHeights(scales.map((x) => CAPTIONS.fontSizePx * x), CAPTIONS.lineHeight) : null),
		[scales],
	);

	const t = frame / fps;
	const early = fromFrame0 && chunks.length > 0 && t < chunks[0].chunk_start;
	const index = early ? 0 : chunks.findIndex((c) => t >= c.chunk_start && t < c.chunk_end);
	const chunk = chunks[index];

	let content = null;
	if (scales && heights && chunk) {
		const scale = fromFrame0 && index === 0 ? 1 : popScale(frame, Math.ceil(chunk.chunk_start * fps), fps);
		const words = chunk.lines.flat();
		const active = fromFrame0 && index === 0 && words.length > 0 && t < words[0].start ? 0 : activeWord(words, t);
		// Index in `words` of each line's first word.
		const lineStart = chunk.lines.map((_, li) => chunk.lines.slice(0, li).flat().length);

		content = (
			<div style={blockStyle(CAPTIONS.fontSizePx * scales[index], heights[index], scale, height)}>
				{chunk.lines.map((line, li) => (
					<div key={li} style={{whiteSpace: 'nowrap'}}>
						{line.map((word, wi) => (
							<span key={wi}>
								{wi > 0 ? ' ' : null}
								<span style={lineStart[li] + wi === active ? {color: CAPTIONS.activeFill} : undefined}>
									{word.text}
								</span>
							</span>
						))}
					</div>
				))}
			</div>
		);
	}

	return (
		<AbsoluteFill>
			<style>{fontFaceCss}</style>
			{content}
		</AbsoluteFill>
	);
};
