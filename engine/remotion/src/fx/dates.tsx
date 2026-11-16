import {Easing, interpolate} from 'remotion';
import {FxDeadline, FxDuration, FxStart} from '../types';
import {C, Canvas, FX, Fit, Label, OUTLINE, Pop, TemplateDef, TemplateProps, clamp, formatNumber, lerp, lineH, pop, punch, ramp, textWidth} from './kit';
import {Rocket} from './shapes';

// --- deadline: a calendar flips to the date -------------------------------------------

const DL = FX.templates.deadline;

const deadlineLandS = () => {
	const f = DL.flips;
	return f.count > 0 ? f.firstAtS + (f.count - 1) * f.gapS + f.durationS : DL.in.atS;
};

const Page: React.FC<{day: number | null; month: string; monthPx: number; dayScale?: number; style?: React.CSSProperties}> = ({
	day,
	month,
	monthPx,
	dayScale = 1,
	style,
}) => {
	const P = DL.page;
	const inner = (P.width - 2 * OUTLINE) / 2;
	return (
		<div
			style={{
				position: 'absolute',
				left: 0,
				top: 0,
				width: P.width,
				height: P.height,
				boxSizing: 'border-box',
				border: `${OUTLINE}px solid ${C.outline}`,
				borderRadius: P.radius,
				backgroundColor: P.fill,
				overflow: 'hidden',
				...style,
			}}
		>
			<div
				style={{
					position: 'absolute',
					left: 0,
					right: 0,
					top: 0,
					height: P.headerPx - OUTLINE,
					backgroundColor: P.header,
					borderBottom: `${OUTLINE}px solid ${C.outline}`,
				}}
			>
				<Label cx={inner} top={(P.headerPx - 2 * OUTLINE - lineH(monthPx)) / 2} px={monthPx} color={P.monthColor}>
					{month}
				</Label>
			</div>
			{day === null ? null : (
				<Label
					cx={inner}
					top={P.headerPx + (P.height - P.headerPx - lineH(P.dayPx)) / 2 - OUTLINE}
					px={P.dayPx}
					color={P.dayColor}
					strokeEm={0}
					scale={dayScale}
				>
					{day}
				</Label>
			)}
		</div>
	);
};

const DeadlineView: React.FC<TemplateProps<FxDeadline>> = ({data, c, box, maxScale, measure}) => {
	const P = DL.page;
	const K = DL.counter;
	const F = DL.flips;
	const ringTop = P.ring.height / 2;
	const stack = P.stackPages * P.stackOffsetPx;
	const calW = P.width + stack;
	const calH = ringTop + P.height + stack;
	// The month shrinks to fit the header.
	const monthPx = Math.min(P.monthPx, (P.monthPx * (P.width - 2 * P.monthPaddingPx)) / textWidth(measure, data.month, P.monthPx));
	const daysLeft = data.daysLeft ?? null;
	const tag =
		daysLeft === null
			? null
			: (() => {
					const widest = Math.max(
						textWidth(measure, formatNumber(daysLeft + K.tickFrom), K.numberPx),
						textWidth(measure, K.label, K.labelPx),
					);
					return {width: widest + 2 * K.paddingPx, height: 2 * K.paddingPx + lineH(K.numberPx) + lineH(K.labelPx)};
				})();
	// The tag sits on the calendar's right edge (a negative gap overlaps it), offsetYPx low.
	const tagY0 = tag ? (calH - tag.height) / 2 + K.offsetYPx : 0;
	const minY = Math.min(0, tagY0);
	const width = tag ? Math.max(calW, calW + K.gapPx + tag.width) : calW;
	const height = Math.max(calH, tag ? tagY0 + tag.height : 0) - minY;

	const calScale = lerp(DL.in.fromScale, 1, pop(c, DL.in.atS, DL.in.durationS));
	const landS = deadlineLandS();
	// The date page's number pops as the last page flips off it.
	const dayScale = F.count > 0 ? lerp(DL.land.dayFromScale, 1, pop(c, landS - F.durationS / 2, DL.land.durationS)) : 1;
	// Pages still to flip, the first to go on top: the days before the date (blank below 1).
	const flips = Array.from({length: F.count}, (_, k) => {
		const day = data.day - F.count + k;
		const at = F.firstAtS + k * F.gapS;
		return {
			day: day >= 1 ? day : null,
			done: c.t >= at + F.durationS,
			angle: interpolate(c.t, [at, at + F.durationS], [0, F.toDeg], {...clamp, easing: Easing.in(Easing.quad)}),
		};
	}).reverse();

	const tagScale = tag ? pop(c, K.atS, K.popS, 10) : 0;
	// The calendar starts centred and slides over to make room as the tag pops in.
	const calShift = ((width - calW) / 2) * (1 - tagScale);
	const ticked = daysLeft === null ? 0 : daysLeft + Math.round(K.tickFrom * (1 - ramp(c, K.atS, K.tickS)));
	const ringXs = [P.width * P.ring.insetRatio, P.width * (1 - P.ring.insetRatio)];

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			<Pop x={calShift} y={-minY} width={calW} height={calH} scale={calScale} origin="50% 100%">
				<div style={{position: 'absolute', left: 0, top: ringTop, width: P.width, height: P.height}}>
					{Array.from({length: P.stackPages}, (_, i) => P.stackPages - i).map((k) => (
						<Page key={k} day={null} month="" monthPx={monthPx} style={{left: k * P.stackOffsetPx, top: k * P.stackOffsetPx}} />
					))}
					<Page day={data.day} month={data.month} monthPx={monthPx} dayScale={dayScale} />
					{flips.map((p, i) =>
						p.done ? null : (
							<Page
								key={i}
								day={p.day}
								month={data.month}
								monthPx={monthPx}
								style={{
									transform: `perspective(${F.perspectivePx}px) rotateX(${p.angle}deg)`,
									transformOrigin: '50% 0%',
									backfaceVisibility: 'hidden',
								}}
							/>
						),
					)}
				</div>
				{ringXs.map((x, i) => (
					<div
						key={i}
						style={{
							position: 'absolute',
							left: x - P.ring.width / 2,
							top: 0,
							width: P.ring.width,
							height: P.ring.height,
							boxSizing: 'border-box',
							borderRadius: P.ring.radius,
							border: `${OUTLINE}px solid ${C.outline}`,
							backgroundColor: P.ring.fill,
						}}
					/>
				))}
			</Pop>
			{tag ? (
				<Pop
					x={calW + K.gapPx}
					y={tagY0 - minY}
					width={tag.width}
					height={tag.height}
					scale={tagScale}
					rotate={lerp(K.fromDeg, K.deg, tagScale)}
					style={{boxSizing: 'border-box', border: `${OUTLINE}px solid ${C.outline}`, borderRadius: K.radius, backgroundColor: K.fill}}
				>
					<Label cx={tag.width / 2 - OUTLINE} top={K.paddingPx - OUTLINE} px={K.numberPx} color={K.textColor} strokeEm={0}>
						{formatNumber(ticked)}
					</Label>
					<Label cx={tag.width / 2 - OUTLINE} top={K.paddingPx - OUTLINE + lineH(K.numberPx)} px={K.labelPx} color={K.textColor} strokeEm={0}>
						{K.label}
					</Label>
				</Pop>
			) : null}
		</Fit>
	);
};

export const deadline: TemplateDef<FxDeadline> = {
	View: DeadlineView,
	endS: (data) => {
		const K = DL.counter;
		const counter = data.daysLeft == null ? 0 : K.atS + Math.max(K.popS, K.tickS);
		return Math.max(DL.in.atS + DL.in.durationS, deadlineLandS() + DL.land.durationS, counter);
	},
};

// --- start: a rocket lifts off beside the start date -------------------------------------

const ST = FX.templates.start;

const StartView: React.FC<TemplateProps<FxStart>> = ({data, c, box, maxScale, measure}) => {
	const R = ST.rocket;
	const label = data.label ?? ST.label;
	const day = String(data.day);
	const textW = Math.max(
		textWidth(measure, label, ST.labelPx),
		textWidth(measure, day, ST.dayPx),
		textWidth(measure, data.month, ST.monthPx),
	);
	const textH = lineH(ST.labelPx) + lineH(ST.dayPx) + lineH(ST.monthPx);
	const width = R.boxWidth + ST.gapPx + textW;
	const height = Math.max(R.boxHeight, textH);
	const textX = R.boxWidth + ST.gapPx + textW / 2;
	const textTop = (height - textH) / 2;
	// The rocket's body bottom (its origin), leaving room for the flame below it.
	const rx = R.boxWidth / 2;
	const ry = (height + R.boxHeight) / 2 - 130 * R.scale;
	const rise = pop(c, R.atS, R.riseS, R.damping);
	const P = ST.puffs;
	const puffs = Array.from({length: P.count}, (_, i) => {
		const k = P.count > 1 ? i / (P.count - 1) - 0.5 : 0;
		const at = P.atS + Math.abs(k) * 0.12;
		const p = ramp(c, at, P.durationS, (x) => x);
		// Each puff swells and shrinks away, drifting up and out from the launch pad.
		return {
			x: rx + k * 2 * P.spreadPx * (0.7 + 0.3 * p),
			y: ry + 110 * R.scale - Math.abs(k) * 30 - P.risePx * p,
			s: c.t < at ? 0 : Math.sin(Math.PI * p) * (1 - 0.3 * Math.abs(k)),
		};
	});

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			<Canvas width={width} height={height}>
				<g
					transform={`translate(${rx} ${ry + R.fromYPx * (1 - rise)}) rotate(${R.tiltDeg * Math.min(1, Math.max(0, rise))} 0 ${-130 * R.scale}) scale(${R.scale})`}
				>
					<Rocket flame={c.t < R.atS ? 0 : 1} />
				</g>
				{puffs.map((p, i) =>
					p.s > 0.01 ? <circle key={i} cx={p.x} cy={p.y} r={P.r * p.s} fill={C.white} stroke={C.outline} strokeWidth={OUTLINE} /> : null,
				)}
			</Canvas>
			<Label cx={textX} top={textTop} px={ST.labelPx} color={ST.labelColor} scale={pop(c, ST.labelAtS, ST.popS)}>
				{label}
			</Label>
			<Label cx={textX} top={textTop + lineH(ST.labelPx)} px={ST.dayPx} color={ST.dayColor} scale={pop(c, ST.dateAtS, ST.popS, 10)}>
				{day}
			</Label>
			<Label
				cx={textX}
				top={textTop + lineH(ST.labelPx) + lineH(ST.dayPx)}
				px={ST.monthPx}
				color={ST.monthColor}
				scale={pop(c, ST.dateAtS + 0.08, ST.popS)}
			>
				{data.month}
			</Label>
		</Fit>
	);
};

export const start: TemplateDef<FxStart> = {
	View: StartView,
	endS: () =>
		Math.max(ST.rocket.atS + ST.rocket.riseS, ST.puffs.atS + 0.12 + ST.puffs.durationS, ST.labelAtS + ST.popS, ST.dateAtS + 0.08 + ST.popS),
};

// --- duration: a stopwatch fills while the number counts up ------------------------------

const DU = FX.templates.duration;

// The filled sector from 12 o'clock clockwise to `p` of the way round.
const sector = (cx: number, cy: number, r: number, p: number): string => {
	if (p >= 0.999) {
		return `M ${cx - r} ${cy} A ${r} ${r} 0 1 1 ${cx + r} ${cy} A ${r} ${r} 0 1 1 ${cx - r} ${cy} Z`;
	}
	const a = 2 * Math.PI * p - Math.PI / 2;
	return `M ${cx} ${cy} L ${cx} ${cy - r} A ${r} ${r} 0 ${p > 0.5 ? 1 : 0} 1 ${cx + r * Math.cos(a)} ${cy + r * Math.sin(a)} Z`;
};

const DurationView: React.FC<TemplateProps<FxDuration>> = ({data, c, box, maxScale, measure}) => {
	const W = DU.watch;
	const watchW = 2 * W.r + OUTLINE;
	const watchH = 2 * W.r + OUTLINE + W.buttonPx;
	const final = formatNumber(data.value);
	const numW = textWidth(measure, final, DU.numberPx);
	const textW = Math.max(numW, textWidth(measure, data.unit, DU.unitPx));
	const textH = lineH(DU.numberPx) + lineH(DU.unitPx);
	const width = watchW + DU.gapPx + textW;
	const height = Math.max(watchH, textH);
	const cx = watchW / 2;
	const cy = (height - watchH) / 2 + W.buttonPx + W.r + OUTLINE / 2;

	const inScale = lerp(DU.in.fromScale, 1, pop(c, DU.in.atS, DU.in.durationS));
	const p = ramp(c, DU.fill.atS, DU.fill.durationS, Easing.inOut(Easing.cubic));
	const landS = DU.fill.atS + DU.fill.durationS;
	const dt = c.t - landS;
	const wobble = dt < 0 ? 0 : DU.land.wobbleDeg * Math.exp(-dt / 0.1) * Math.sin(2 * Math.PI * 3 * dt);
	const hand = 2 * Math.PI * p - Math.PI / 2;
	const textX = watchW + DU.gapPx + textW / 2;
	const textTop = (height - textH) / 2;

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			<Pop x={0} y={0} width={watchW} height={height} scale={inScale} rotate={wobble} origin={`${cx}px ${cy}px`}>
				<Canvas width={watchW} height={height}>
					<rect x={cx - W.buttonPx / 2} y={cy - W.r - W.buttonPx} width={W.buttonPx} height={W.buttonPx * 0.7} rx={8} fill={C.bezel} stroke={C.outline} strokeWidth={OUTLINE} />
					<rect x={cx - 10} y={cy - W.r - W.buttonPx * 0.32} width={20} height={W.buttonPx * 0.4} fill={C.bezel} stroke={C.outline} strokeWidth={OUTLINE} />
					<circle cx={cx} cy={cy} r={W.r} fill={W.face} stroke={C.outline} strokeWidth={OUTLINE + 2} />
					{p > 0 ? <path d={sector(cx, cy, W.r - OUTLINE, p)} fill={W.fill} /> : null}
					{Array.from({length: W.ticks}, (_, i) => {
						const a = (2 * Math.PI * i) / W.ticks;
						const r0 = W.r - OUTLINE - 6;
						return (
							<line
								key={i}
								x1={cx + r0 * Math.sin(a)}
								y1={cy - r0 * Math.cos(a)}
								x2={cx + (r0 - W.tickPx) * Math.sin(a)}
								y2={cy - (r0 - W.tickPx) * Math.cos(a)}
								stroke={C.outline}
								strokeWidth={OUTLINE}
								strokeLinecap="round"
							/>
						);
					})}
					<line
						x1={cx}
						y1={cy}
						x2={cx + W.r * W.handRatio * Math.cos(hand)}
						y2={cy + W.r * W.handRatio * Math.sin(hand)}
						stroke={C.outline}
						strokeWidth={OUTLINE * 1.8}
						strokeLinecap="round"
					/>
					<circle cx={cx} cy={cy} r={14} fill={C.outline} />
				</Canvas>
			</Pop>
			<div style={{position: 'absolute', left: 0, top: 0, width, height, transform: `scale(${inScale})`, transformOrigin: `${textX}px 50%`}}>
				<Label cx={textX} top={textTop} px={DU.numberPx} color={DU.numberColor} scale={punch(c, landS, DU.land.durationS, DU.land.scale)}>
					{formatNumber(Math.round(data.value * p))}
				</Label>
				<Label cx={textX} top={textTop + lineH(DU.numberPx)} px={DU.unitPx} color={DU.unitColor}>
					{data.unit}
				</Label>
			</div>
		</Fit>
	);
};

export const duration: TemplateDef<FxDuration> = {
	View: DurationView,
	endS: () => Math.max(DU.in.atS + DU.in.durationS, DU.fill.atS + DU.fill.durationS + DU.land.durationS),
};
