import {Easing} from 'remotion';
import {FxAbroad, FxFormat, FxLocation} from '../types';
import {C, Canvas, FX, Fit, Label, OUTLINE, Pop, TemplateDef, TemplateProps, lerp, lineH, pop, ramp, textWidth} from './kit';
import destinations from '@channel/graphics/destinations.json';
import {Pt, abroadLayout, mapDirectionDeg} from './layout';
import {Building, GeorgiaMap, Laptop, Pin, Plane, cityLonLat, cityPoint, georgiaHeight} from './shapes';

// Easing.bounce first touches down this far into its duration.
const BOUNCE_HIT = 0.364;

// --- location: a pin drops on the city on a map of Georgia --------------------------------

const LO = FX.templates.location;

const LocationView: React.FC<TemplateProps<FxLocation>> = ({data, c, box, maxScale, measure}) => {
	const P = LO.pin;
	const U = LO.unknownPin;
	const mapW = LO.mapWidth;
	const mapH = georgiaHeight(mapW);
	const city = cityPoint(data.city, mapW);
	const labelW = textWidth(measure, data.city, LO.labelPx);
	// A city the map doesn't know: no pin on the map, a small one beside the label.
	const rowW = city ? labelW : U.r * 2 + U.gapPx + labelW;
	const topRoom = city ? Math.max(0, P.h + P.r + OUTLINE - city[1]) : 0;
	const width = Math.max(mapW, rowW);
	const height = topRoom + mapH + LO.labelGapPx + lineH(LO.labelPx);
	const mapX = (width - mapW) / 2;
	const mapY = topRoom;
	const labelTop = mapY + mapH + LO.labelGapPx;
	const labelScale = pop(c, LO.labelAtS, LO.labelPopS);

	const inScale = lerp(LO.in.fromScale, 1, pop(c, LO.in.atS, LO.in.durationS));
	const drop = -P.dropPx * (1 - ramp(c, P.atS, P.dropS, Easing.bounce));
	const hitS = P.atS + BOUNCE_HIT * P.dropS;
	const ripple = ramp(c, hitS, LO.ripple.durationS);
	// The city's own dot under the pin's tip, popping in as the pin lands: it marks the
	// spot, since the pin's head stands well above it.
	const dotScale = pop(c, hitS, LO.dot.popS);

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			<Pop x={mapX} y={mapY} width={mapW} height={mapH} scale={inScale}>
				<Canvas width={mapW} height={mapH}>
					<GeorgiaMap width={mapW} fill={LO.land} outlinePx={LO.mapOutlinePx} />
				</Canvas>
			</Pop>
			{city ? (
				<Canvas width={width} height={height}>
					{c.t >= hitS && ripple < 1 ? (
						<ellipse
							cx={mapX + city[0]}
							cy={mapY + city[1]}
							rx={LO.ripple.radiusPx * ripple}
							ry={LO.ripple.radiusPx * ripple * 0.42}
							fill="none"
							stroke={LO.ripple.color}
							strokeWidth={LO.ripple.widthPx * (1 - ripple)}
						/>
					) : null}
					{dotScale > 0 ? (
						<circle
							cx={mapX + city[0]}
							cy={mapY + city[1]}
							r={LO.dot.r * dotScale}
							fill={LO.dot.fill}
							stroke={C.outline}
							strokeWidth={OUTLINE * 0.8}
						/>
					) : null}
					{c.t >= P.atS ? (
						<g transform={`translate(${mapX + city[0]} ${mapY + city[1] + drop})`}>
							<Pin r={P.r} h={P.h} />
						</g>
					) : null}
				</Canvas>
			) : null}
			<Pop x={(width - rowW) / 2} y={labelTop} width={rowW} height={lineH(LO.labelPx)} scale={labelScale}>
				{city ? null : (
					<Canvas width={rowW} height={lineH(LO.labelPx)}>
						<g transform={`translate(${U.r} ${lineH(LO.labelPx) / 2 + (U.h + U.r) / 2})`}>
							<Pin r={U.r} h={U.h} />
						</g>
					</Canvas>
				)}
				<Label cx={rowW - labelW / 2} top={0} px={LO.labelPx} color={LO.labelColor}>
					{data.city}
				</Label>
			</Pop>
		</Fit>
	);
};

export const location: TemplateDef<FxLocation> = {
	View: LocationView,
	endS: () =>
		Math.max(
			LO.in.atS + LO.in.durationS,
			LO.pin.atS + LO.pin.dropS,
			LO.pin.atS + BOUNCE_HIT * LO.pin.dropS + Math.max(LO.ripple.durationS, LO.dot.popS),
			LO.labelAtS + LO.labelPopS,
		),
};

// --- abroad: a plane flies from Georgia to the destination ------------------------------------

const AB = FX.templates.abroad;
const DEST = destinations as unknown as {places: Record<string, Pt>; aliases: Record<string, string>};

// Where the destination lies from Georgia's start city, as on a world map (layout.ts);
// one destinations.json doesn't know flies at abroad.unknownDeg.
const abroadDeg = (destination: string): number => {
	const name = destination.trim().replace(/\s+/g, ' ');
	const to = DEST.places[DEST.aliases[name] ?? name];
	const from = cityLonLat(AB.origin.from);
	return to && from ? mapDirectionDeg(from, to) : AB.unknownDeg;
};

const bezier = (p: [Pt, Pt, Pt], u: number): Pt => [
	(1 - u) ** 2 * p[0][0] + 2 * (1 - u) * u * p[1][0] + u ** 2 * p[2][0],
	(1 - u) ** 2 * p[0][1] + 2 * (1 - u) * u * p[1][1] + u ** 2 * p[2][1],
];
const tangentDeg = (p: [Pt, Pt, Pt], u: number): number => {
	const dx = 2 * (1 - u) * (p[1][0] - p[0][0]) + 2 * u * (p[2][0] - p[1][0]);
	const dy = 2 * (1 - u) * (p[1][1] - p[0][1]) + 2 * u * (p[2][1] - p[1][1]);
	return (Math.atan2(dy, dx) * 180) / Math.PI;
};

const AbroadView: React.FC<TemplateProps<FxAbroad>> = ({data, c, box, maxScale, measure}) => {
	const O = AB.origin;
	const D = AB.dest;
	const A = AB.arc;
	const PL = AB.plane;
	const mapH = georgiaHeight(O.mapWidth);
	const from = cityPoint(O.from, O.mapWidth) ?? [O.mapWidth / 2, mapH / 2];
	// The name as big as fits maxLabelWidthPx; a long name of two or more words that one
	// line would shrink below wrapBelowPx goes onto two, split where its wider line is
	// narrowest, when that's bigger.
	const sizeFor = (w: number) => Math.min(D.labelPx, (D.labelPx * D.maxLabelWidthPx) / w);
	const words = data.destination.trim().split(/\s+/);
	const oneLine = {lines: [data.destination], w: textWidth(measure, data.destination, D.labelPx)};
	const name = words
		.slice(1)
		.map((_, k) => [words.slice(0, k + 1).join(' '), words.slice(k + 1).join(' ')])
		.map((lines) => ({lines, w: Math.max(...lines.map((l) => textWidth(measure, l, D.labelPx)))}))
		.reduce((best, two) => (sizeFor(oneLine.w) < D.wrapBelowPx && sizeFor(two.w) > sizeFor(best.w) ? two : best), oneLine);
	const nameW = name.w;
	const labelPx = sizeFor(nameW);
	const L = abroadLayout(abroadDeg(data.destination), {
		mapW: O.mapWidth,
		mapH,
		from,
		originLabelW: textWidth(measure, O.label, O.labelPx),
		originLabelH: lineH(O.labelPx),
		originLabelGapPx: O.labelGapPx,
		originLabelAboveSin: O.labelAboveSin,
		pinR: D.pinR,
		pinH: D.pinH,
		outlinePx: OUTLINE,
		labelW: Math.min(nameW, D.maxLabelWidthPx),
		labelH: name.lines.length * lineH(labelPx),
		labelGapPx: D.labelGapPx,
		flightGapPx: AB.layout.flightGapPx,
		minFlightPx: AB.layout.minFlightPx,
		gapPx: AB.layout.gapPx,
		liftRatio: A.liftRatio,
		planeR: PL.size / 2,
		clearPx: PL.clearPx,
		samples: AB.layout.samples,
	});
	const u = L.stop * ramp(c, PL.atS, PL.flyS, Easing.inOut(Easing.quad));
	const plane = bezier(L.path, u);
	// The trail so far: the curve's first part, split at u.
	const q1: Pt = [lerp(L.path[0][0], L.path[1][0], u), lerp(L.path[0][1], L.path[1][1], u)];
	const inScale = lerp(AB.in.fromScale, 1, pop(c, AB.in.atS, AB.in.durationS));
	const pinScale = pop(c, D.pinAtS, D.pinPopS, 10);
	const mapBox = {x: L.map[0], y: L.map[1], w: O.mapWidth, h: mapH};

	// Georgia and its name pop in about the map's centre.
	const mapCentre = (r: {x: number; y: number}) => `${mapBox.x + mapBox.w / 2 - r.x}px ${mapBox.y + mapBox.h / 2 - r.y}px`;

	return (
		<Fit box={box} maxScale={maxScale} width={L.width} height={L.height}>
			<Pop x={mapBox.x} y={mapBox.y} width={mapBox.w} height={mapBox.h} scale={inScale} origin={mapCentre(mapBox)}>
				<Canvas width={O.mapWidth} height={mapH}>
					<GeorgiaMap width={O.mapWidth} fill={FX.templates.location.land} outlinePx={FX.templates.location.mapOutlinePx} />
					<circle cx={from[0]} cy={from[1]} r={O.dotR} fill={C.white} stroke={C.outline} strokeWidth={OUTLINE * 0.8} />
				</Canvas>
			</Pop>
			<Canvas width={L.width} height={L.height}>
				{c.t >= PL.atS ? (
					<path
						d={`M ${L.path[0][0]} ${L.path[0][1]} Q ${q1[0]} ${q1[1]} ${plane[0]} ${plane[1]}`}
						fill="none"
						stroke={A.color}
						strokeWidth={A.widthPx}
						strokeDasharray={`${A.dashPx} ${A.gapPx}`}
						strokeLinecap="round"
					/>
				) : null}
			</Canvas>
			<Pop x={L.originLabel.x} y={L.originLabel.y} width={L.originLabel.w} height={L.originLabel.h} scale={inScale} origin={mapCentre(L.originLabel)}>
				<Label cx={L.originLabel.w / 2} top={0} px={O.labelPx} color={O.labelColor}>
					{O.label}
				</Label>
			</Pop>
			<Canvas width={L.width} height={L.height}>
				{pinScale > 0 ? (
					<g transform={`translate(${L.tip[0]} ${L.tip[1]}) scale(${pinScale})`}>
						<Pin r={D.pinR} h={D.pinH} />
					</g>
				) : null}
			</Canvas>
			{name.lines.map((line, k) => (
				<Label
					key={k}
					cx={L.label.x + L.label.w / 2}
					top={L.label.y + k * lineH(labelPx)}
					px={labelPx}
					color={D.labelColor}
					scale={pop(c, D.labelAtS, D.labelPopS, 10)}
				>
					{line}
				</Label>
			))}
			<Canvas width={L.width} height={L.height}>
				{c.t >= PL.atS ? (
					<g transform={`translate(${plane[0]} ${plane[1]}) rotate(${tangentDeg(L.path, u)}) scale(${pop(c, PL.atS, 0.25)})`}>
						<Plane size={PL.size} />
					</g>
				) : null}
			</Canvas>
		</Fit>
	);
};

export const abroad: TemplateDef<FxAbroad> = {
	View: AbroadView,
	endS: () =>
		Math.max(AB.in.atS + AB.in.durationS, AB.plane.atS + AB.plane.flyS, AB.dest.pinAtS + AB.dest.pinPopS, AB.dest.labelAtS + AB.dest.labelPopS),
};

// --- format: online (a laptop) or in person (a building) flips in -----------------------------

const FO = FX.templates.format;
const ICONS = {online: {width: 360, height: 250, Icon: Laptop}, onsite: {width: 340, height: 270, Icon: Building}};

const FormatView: React.FC<TemplateProps<FxFormat>> = ({data, c, box, maxScale, measure}) => {
	const icon = ICONS[data.mode];
	const label = data.label ?? FO.labels[data.mode];
	const iconH = Math.max(ICONS.online.height, ICONS.onsite.height);
	const width = Math.max(icon.width, textWidth(measure, label, FO.labelPx));
	const height = iconH + FO.gapPx + lineH(FO.labelPx);
	const F = FO.flip;
	const flip = pop(c, F.atS, F.durationS, F.damping);

	return (
		<Fit box={box} maxScale={maxScale} width={width} height={height}>
			{c.t >= F.atS ? (
				<div
					style={{
						position: 'absolute',
						left: (width - icon.width) / 2,
						top: iconH - icon.height,
						width: icon.width,
						height: icon.height,
						transform: `perspective(${F.perspectivePx}px) rotateY(${lerp(F.fromDeg, 0, flip)}deg)`,
						transformOrigin: '50% 50%',
					}}
				>
					<Canvas width={icon.width} height={icon.height}>
						<icon.Icon />
					</Canvas>
				</div>
			) : null}
			<Label cx={width / 2} top={iconH + FO.gapPx} px={FO.labelPx} color={FO.labelColor} scale={pop(c, FO.labelAtS, FO.labelPopS, 10)}>
				{label}
			</Label>
		</Fit>
	);
};

export const format: TemplateDef<FxFormat> = {
	View: FormatView,
	endS: () => Math.max(FO.flip.atS + FO.flip.durationS, FO.labelAtS + FO.labelPopS),
	check: (data) => {
		if (!(data.mode in ICONS)) {
			throw new Error(`Fx format: mode "${data.mode}"; expected one of: ${Object.keys(ICONS).join(', ')}`);
		}
	},
};
