import {useMemo} from 'react';
import {Easing, random} from 'remotion';
import {FxAge, FxMoney, FxPlaces, FxPrize} from '../types';
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
	formatNumber,
	lerp,
	lineH,
	pop,
	punch,
	ramp,
	textStyle,
	textWidth,
} from './kit';
import {Coin, Seat, Sparkle, Trophy} from './shapes';

// --- age: a range bar draws in, its marker sliding to the upper age ---------------------

const AG = FX.templates.age;

const AgeView: React.FC<TemplateProps<FxAge>> = ({data, c, box, maxScale}) => {
	const {from, to} = data;
	const K = AG.knob;
	const TR = AG.track;
	const span = to - from;
	const lo = from - span * TR.padRatio;
	const hi = to + span * TR.padRatio;
	const trackCY = lineH(AG.numberPx) + AG.numberGapPx + K.radius;
	const unitTop = trackCY + K.radius + AG.unitGapPx;
	const width = TR.width + 2 * K.radius;
	const height = unitTop + lineH(AG.unitPx);
	const x = (v: number) => K.radius + ((v - lo) / (hi - lo)) * TR.width;

	const draw = Math.max(0, pop(c, TR.atS, TR.drawS));
	const left = pop(c, AG.leftAtS, K.popS);
	const right = pop(c, AG.rightAtS, K.popS);
	const slide = pop(c, AG.rightAtS, AG.slide.durationS, AG.slide.damping);
	const v = from + span * slide;
	const shown = Math.round(Math.min(to, Math.max(from, v)));
	const ty = trackCY - TR.height / 2;
	const o = OUTLINE / 2;

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			<Canvas width={width} height={height}>
				<rect
					x={K.radius + o}
					y={ty + o}
					width={Math.max(0, TR.width * draw - OUTLINE)}
					height={TR.height - OUTLINE}
					rx={(TR.height - OUTLINE) / 2}
					fill={C.empty}
					stroke={C.outline}
					strokeWidth={OUTLINE}
				/>
				{c.t >= AG.rightAtS ? (
					<rect
						x={x(from)}
						y={ty + o}
						width={Math.max(0, x(v) - x(from))}
						height={TR.height - OUTLINE}
						fill={AG.fill}
						stroke={C.outline}
						strokeWidth={OUTLINE}
					/>
				) : null}
				{[
					{cx: x(from), s: left},
					{cx: x(v), s: right},
				].map((k, i) => (
					<circle key={i} cx={k.cx} cy={trackCY} r={Math.max(0, (K.radius - o) * k.s)} fill={K.fill} stroke={C.outline} strokeWidth={OUTLINE} />
				))}
			</Canvas>
			<Label cx={x(from)} top={0} px={AG.numberPx} color={AG.numberColor} scale={left}>
				{from}
			</Label>
			{c.t >= AG.rightAtS ? (
				<Label cx={x(v)} top={0} px={AG.numberPx} color={AG.numberColor} scale={right}>
					{shown}
				</Label>
			) : null}
			<Label cx={(x(from) + x(to)) / 2} top={unitTop} px={AG.unitPx} color={AG.unitColor} scale={pop(c, AG.unitAtS, AG.unitPopS)}>
				{data.unit}
			</Label>
		</Fit>
	);
};

export const age: TemplateDef<FxAge> = {
	View: AgeView,
	endS: () => Math.max(AG.track.atS + AG.track.drawS, AG.rightAtS + Math.max(AG.knob.popS, AG.slide.durationS), AG.unitAtS + AG.unitPopS),
	check: (data) => {
		if (!(data.to > data.from)) {
			throw new Error(`Fx age: "to" (${data.to}) must be above "from" (${data.from})`);
		}
	},
};

// --- money: the amount counts up, coins stacking beside it --------------------------------

const MO = FX.templates.money;

const moneyLandS = () => MO.count.atS + MO.count.durationS;
const coinAtS = (i: number) => MO.count.atS + (i / MO.coins.count) * MO.count.durationS;

// A count-up number, right-aligned in a box as wide as its final value.
const Counter: React.FC<{x: number; y: number; width: number; px: number; color: string; value: string}> = ({x, y, width, px, color, value}) => (
	<div style={{position: 'absolute', left: x, top: y, width, height: lineH(px), textAlign: 'right', ...textStyle(px, color)}}>{value}</div>
);

const MoneyView: React.FC<TemplateProps<FxMoney>> = ({data, c, box, maxScale, measure}) => {
	const CO = MO.coins;
	const numW = textWidth(measure, formatNumber(data.value), MO.numberPx);
	const blockW = Math.max(numW, textWidth(measure, data.unit, MO.unitPx));
	const blockH = lineH(MO.numberPx) + MO.unitGapPx + lineH(MO.unitPx);
	const coinW = 2 * CO.radiusX + OUTLINE;
	const stackH = CO.count * CO.thicknessPx + 2 * CO.radiusY + OUTLINE;
	const textX = coinW + CO.gapPx;
	const width = textX + blockW;
	const height = Math.max(blockH, stackH);

	const inScale = lerp(MO.in.fromScale, 1, pop(c, MO.in.atS, MO.in.durationS));
	const counted = data.value * ramp(c, MO.count.atS, MO.count.durationS);
	const landS = moneyLandS();
	const numScale = inScale * punch(c, landS, MO.land.durationS, MO.land.scale);
	const blockTop = (height - blockH) / 2;
	const cx = textX + blockW / 2;
	// Bottom coin's top face; each next one a coin thickness higher.
	const baseY = height - (height - stackH) / 2 - OUTLINE / 2 - CO.radiusY - CO.thicknessPx;

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			<Canvas width={coinW} height={height}>
				{Array.from({length: CO.count}, (_, i) => {
					const at = coinAtS(i);
					if (c.t < at) {
						return null;
					}
					const drop = -CO.dropPx * (1 - ramp(c, at, CO.dropS, Easing.bounce));
					return <Coin key={i} cx={coinW / 2} y={baseY - i * CO.thicknessPx + drop} rx={CO.radiusX} ry={CO.radiusY} th={CO.thicknessPx} />;
				})}
			</Canvas>
			<Pop x={0} y={0} width={width} height={height} scale={numScale} origin={`${cx}px ${blockTop + lineH(MO.numberPx) / 2}px`}>
				<Counter x={cx - numW / 2} y={blockTop} width={numW} px={MO.numberPx} color={MO.numberColor} value={formatNumber(counted)} />
			</Pop>
			<Label cx={cx} top={blockTop + lineH(MO.numberPx) + MO.unitGapPx} px={MO.unitPx} color={MO.unitColor} scale={inScale}>
				{data.unit}
			</Label>
			<Sparks c={c} atS={landS} cx={cx} cy={blockTop + lineH(MO.numberPx) / 2} w={numW} h={lineH(MO.numberPx) * 0.7} />
		</Fit>
	);
};

export const money: TemplateDef<FxMoney> = {
	View: MoneyView,
	endS: () =>
		Math.max(
			MO.in.atS + MO.in.durationS,
			moneyLandS() + Math.max(MO.land.durationS, FX.sparks.durationS),
			coinAtS(MO.coins.count - 1) + MO.coins.dropS,
		),
};

// --- places: a big count while a grid of seats fills up -------------------------------------

const PL = FX.templates.places;

// When each seat fills: a sweep from the top-left corner, seeded jitter on top.
const seatTimes = (): number[] => {
	const G = PL.grid;
	const F = PL.fill;
	const diagonals = Math.max(1, G.cols - 1 + G.rows - 1);
	const times: number[] = [];
	for (let r = 0; r < G.rows; r++) {
		for (let col = 0; col < G.cols; col++) {
			times.push(F.atS + ((col + r) / diagonals) * (F.durationS - G.jitterS) + random(`${G.seed}:${r}:${col}`) * G.jitterS);
		}
	}
	return times;
};

const PlacesView: React.FC<TemplateProps<FxPlaces>> = ({data, c, box, maxScale, measure}) => {
	const G = PL.grid;
	const numW = textWidth(measure, formatNumber(data.value), PL.numberPx);
	const unitW = textWidth(measure, data.unit, PL.unitPx);
	const rowW = numW + PL.unitGapPx + unitW;
	const rowH = lineH(PL.numberPx);
	const gridW = G.cols * G.seatPx + (G.cols - 1) * G.gapPx;
	const gridH = G.rows * G.seatPx + (G.rows - 1) * G.gapPx;
	const width = Math.max(rowW, gridW);
	const height = rowH + PL.rowGapPx + gridH;

	const times = useMemo(seatTimes, []);
	const filled = times.filter((at) => c.t >= at).length;
	const landS = Math.max(...times);
	const inScale = lerp(PL.in.fromScale, 1, pop(c, PL.in.atS, PL.in.durationS));
	const numScale = inScale * punch(c, landS, PL.land.durationS, PL.land.scale);
	const rowX = (width - rowW) / 2;
	const gridX = (width - gridW) / 2;
	const gridY = rowH + PL.rowGapPx;

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			<Pop x={0} y={0} width={width} height={rowH} scale={numScale}>
				<div style={{position: 'absolute', left: rowX, top: 0, display: 'flex', alignItems: 'baseline'}}>
					<span style={{...textStyle(PL.numberPx, PL.numberColor), display: 'inline-block', width: numW, textAlign: 'right'}}>
						{formatNumber((data.value * filled) / times.length)}
					</span>
					<span style={{...textStyle(PL.unitPx, PL.unitColor), marginLeft: PL.unitGapPx}}>{data.unit}</span>
				</div>
			</Pop>
			<Pop x={gridX} y={gridY} width={gridW} height={gridH} scale={inScale}>
				<Canvas width={gridW} height={gridH}>
					{times.map((at, i) => {
						const on = c.t >= at;
						const s = on ? lerp(PL.fill.fromScale, 1, pop(c, at, PL.fill.popS)) : 1;
						const x = (i % G.cols) * (G.seatPx + G.gapPx);
						const y = Math.floor(i / G.cols) * (G.seatPx + G.gapPx);
						const h = G.seatPx / 2;
						return (
							<g key={i} transform={`translate(${x + h} ${y + h}) scale(${s}) translate(${-h} ${-h})`}>
								<Seat size={G.seatPx} fill={on ? G.full : C.empty} outlinePx={G.outlinePx} />
							</g>
						);
					})}
				</Canvas>
			</Pop>
		</Fit>
	);
};

export const places: TemplateDef<FxPlaces> = {
	View: PlacesView,
	endS: () => Math.max(PL.in.atS + PL.in.durationS, PL.fill.atS + PL.fill.durationS + Math.max(PL.fill.popS, PL.land.durationS)),
};

// --- prize: a trophy pops up, the prize under it ----------------------------------------------

const PR = FX.templates.prize;
const TROPHY = {width: 260, height: 290};

const PrizeView: React.FC<TemplateProps<FxPrize>> = ({data, c, box, maxScale, measure}) => {
	const T = PR.trophy;
	const S = PR.sparkles;
	const textW = textWidth(measure, data.text, PR.textPx);
	const width = Math.max(TROPHY.width + 120, textW);
	const height = TROPHY.height + PR.gapPx + lineH(PR.textPx);
	const tx = (width - TROPHY.width) / 2;
	const rise = pop(c, T.atS, T.riseS, T.damping);
	const dt = c.t - (T.atS + T.riseS * 0.4);
	const wobble = dt < 0 ? 0 : T.wobbleDeg * Math.exp(-dt / 0.12) * Math.sin(2 * Math.PI * 2.5 * dt);
	const sp = pop(c, S.atS, S.popS);

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			<Pop
				x={tx}
				y={T.fromYPx * (1 - rise)}
				width={TROPHY.width}
				height={TROPHY.height}
				scale={lerp(0.3, 1, rise)}
				rotate={wobble}
				origin="50% 100%"
			>
				<Canvas width={TROPHY.width} height={TROPHY.height}>
					<Trophy />
				</Canvas>
			</Pop>
			{sp > 0 ? (
				<Canvas width={width} height={height}>
					{(S.items as [number, number, number][]).map(([x, y, r], i) => (
						<Sparkle key={i} cx={tx + x} cy={y} r={r * sp} fill={S.fill} />
					))}
				</Canvas>
			) : null}
			<Label cx={width / 2} top={TROPHY.height + PR.gapPx} px={PR.textPx} color={PR.textColor} scale={pop(c, PR.textAtS, PR.textPopS, 10)}>
				{data.text}
			</Label>
		</Fit>
	);
};

export const prize: TemplateDef<FxPrize> = {
	View: PrizeView,
	endS: () => Math.max(PR.trophy.atS + PR.trophy.riseS, PR.trophy.atS + PR.trophy.riseS * 0.4 + 0.5, PR.sparkles.atS + PR.sparkles.popS, PR.textAtS + PR.textPopS),
};
