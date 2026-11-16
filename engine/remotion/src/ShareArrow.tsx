import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {CAPTIONS, fontFaceCss, useCaptionFonts} from './Captions';
import {Pt, arrowGeometry, headPath, lengthOf, pathOf, platformSpec} from './arrow';
import shareSpec from './share.json';
import {ShareProps} from './types';

// The share arrow: while the host says the share line ("send this to your friend who..."), a
// hand-drawn arrow in his colours points at the platform's own share button (reelline/media/share.py
// times it; scripts/render_reel.py renders only its stretch once per platform). It draws itself
// along its curve, its head pops, it nudges toward the button until the line ends, then shrinks
// into the button. All numbers are in share.json (shared with Python); the drawing is here.
export const SHARE = shareSpec;

const springAt = (t: number, atS: number, durationS: number, damping: number, fps: number) =>
	t < atS ? 0 : spring({frame: (t - atS) * fps, fps, config: {damping}, durationInFrames: Math.max(1, Math.round(durationS * fps))});

export const ShareArrow: React.FC<{share: ShareProps | null | undefined}> = ({share}) => {
	const frame = useCurrentFrame();
	const {fps, width, height} = useVideoConfig();
	const fontsReady = useCaptionFonts();
	const t = frame / fps;
	if (!share || !share.platform || !SHARE.on || t < share.start || t >= share.end) {
		return null;
	}
	const {arrow: A, label: L, place} = platformSpec(SHARE, share.platform);
	const g = arrowGeometry(A, place);
	const dir: Pt = [Math.cos(g.angle), Math.sin(g.angle)];

	// Drawing along the curve, then the head's pop.
	const drawn = interpolate(t, [share.start, share.start + SHARE.drawS], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
		easing: Easing.inOut(Easing.cubic),
	});
	const along = drawn * g.length;
	const drawnAt = share.start + SHARE.drawS;
	const H = A.head;
	const headAt = drawnAt - H.popS * 0.3;
	const head = t < headAt ? 0 : H.fromScale + (1 - H.fromScale) * springAt(t, headAt, H.popS, H.damping, fps);

	// The nudge toward the button while the line goes on (from the head's pop), easing in.
	const tau = Math.max(0, t - drawnAt);
	const W = A.wiggle;
	const ease = Math.min(1, tau / 0.25);
	const nudge = ease * W.distancePx * (0.5 - 0.5 * Math.cos(2 * Math.PI * W.hz * tau));
	const sway = ease * W.rotateDeg * Math.sin(Math.PI * W.hz * tau);

	// The exit: shrinking into the button and fading after the line's last word.
	const out = interpolate(t, [share.lineEnd, share.end], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
		easing: Easing.in(Easing.cubic),
	});
	const E = A.exit;
	const shift = nudge + out * E.towardPx;
	const scale = 1 - out * (1 - E.scaleTo);
	const group = [
		`translate(${(shift * dir[0]).toFixed(2)} ${(shift * dir[1]).toFixed(2)})`,
		`translate(${g.tip[0].toFixed(2)} ${g.tip[1].toFixed(2)})`,
		`rotate(${sway.toFixed(3)})`,
		`scale(${scale.toFixed(4)})`,
		`translate(${(-g.tip[0]).toFixed(2)} ${(-g.tip[1]).toFixed(2)})`,
	].join(' ');

	const C = A.colors;
	const dashed = (visible: number, total: number) => `${Math.max(0, visible).toFixed(2)} ${(total + A.strokePx * 4).toFixed(2)}`;
	const common = {fill: 'none', strokeLinejoin: 'round' as const};
	const shaftD = pathOf(g.points);
	const shaft =
		along > 0 ? (
			<g>
				<path {...common} d={shaftD} strokeLinecap="round" strokeDasharray={dashed(along, g.length)} stroke={C.ink} strokeWidth={A.strokePx + 2 * A.outlinePx} />
				<path {...common} d={shaftD} strokeLinecap="round" strokeDasharray={dashed(along, g.length)} stroke={C.shaft} strokeWidth={A.strokePx} />
			</g>
		) : null;
	// The loop's later pass drawn again on top, so it crosses over the earlier one. Flat ends,
	// and the pink a sample longer than the ink at each end, so no seam shows where it joins.
	let over = null;
	if (g.over) {
		const [a, b] = g.over;
		const ink = g.points.slice(a, b);
		const pink = g.points.slice(a - 1, b + 1);
		const inkFrom = lengthOf(g.points.slice(0, a + 1));
		const pinkFrom = lengthOf(g.points.slice(0, a));
		if (along > inkFrom) {
			over = (
				<g>
					<path {...common} d={pathOf(ink)} strokeLinecap="butt" strokeDasharray={dashed(along - inkFrom, lengthOf(ink))} stroke={C.ink} strokeWidth={A.strokePx + 2 * A.outlinePx} />
					<path {...common} d={pathOf(pink)} strokeLinecap="butt" strokeDasharray={dashed(along - pinkFrom, lengthOf(pink))} stroke={C.shaft} strokeWidth={A.strokePx} />
				</g>
			);
		}
	}

	const labelPop = L.on && fontsReady ? springAt(t, drawnAt, L.popS, L.damping, fps) : 0;
	const labelScale = labelPop > 0 ? L.fromScale + (1 - L.fromScale) * labelPop : 0;
	const at: Pt = [g.tail[0] + L.offset[0], g.tail[1] + L.offset[1]];

	return (
		<AbsoluteFill style={{opacity: 1 - out}}>
			<style>{fontFaceCss}</style>
			<svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
				<g transform={group}>
					{shaft}
					{over}
					{head > 0 ? (
						<g transform={`translate(${g.end[0].toFixed(2)} ${g.end[1].toFixed(2)}) rotate(${((g.angle * 180) / Math.PI).toFixed(3)}) scale(${head.toFixed(4)})`}>
							<path
								d={headPath(H)}
								fill={C.head}
								stroke={C.ink}
								strokeWidth={2 * A.outlinePx}
								strokeLinejoin="round"
								style={{paintOrder: 'stroke fill'}}
							/>
						</g>
					) : null}
					{labelScale > 0 ? (
						<g transform={`translate(${at[0].toFixed(2)} ${at[1].toFixed(2)}) rotate(${L.rotateDeg}) scale(${labelScale.toFixed(4)})`}>
							<text
								x={0}
								y={0}
								textAnchor="middle"
								dominantBaseline="middle"
								fill={L.color}
								stroke={C.ink}
								strokeWidth={2 * CAPTIONS.strokeEm * L.fontPx}
								strokeLinejoin="round"
								style={{font: `${CAPTIONS.fontWeight} ${L.fontPx}px ${CAPTIONS.fontFamily}`, paintOrder: 'stroke fill'}}
							>
								{L.text}
							</text>
						</g>
					) : null}
				</g>
			</svg>
		</AbsoluteFill>
	);
};
