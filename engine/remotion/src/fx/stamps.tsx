import {FxCertificate, FxCondition, FxFree} from '../types';
import {
	C,
	Canvas,
	FX,
	Fit,
	Label,
	OUTLINE,
	Pop,
	Sparks,
	TemplateDef,
	TemplateProps,
	lerp,
	lineH,
	pop,
	shake,
	slam,
	slamEndS,
	slamHitS,
	textWidth,
} from './kit';
import {Seal, tickPoints} from './shapes';

// The bounding box of a w x h box turned by deg.
const turned = (w: number, h: number, deg: number) => {
	const a = (Math.abs(deg) * Math.PI) / 180;
	return {width: w * Math.cos(a) + h * Math.sin(a), height: h * Math.cos(a) + w * Math.sin(a)};
};

// --- condition: one condition, a big check stamp slamming on above it -------------------

const CO = FX.templates.condition;

const ConditionView: React.FC<TemplateProps<FxCondition>> = ({data, c, box, maxScale, measure}) => {
	const S = CO.stamp;
	const R = S.radius;
	const textW = textWidth(measure, data.text, CO.textPx);
	const width = Math.max(textW, 2 * R + OUTLINE);
	const height = 2 * R + OUTLINE + lineH(CO.textPx) - S.overlapPx;
	const cx = width / 2;
	const st = slam(c, S.atS);
	const hitS = slamHitS(S.atS);
	const tick = tickPoints(2 * R).map(([x, y]) => [x - R, y - R].join(',')).join(' ');

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height} offset={shake(c, hitS, 'condition')}>
			{st ? (
				<Pop x={cx - R - OUTLINE / 2} y={0} width={2 * R + OUTLINE} height={2 * R + OUTLINE} scaleX={st.scaleX} scaleY={st.scaleY} rotate={S.deg}>
					<Canvas width={2 * R + OUTLINE} height={2 * R + OUTLINE}>
						<g transform={`translate(${R + OUTLINE / 2} ${R + OUTLINE / 2})`}>
							<circle r={R} fill={S.fill} stroke={C.outline} strokeWidth={OUTLINE + 2} />
							<circle r={R * S.ringRatio} fill="none" stroke={C.outline} strokeWidth={OUTLINE - 1} />
							<polyline points={tick} fill="none" stroke={C.outline} strokeWidth={R * 0.2 + 2 * OUTLINE} strokeLinecap="round" strokeLinejoin="round" />
							<polyline points={tick} fill="none" stroke={C.white} strokeWidth={R * 0.2} strokeLinecap="round" strokeLinejoin="round" />
						</g>
					</Canvas>
				</Pop>
			) : null}
			<Sparks c={c} atS={hitS} cx={cx} cy={R} w={2 * R} h={2 * R} />
			<Label
				cx={cx}
				top={2 * R + OUTLINE - S.overlapPx}
				px={CO.textPx}
				color={CO.textColor}
				scale={lerp(CO.in.fromScale, 1, pop(c, CO.in.atS, CO.in.durationS))}
			>
				{data.text}
			</Label>
		</Fit>
	);
};

export const condition: TemplateDef<FxCondition> = {
	View: ConditionView,
	endS: () => Math.max(CO.in.atS + CO.in.durationS, slamEndS(CO.stamp.atS)),
	check: (data) => {
		if (/\n/.test(data.text) || data.text.trim().length > CO.maxChars) {
			throw new Error(`Fx condition: one line of at most ${CO.maxChars} characters, got ${JSON.stringify(data.text)}`);
		}
	},
};

// --- free: a big "უფასო" stamp slams down -------------------------------------------------

const FR = FX.templates.free;

const FreeView: React.FC<TemplateProps<FxFree>> = ({data, c, box, maxScale, measure}) => {
	const text = data.text ?? FR.text;
	const w = textWidth(measure, text, FR.textPx) + 2 * FR.paddingXPx;
	const h = lineH(FR.textPx) + 2 * FR.paddingYPx;
	const {width, height} = turned(w, h, FR.deg);
	const st = slam(c, FR.atS);
	const hitS = slamHitS(FR.atS);
	const inset = FR.innerInsetPx;

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height} offset={shake(c, hitS, 'free')}>
			{st ? (
				<Pop
					x={(width - w) / 2}
					y={(height - h) / 2}
					width={w}
					height={h}
					scaleX={st.scaleX}
					scaleY={st.scaleY}
					rotate={FR.deg}
					style={{boxSizing: 'border-box', border: `${FR.outlinePx}px solid ${C.outline}`, borderRadius: FR.radius, backgroundColor: FR.fill}}
				>
					<div
						style={{
							position: 'absolute',
							inset: inset - FR.outlinePx,
							border: `${FR.innerOutlinePx}px solid ${C.outline}`,
							borderRadius: FR.radius - inset / 2,
						}}
					/>
					<Label cx={w / 2 - FR.outlinePx} top={FR.paddingYPx - FR.outlinePx} px={FR.textPx} color={FR.textColor} strokeEm={0}>
						{text}
					</Label>
				</Pop>
			) : null}
			<Sparks c={c} atS={hitS} cx={width / 2} cy={height / 2} w={w} h={h} />
		</Fit>
	);
};

export const free: TemplateDef<FxFree> = {
	View: FreeView,
	endS: () => slamEndS(FR.atS),
};

// --- certificate: a diploma scroll unrolls and a seal stamps on ------------------------------

const CE = FX.templates.certificate;

const CertificateView: React.FC<TemplateProps<FxCertificate>> = ({data, c, box, maxScale, measure}) => {
	const P = CE.paper;
	const R = CE.roller;
	const L = CE.lines;
	const S = CE.seal;
	const label = data.label ?? CE.label;
	const labelPx = Math.min(CE.labelPx, (CE.labelPx * (P.width - 2 * L.leftPx)) / textWidth(measure, label, CE.labelPx));
	const paperX = R.width;
	const paperY = R.overhangPx;
	const sealX = paperX + P.width * S.xRatio;
	const sealY = paperY + P.height * S.yRatio;
	const width = P.width + 2 * R.width;
	const height = Math.max(P.height + 2 * R.overhangPx, sealY + S.r * 1.55);
	const cx = paperX + P.width / 2;
	const open = Math.max(0, pop(c, CE.unroll.atS, CE.unroll.durationS, CE.unroll.damping));
	const visW = P.width * open;
	const st = slam(c, S.atS);
	const hitS = slamHitS(S.atS);

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height} offset={shake(c, hitS, 'certificate')}>
			<div style={{position: 'absolute', left: cx - visW / 2, top: paperY, width: visW, height: P.height, overflow: 'hidden'}}>
				<div
					style={{
						position: 'absolute',
						left: visW / 2 - P.width / 2,
						top: 0,
						width: P.width,
						height: P.height,
						boxSizing: 'border-box',
						backgroundColor: P.fill,
						border: `${OUTLINE}px solid ${C.outline}`,
						borderRadius: P.radius,
					}}
				>
					<Label cx={P.width / 2 - OUTLINE} top={CE.labelTopPx} px={labelPx} color={CE.labelColor} strokeEm={0}>
						{label}
					</Label>
					{L.widths.map((wr, i) => (
						<div
							key={i}
							style={{
								position: 'absolute',
								left: L.leftPx,
								top: L.topPx + i * (L.heightPx + L.gapPx),
								width: (P.width - 2 * L.leftPx) * wr,
								height: L.heightPx,
								borderRadius: L.heightPx / 2,
								backgroundColor: L.color,
							}}
						/>
					))}
				</div>
			</div>
			{[-1, 1].map((side) => (
				<div
					key={side}
					style={{
						position: 'absolute',
						left: cx + side * (visW / 2) - (side < 0 ? R.width : 0),
						top: 0,
						width: R.width,
						height: P.height + 2 * R.overhangPx,
						boxSizing: 'border-box',
						border: `${OUTLINE}px solid ${C.outline}`,
						borderRadius: R.radius,
						backgroundColor: R.fill,
					}}
				/>
			))}
			{st ? (
				<Pop x={sealX - S.r * 1.6} y={sealY - S.r * 1.6} width={S.r * 3.2} height={S.r * 3.2} scaleX={st.scaleX} scaleY={st.scaleY} rotate={-10}>
					<Canvas width={S.r * 3.2} height={S.r * 3.2}>
						<g transform={`translate(${S.r * 1.6} ${S.r * 1.6})`}>
							<Seal r={S.r} />
						</g>
					</Canvas>
				</Pop>
			) : null}
			<Sparks c={c} atS={hitS} cx={sealX} cy={sealY} w={2 * S.r} h={2 * S.r} />
		</Fit>
	);
};

export const certificate: TemplateDef<FxCertificate> = {
	View: CertificateView,
	endS: () => Math.max(CE.unroll.atS + CE.unroll.durationS, slamEndS(CE.seal.atS)),
};
