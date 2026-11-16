import {useMemo} from 'react';
import {AbsoluteFill, useVideoConfig} from 'remotion';
import {CAPTIONS, fontFaceCss, useCaptionFonts} from './Captions';
import {fitHeadlineOrThrow} from './fit';
import {Background, TvStill, canvasMeasure, useDrift} from './Scene';
import {CoverProps} from './types';
import {Box, Host, faceBoxes, restMouth} from '@channel/remotion/Mascot';

// All tunable cover numbers. Pixel values are output pixels (1080x1920).
export const COVER = {
	// The profile grid crops the 9:16 frame to its central 3:4 (1080x1440). The
	// headline and the host's face must sit inside it, marginPx in from its edges.
	safeArea: {x: 0, y: 240, width: 1080, height: 1440},
	marginPx: 60,
	// Under the background image (only seen if it fails to cover the frame).
	backdrop: '#000000',
	headline: {
		// Caption style, much bigger.
		fontFamily: CAPTIONS.fontFamily,
		fontWeight: CAPTIONS.fontWeight,
		fill: CAPTIONS.fill,
		stroke: CAPTIONS.stroke,
		strokeEm: CAPTIONS.strokeEm,
		lineHeight: 1.1,
		maxLines: 3,
		// Largest size tried first, then shrunk in steps; below minFontPx the render fails.
		// 80, not 96: four long Georgian words (42 chars, job #66) need 92 for 3 lines.
		maxFontPx: 140,
		minFontPx: 80,
		stepPx: 4,
		// The text box spans the safe area's width inside the margins, from its top
		// margin down to bottomY (over the wall and the TV), centred in it vertically.
		bottomY: 960,
	},
	// The headline box must end at least this far above the host's eyes.
	eyeClearancePx: 24,
	// Laid over the TV face (image or idle) so the headline over it stays legible; null for none.
	tvDim: 'rgba(8, 6, 24, 0.45)' as string | null,
	// debugSafeArea: the safe area's outline, and its margin inset dashed.
	debug: {color: '#00FF66', strokePx: 4, dashPx: 16},
};

const cssFont = (sizePx: number) => `${COVER.headline.fontWeight} ${sizePx}px ${COVER.headline.fontFamily}`;

const inset = (b: Box, px: number): Box => ({left: b.left + px, top: b.top + px, right: b.right - px, bottom: b.bottom - px});
const inside = (b: Box, outer: Box): boolean =>
	b.left >= outer.left && b.top >= outer.top && b.right <= outer.right && b.bottom <= outer.bottom;
const fmt = (b: Box) => `(${[b.left, b.top, b.right, b.bottom].map(Math.round).join(', ')})`;

// The headline's text box, checked against the safe area and the host's face; throws
// if the layout config puts either out of place.
const layoutBoxes = (props: CoverProps, width: number, height: number): Box => {
	const s = COVER.safeArea;
	const safe = inset({left: s.x, top: s.y, right: s.x + s.width, bottom: s.y + s.height}, COVER.marginPx);
	const text: Box = {left: safe.left, top: safe.top, right: safe.right, bottom: COVER.headline.bottomY};
	const face = faceBoxes(props.anchors, width, height);
	for (const [what, box] of [['eye', face.eyes[0]], ['eye', face.eyes[1]], ['mouth', face.mouth]] as const) {
		if (!inside(box, safe)) {
			throw new Error(`Cover: the host's ${what} ${fmt(box)} is outside the safe area minus margins ${fmt(safe)}`);
		}
	}
	if (text.bottom <= text.top || !inside(text, safe)) {
		throw new Error(`Cover: headline box ${fmt(text)} is outside the safe area minus margins ${fmt(safe)}`);
	}
	const eyesTop = Math.min(...face.eyes.map((e) => e.top));
	if (text.bottom > eyesTop - COVER.eyeClearancePx) {
		throw new Error(
			`Cover: headline box bottom ${text.bottom} must be at least ${COVER.eyeClearancePx}px above the host's eyes (top ${Math.round(eyesTop)})`,
		);
	}
	return text;
};

const SafeAreaOverlay: React.FC = () => {
	const {safeArea: s, marginPx: m, debug: d} = COVER;
	const rect = (x: number, y: number, w: number, h: number, dashed: boolean) => (
		<rect
			x={x + d.strokePx / 2}
			y={y + d.strokePx / 2}
			width={w - d.strokePx}
			height={h - d.strokePx}
			fill="none"
			stroke={d.color}
			strokeWidth={d.strokePx}
			strokeDasharray={dashed ? `${d.dashPx} ${d.dashPx}` : undefined}
		/>
	);
	return (
		<AbsoluteFill>
			<svg width="100%" height="100%">
				{rect(s.x, s.y, s.width, s.height, false)}
				{rect(s.x + m, s.y + m, s.width - 2 * m, s.height - 2 * m, true)}
			</svg>
		</AbsoluteFill>
	);
};

// Cover still for the profile grid: the reel's scene, the TV on the reel's first
// image (or idle), the host at rest looking forward, and the headline over the wall.
// Never the reel's first frame; the reel is rendered separately.
export const Cover: React.FC<CoverProps> = (props) => {
	const {anchors, mood, background, screen, image, headline, units, debugSafeArea} = props;
	const fontsReady = useCaptionFonts();
	const drift = useDrift(background);
	const {width, height} = useVideoConfig();
	const box = useMemo(() => layoutBoxes(props, width, height), [props, width, height]);
	const fit = useMemo(() => {
		if (!fontsReady) {
			return null;
		}
		if (units.join(' ') !== headline.trim().split(/\s+/).join(' ')) {
			throw new Error(`Cover: units ${JSON.stringify(units)} don't spell the headline "${headline}"`);
		}
		const size = {width: box.right - box.left, height: box.bottom - box.top};
		return fitHeadlineOrThrow(units, size, COVER.headline, canvasMeasure(cssFont), 'Cover headline');
	}, [fontsReady, units, headline, box]);

	const h = COVER.headline;
	return (
		<AbsoluteFill style={{backgroundColor: COVER.backdrop}}>
			<style>{fontFaceCss}</style>
			<Background src={background} drift={drift} />
			<TvStill image={image} dim={COVER.tvDim} drift={drift} spec={screen} />
			<Host
				anchors={anchors}
				mouthImage={restMouth(anchors, mood)}
				bodyScaleY={1}
				angle={{left: 0, right: 0}}
				lid={0}
				pupil={[0, 0]}
			/>
			{fit ? (
				<div
					style={{
						position: 'absolute',
						left: box.left,
						top: box.top,
						width: box.right - box.left,
						height: box.bottom - box.top,
						display: 'flex',
						flexDirection: 'column',
						justifyContent: 'center',
						alignItems: 'center',
						textAlign: 'center',
						font: cssFont(fit.fontPx),
						lineHeight: h.lineHeight,
						color: h.fill,
						WebkitTextStroke: `${h.strokeEm}em ${h.stroke}`,
						paintOrder: 'stroke fill',
					}}
				>
					{fit.lines.map((line, i) => (
						<div key={i} style={{whiteSpace: 'nowrap'}}>
							{line}
						</div>
					))}
				</div>
			) : null}
			{debugSafeArea ? <SafeAreaOverlay /> : null}
		</AbsoluteFill>
	);
};
