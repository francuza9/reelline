import {FxApply, FxSteps} from '../types';
import {C, Canvas, FX, Fit, Label, OUTLINE, Pop, TemplateDef, TemplateProps, lerp, lineH, pop, punch, ramp, textStyle, textWidth} from './kit';
import {stepsLayout} from './layout';
import {Cursor} from './shapes';

// --- apply: a domain types into a full-width address bar, then a cursor clicks go ---------

const AP = FX.templates.apply;

// When typing ends, the cursor arrives and it clicks (seconds from the template's start).
const applyTimes = (url: string) => {
	const typeEnd = AP.type.atS + Math.min(AP.type.maxS, url.length * AP.type.charS);
	const cursorAt = typeEnd + AP.cursor.afterTypeS;
	const clickAt = cursorAt + AP.cursor.moveS + AP.click.afterMoveS;
	return {typeEnd, cursorAt, clickAt};
};

const ApplyView: React.FC<TemplateProps<FxApply>> = ({data, c, box, maxScale, measure}) => {
	const W = AP.window;
	const A = AP.address;
	const B = AP.button;
	const {typeEnd, cursorAt, clickAt} = applyTimes(data.url);
	// The address bar spans the window; the go button sits on the page under it, right.
	const bar = {x: A.marginPx, y: W.barPx + A.marginPx, w: W.width - 2 * A.marginPx, h: A.heightPx};
	const pageTop = bar.y + bar.h;
	const button = {x: W.width - A.marginPx - B.width, y: pageTop + B.topPx, w: B.width, h: B.height};
	const height = button.y + button.h + B.bottomPx;
	// The domain as big as fits between the bar's paddings, its caret included.
	const avail = bar.w - 2 * A.paddingPx - AP.caret.widthPx - 4;
	const textPx = Math.min(A.textPx, (A.textPx * avail) / textWidth(measure, data.url, A.textPx));
	const typed = data.url.slice(0, Math.round(data.url.length * ramp(c, AP.type.atS, typeEnd - AP.type.atS, (x) => x)));
	const clicked = c.t >= clickAt;
	const caretOn = c.t >= AP.type.atS && !clicked && (c.t < typeEnd || Math.floor(c.t * AP.caret.blinkHz * 2) % 2 === 0);
	const textX = bar.x + A.paddingPx;
	const midY = bar.y + bar.h / 2;
	const caretX = textX + measure(typed, textPx) + 4;
	const move = ramp(c, cursorAt, AP.cursor.moveS);
	const bc = {x: button.x + button.w / 2, y: button.y + button.h / 2};
	const tip = {x: bc.x + B.width * 0.12, y: bc.y + 6};
	const cursor = {x: tip.x + AP.cursor.fromXPx * (1 - move), y: tip.y + AP.cursor.fromYPx * (1 - move)};
	const ripple = ramp(c, clickAt, AP.click.rippleS);
	const press = punch(c, clickAt, AP.click.durationS, 1 - AP.click.squash);
	const P = AP.body;
	const lineW = button.x - P.gapPx - (bar.x + P.insetPx);
	const arrow = B.height * 0.22;

	return (
		<Fit box={box} maxScale={maxScale} width={W.width} height={height}>
			<Pop x={0} y={0} width={W.width} height={height} scale={lerp(AP.in.fromScale, 1, pop(c, AP.in.atS, AP.in.durationS))}>
				<div
					style={{
						position: 'absolute',
						inset: 0,
						boxSizing: 'border-box',
						border: `${OUTLINE}px solid ${C.outline}`,
						borderRadius: W.radius,
						backgroundColor: W.fill,
						overflow: 'hidden',
					}}
				>
					<div style={{position: 'absolute', left: 0, right: 0, top: 0, height: W.barPx - OUTLINE, backgroundColor: W.bar, borderBottom: `${OUTLINE}px solid ${C.outline}`}} />
				</div>
				<Canvas width={W.width} height={height}>
					{W.dots.map((fill, i) => (
						<circle key={i} cx={30 + i * 34} cy={W.barPx / 2} r={W.dotR} fill={fill} stroke={C.outline} strokeWidth={OUTLINE * 0.8} />
					))}
					<rect x={bar.x} y={bar.y} width={bar.w} height={bar.h} rx={A.radius} fill={A.fill} stroke={C.outline} strokeWidth={OUTLINE} />
					{P.widths.map((wr, i) => (
						<rect
							key={i}
							x={bar.x + P.insetPx}
							y={button.y + P.topPx + i * (P.heightPx + P.lineGapPx)}
							width={lineW * wr}
							height={P.heightPx}
							rx={P.heightPx / 2}
							fill={P.color}
						/>
					))}
					{clicked && ripple < 1 ? (
						<rect
							x={button.x - AP.click.ripplePx * ripple}
							y={button.y - AP.click.ripplePx * ripple}
							width={button.w + 2 * AP.click.ripplePx * ripple}
							height={button.h + 2 * AP.click.ripplePx * ripple}
							rx={button.h / 2 + AP.click.ripplePx * ripple}
							fill="none"
							stroke={C.pink}
							strokeWidth={10 * (1 - ripple)}
						/>
					) : null}
					<g transform={`translate(${bc.x} ${bc.y}) scale(${press})`}>
						<rect x={-button.w / 2} y={-button.h / 2} width={button.w} height={button.h} rx={button.h / 2} fill={clicked ? B.doneFill : B.fill} stroke={C.outline} strokeWidth={OUTLINE} />
						<line x1={-arrow * 1.3} y1={0} x2={arrow * 1.1} y2={0} stroke={C.outline} strokeWidth={OUTLINE * 1.6} strokeLinecap="round" />
						<polyline
							points={`${arrow * 0.1},${-arrow} ${arrow * 1.2},0 ${arrow * 0.1},${arrow}`}
							fill="none"
							stroke={C.outline}
							strokeWidth={OUTLINE * 1.6}
							strokeLinecap="round"
							strokeLinejoin="round"
						/>
					</g>
					{caretOn ? <rect x={caretX} y={midY - textPx * 0.5} width={AP.caret.widthPx} height={textPx} rx={2} fill={A.textColor} /> : null}
				</Canvas>
				<div style={{position: 'absolute', left: textX, top: midY - lineH(textPx) / 2, height: lineH(textPx), ...textStyle(textPx, A.textColor, 0)}}>
					{typed}
				</div>
				{c.t >= cursorAt ? (
					<Canvas width={W.width} height={height}>
						<g transform={`translate(${cursor.x} ${cursor.y}) scale(${AP.cursor.scale * press})`}>
							<Cursor />
						</g>
					</Canvas>
				) : null}
			</Pop>
		</Fit>
	);
};

export const apply: TemplateDef<FxApply> = {
	View: ApplyView,
	endS: (data) => applyTimes(data.url).clickAt + Math.max(AP.click.durationS, AP.click.rippleS),
};

// --- steps: numbered circles in a column light up top to bottom ------------------------------

const SP = FX.templates.steps;

const stepAtS = (i: number) => SP.firstAtS + i * SP.stepS;

const StepsView: React.FC<TemplateProps<FxSteps>> = ({data, c, box, maxScale, measure}) => {
	const LI = SP.list;
	const o = OUTLINE;
	// Each label on one line and, with two or more words, split where its wider line is narrowest.
	const em = (t: string) => textWidth(measure, t, 100) / 100;
	const splits = data.labels.map((l) => {
		const words = l.trim().split(/\s+/);
		const cuts = words.slice(1).map((_, k) => [words.slice(0, k + 1).join(' '), words.slice(k + 1).join(' ')]);
		return cuts.length === 0 ? null : cuts.reduce((best, cut) => (Math.max(...cut.map(em)) < Math.max(...best.map(em)) ? cut : best));
	});
	// Laid out to fill the box at the placement's full scale.
	const {width, height, r, labelPx, cx, ys, labelX, lines} = stepsLayout(
		data.labels.map((l, i) => [em(l), splits[i] ? Math.max(...(splits[i] as string[]).map(em)) : null]),
		{
			boxW: box.width / maxScale,
			boxH: box.height / maxScale,
			rRatio: LI.rRatio,
			rMin: LI.rMin,
			rMax: LI.rMax,
			outlinePx: o,
			minLinePx: LI.minLinePx,
			maxLinePx: LI.maxLinePx,
			labelMaxPx: SP.labelPx,
			labelMinPx: SP.labelMinPx,
			labelStepPx: 1,
			labelGapPx: SP.labelGapPx,
			rowGapPx: SP.rowGapPx,
			lineHeight: FX.text.lineHeight,
		},
	);
	const numberPx = r * SP.numberRatio;
	const inScale = lerp(SP.in.fromScale, 1, pop(c, SP.in.atS, SP.in.durationS));
	const L = SP.line;
	const punchAt = (i: number) => (c.t >= stepAtS(i) ? punch(c, stepAtS(i), SP.popS, SP.punchScale) : 1);

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			<Pop x={0} y={0} width={width} height={height} scale={inScale}>
				<Canvas width={width} height={height}>
					{ys.slice(1).map((by, i) => {
						const ay = ys[i] + r;
						const ey = by - r;
						const q = ramp(c, stepAtS(i) + 0.08, SP.stepS - 0.08);
						return (
							<g key={i}>
								<line x1={cx} y1={ay} x2={cx} y2={ey} stroke={C.outline} strokeWidth={L.widthPx + 2 * o} strokeLinecap="round" />
								<line x1={cx} y1={ay} x2={cx} y2={ey} stroke={C.empty} strokeWidth={L.widthPx} strokeLinecap="round" />
								{q > 0 ? <line x1={cx} y1={ay} x2={cx} y2={lerp(ay, ey, q)} stroke={L.fill} strokeWidth={L.widthPx} strokeLinecap="round" /> : null}
							</g>
						);
					})}
					{ys.map((y, i) => (
						<g key={i} transform={`translate(${cx} ${y}) scale(${punchAt(i)})`}>
							<circle r={r} fill={c.t >= stepAtS(i) ? SP.lit : SP.unlit} stroke={C.outline} strokeWidth={o} />
						</g>
					))}
				</Canvas>
				{ys.map((y, i) => (
					<Label key={i} cx={cx} top={y - lineH(numberPx) / 2} px={numberPx} color={c.t >= stepAtS(i) ? C.outline : SP.numberUnlit} strokeEm={0} scale={punchAt(i)}>
						{i + 1}
					</Label>
				))}
				{ys.map((y, i) => {
					// Left-aligned, the label's lines centred on the circle as a block.
					const text = lines[i] === 2 ? (splits[i] as string[]) : [data.labels[i]];
					const top = y - (text.length * lineH(labelPx)) / 2;
					const scale = pop(c, stepAtS(i), SP.popS);
					return text.map((line, k) => (
						<Label key={`${i}-${k}`} cx={labelX + (em(line) * labelPx) / 2} top={top + k * lineH(labelPx)} px={labelPx} color={SP.labelColor} scale={scale}>
							{line}
						</Label>
					));
				})}
			</Pop>
		</Fit>
	);
};

export const steps: TemplateDef<FxSteps> = {
	View: StepsView,
	endS: (data) => stepAtS(data.labels.length - 1) + SP.popS,
	check: (data) => {
		const n = data.labels.length;
		if (n < SP.minSteps || n > SP.maxSteps) {
			throw new Error(`Fx steps: ${n} labels; it takes ${SP.minSteps}-${SP.maxSteps}`);
		}
	},
};
