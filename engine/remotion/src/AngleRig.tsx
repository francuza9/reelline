import {Img, staticFile} from 'remotion';
import {Ellipse, RigEye, RigSpec} from './angles';
import {Side} from './rig';

// the host in another camera angle (angles.json rigs): one image, chair included, in its art's
// pixels, placed on the reel by rig.placement. The chair's base below seatY stays put; the
// body above it breathes and bounces (vertical only, pivoting on seatY, so the cut is
// seamless); the stalks above cutY sway and stretch like the front rig's (Mascot.tsx), each with
// its eye; the mouth is lip-synced from rig.mouth.dir; the eyes are baked, so the rig paints
// each pupil over (where it looks) and the lid, inside the eye white, in the stalk's own layer.

// The body and stalk layers reach this many rows past their cut, so they never leave a hairline.
const OVERLAP_PX = 2;

const ellipse = (e: Ellipse, grow = 0, dx = 0, dy = 0, k = 1) => ({
	cx: e.cx + dx,
	cy: e.cy + dy,
	rx: (e.rx + grow) * k,
	ry: (e.ry + grow) * k,
	transform: `rotate(${e.rotate} ${e.cx + dx} ${e.cy + dy})`,
});

// Half the eye white's height (an ellipse rotated by `rotate` degrees).
const halfHeight = (e: Ellipse): number => {
	const a = (e.rotate * Math.PI) / 180;
	return Math.sqrt((e.rx * Math.sin(a)) ** 2 + (e.ry * Math.cos(a)) ** 2);
};

const RigEyeView: React.FC<{
	id: string;
	eye: RigEye;
	rig: RigSpec;
	lid: number;
	look: [number, number];
	pupilScale: number;
}> = ({id, eye, rig, lid, look, pupilScale}) => {
	const colors = rig.colors ?? {yellow: '#FDDB02', outline: '#080700', white: '#FFFFFF'};
	const travel = rig.pupilTravel ?? 0;
	const [dx, dy] = [look[0] * travel, look[1] * travel];
	const {white, pupil, highlight} = eye;
	const hh = halfHeight(white);
	const reach = Math.max(white.rx, white.ry) + 4;
	const top = white.cy - hh;
	const edge = top + lid * 2 * hh;
	return (
		<g>
			<defs>
				<clipPath id={id}>
					<ellipse {...ellipse(white)} />
				</clipPath>
			</defs>
			<g clipPath={`url(#${id})`}>
				{/* The art's pupil painted over, then drawn where the eyes look. */}
				<ellipse {...ellipse(pupil, rig.coverPx ?? 3)} fill={colors.white} />
				<ellipse {...ellipse(pupil, 0, dx, dy, pupilScale)} fill="#000000" />
				<circle
					cx={pupil.cx + dx + (highlight.cx - pupil.cx) * pupilScale}
					cy={pupil.cy + dy + (highlight.cy - pupil.cy) * pupilScale}
					r={highlight.r * pupilScale}
					fill={colors.white}
				/>
				{lid > 0 ? (
					<>
						<rect x={white.cx - reach} y={top - 2} width={2 * reach} height={edge - top + 2} fill={colors.yellow} />
						<line x1={white.cx - reach} x2={white.cx + reach} y1={edge} y2={edge} stroke={colors.outline} strokeWidth={rig.lidStrokePx ?? 6} />
					</>
				) : null}
			</g>
		</g>
	);
};

export const AngleRig: React.FC<{
	name: string;
	rig: RigSpec;
	// The mouth (an assets/mouths code); ignored by a rig without one.
	mouthImage: string;
	// The breathing and talking bounce, the squash and stretch: the body's height, from seatY.
	bodyScaleY: number;
	// Stalk sway (degrees, the front rig's), the stalks' height above cutY.
	angle: Record<Side, number>;
	stalkStretch: number;
	// Eyelid (0 open, 1 shut); where the pupils look, as a fraction of the front rig's eye-white
	// radius (its offsets); the pupils' size; each pupil toward the other eye (same unit).
	lid: number;
	pupil: [number, number];
	pupilScale: number;
	pupilCross: number;
}> = ({name, rig, mouthImage, bodyScaleY, angle, stalkStretch, lid, pupil, pupilScale, pupilCross}) => {
	const [w, h] = rig.size;
	const {left, top, scale} = rig.placement;
	const {cutY, splitX} = rig.stalks;
	const src = staticFile(rig.image);
	const layer: React.CSSProperties = {position: 'absolute', left: 0, top: 0, width: w, height: h};
	const stalkInset = h - cutY - OVERLAP_PX;
	const stalkClip: Record<Side, string> = {
		left: `inset(0 ${w - splitX}px ${stalkInset}px 0)`,
		right: `inset(0 0 ${stalkInset}px ${splitX}px)`,
	};
	const m = rig.mouth;
	const mouthClip = m
		? `polygon(${[...m.clipX.map(([y, x]) => [x, y]), [w, m.clipX[m.clipX.length - 1][0]], [w, m.clipX[0][0]]]
				.map(([x, y]) => `${x}px ${y}px`)
				.join(', ')})`
		: undefined;
	return (
		<div style={{position: 'absolute', left, top, width: w, height: h, transform: `scale(${scale})`, transformOrigin: '0 0'}}>
			{/* The chair's base, below the seat line: static. */}
			<Img src={src} style={{...layer, clipPath: `inset(${rig.seatY}px 0 0 0)`}} />
			<div style={{...layer, transform: `scaleY(${bodyScaleY})`, transformOrigin: `${w / 2}px ${rig.seatY}px`}}>
				<Img src={src} style={{...layer, clipPath: `inset(${cutY}px 0 ${h - rig.seatY - OVERLAP_PX}px 0)`}} />
				{m ? (
					<div style={{...layer, clipPath: mouthClip}}>
						<Img
							src={staticFile(`${m.dir}/${mouthImage}.png`)}
							style={{
								position: 'absolute',
								left: m.center[0] - (m.pngSize[0] * m.scale) / 2,
								top: m.center[1] - (m.pngSize[1] * m.scale) / 2,
								width: m.pngSize[0] * m.scale,
								height: m.pngSize[1] * m.scale,
							}}
						/>
					</div>
				) : null}
				{(['left', 'right'] as const).map((side) => (
					<div
						key={side}
						style={{
							...layer,
							transform: `skewX(${angle[side]}deg) scaleY(${stalkStretch})`,
							transformOrigin: `${splitX}px ${cutY}px`,
						}}
					>
						<Img src={src} style={{...layer, clipPath: stalkClip[side]}} />
						{rig.eyes ? (
							<svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{position: 'absolute', left: 0, top: 0}}>
								<RigEyeView
									id={`rig-${name}-eye-${side}`}
									eye={rig.eyes[side]}
									rig={rig}
									lid={lid}
									look={[pupil[0] + (side === 'left' ? pupilCross : -pupilCross), pupil[1]]}
									pupilScale={pupilScale}
								/>
							</svg>
						) : null}
					</div>
				))}
			</div>
		</div>
	);
};
