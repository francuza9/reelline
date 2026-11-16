import georgia from '@channel/graphics/georgia.json';
import {C, FX, OUTLINE} from './kit';

// Flat SVG shapes for the templates, drawn here (no copied art): fills from fx.json,
// #14120C outlines. Each is drawn about its own origin (noted per shape); place it with
// a <g transform>.

const stroke = {stroke: C.outline, strokeWidth: OUTLINE, strokeLinejoin: 'round' as const, strokeLinecap: 'round' as const};

const pts = (p: [number, number][]) => p.map((q) => q.join(',')).join(' ');
const mirror = (p: [number, number][]): [number, number][] => p.map(([x, y]) => [x, -y]);
const mirrorX = (p: [number, number][], w: number): [number, number][] => p.map(([x, y]) => [w - x, y]);

// A tick in a box of size s, and its length (for drawing it in with a dash offset).
export const tickPoints = (s: number): [number, number][] => [
	[0.24 * s, 0.53 * s],
	[0.43 * s, 0.72 * s],
	[0.78 * s, 0.3 * s],
];
export const pathLength = (p: [number, number][]) => p.slice(1).reduce((sum, q, i) => sum + Math.hypot(q[0] - p[i][0], q[1] - p[i][1]), 0);

// A map pin, its tip at the origin: a round head of radius r centred h above the tip.
export const Pin: React.FC<{r: number; h: number; fill?: string}> = ({r, h, fill = C.pink}) => {
	// Where the sides leave the head: tangent from the tip.
	const b = Math.acos(r / h);
	const sx = r * Math.sin(b);
	const sy = -h + r * Math.cos(b);
	return (
		<g>
			<path d={`M 0 0 L ${-sx} ${sy} A ${r} ${r} 0 1 1 ${sx} ${sy} Z`} fill={fill} {...stroke} />
			<circle cx={0} cy={-h} r={r * 0.42} fill={C.white} {...stroke} />
		</g>
	);
};

// A plane seen from above, pointing along +x, centred on the origin, `size` long.
export const Plane: React.FC<{size: number}> = ({size}) => {
	const k = size / 100;
	const s = (p: [number, number][]): [number, number][] => p.map(([x, y]) => [x * k, y * k]);
	const wing: [number, number][] = [
		[10, -8],
		[-10, -50],
		[-24, -50],
		[-12, -8],
	];
	const tail: [number, number][] = [
		[-34, -6],
		[-44, -24],
		[-51, -24],
		[-47, -6],
	];
	return (
		<g>
			{[wing, mirror(wing), tail, mirror(tail)].map((p, i) => (
				<polygon key={i} points={pts(s(p))} fill={C.white} {...stroke} />
			))}
			<rect x={-50 * k} y={-10 * k} width={102 * k} height={20 * k} rx={10 * k} fill={C.white} {...stroke} />
			<ellipse cx={34 * k} cy={0} rx={8 * k} ry={5 * k} fill={C.pink} {...stroke} strokeWidth={OUTLINE * 0.7} />
		</g>
	);
};

// A rocket pointing up, the bottom of its body at the origin, 260 tall; `flame` scales
// the flame under it (0: none).
export const Rocket: React.FC<{flame: number}> = ({flame}) => {
	const fin: [number, number][] = [
		[-38, -78],
		[-74, -10],
		[-74, 22],
		[-38, 0],
	];
	return (
		<g>
			{flame > 0 ? (
				<g transform={`translate(0 12) scale(1 ${flame})`}>
					<path d="M -24 0 Q 0 120 24 0 Z" fill={C.yellow} {...stroke} />
					<path d="M -11 0 Q 0 64 11 0 Z" fill={C.white} {...stroke} strokeWidth={OUTLINE * 0.7} />
				</g>
			) : null}
			<polygon points={pts(fin)} fill={C.pink} {...stroke} />
			<polygon points={pts(mirrorX(fin, 0))} fill={C.pink} {...stroke} />
			<rect x={-26} y={-4} width={52} height={18} rx={4} fill={C.bezel} {...stroke} />
			<path d="M -38 0 L -38 -150 Q -38 -228 0 -260 Q 38 -228 38 -150 L 38 0 Z" fill={C.white} {...stroke} />
			<path d="M -36 -176 Q -30 -232 0 -260 Q 30 -232 36 -176 Z" fill={C.pink} {...stroke} />
			<circle cx={0} cy={-112} r={22} fill={C.panel} {...stroke} />
			<circle cx={-7} cy={-119} r={6} fill={C.white} />
			<rect x={-6} y={-60} width={12} height={46} rx={6} fill={C.pink} {...stroke} strokeWidth={OUTLINE * 0.7} />
		</g>
	);
};

// A trophy in a 260 x 290 box, top left at the origin.
export const Trophy: React.FC = () => (
	<g>
		{[
			'M 62 58 C 2 52 6 150 84 150',
			'M 198 58 C 258 52 254 150 176 150',
		].map((d, i) => (
			<g key={i}>
				<path d={d} fill="none" stroke={C.outline} strokeWidth={30} strokeLinecap="round" />
				<path d={d} fill="none" stroke={C.yellow} strokeWidth={30 - 2 * OUTLINE} strokeLinecap="round" />
			</g>
		))}
		<rect x={112} y={176} width={36} height={50} fill={C.coinSide} {...stroke} />
		<path d="M 52 30 L 208 30 L 203 108 Q 192 186 130 188 Q 68 186 57 108 Z" fill={C.yellow} {...stroke} />
		<rect x={40} y={18} width={180} height={28} rx={10} fill={C.yellow} {...stroke} />
		<rect x={80} y={222} width={100} height={26} rx={6} fill={C.yellow} {...stroke} />
		<rect x={58} y={244} width={144} height={42} rx={8} fill={C.bezel} {...stroke} />
		<rect x={96} y={256} width={68} height={18} rx={4} fill={C.white} {...stroke} strokeWidth={OUTLINE * 0.6} />
		<Star cx={130} cy={102} r={34} fill={C.white} />
	</g>
);

// A five-pointed star centred on (cx, cy).
export const Star: React.FC<{cx: number; cy: number; r: number; fill: string}> = ({cx, cy, r, fill}) => {
	const p: [number, number][] = Array.from({length: 10}, (_, i) => {
		const a = -Math.PI / 2 + (i * Math.PI) / 5;
		const rr = i % 2 === 0 ? r : r * 0.45;
		return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)];
	});
	return <polygon points={pts(p)} fill={fill} {...stroke} />;
};

// A four-pointed sparkle centred on (cx, cy).
export const Sparkle: React.FC<{cx: number; cy: number; r: number; fill: string}> = ({cx, cy, r, fill}) => {
	const q = r * 0.28;
	return (
		<path
			d={`M ${cx} ${cy - r} Q ${cx + q} ${cy - q} ${cx + r} ${cy} Q ${cx + q} ${cy + q} ${cx} ${cy + r} Q ${cx - q} ${cy + q} ${cx - r} ${cy} Q ${cx - q} ${cy - q} ${cx} ${cy - r} Z`}
			fill={fill}
			{...stroke}
		/>
	);
};

// A laptop in a 360 x 250 box, top left at the origin, a play button on its screen.
export const Laptop: React.FC = () => (
	<g>
		<rect x={40} y={0} width={280} height={194} rx={18} fill={C.bezel} {...stroke} />
		<rect x={58} y={18} width={244} height={158} rx={8} fill={C.pink} {...stroke} strokeWidth={OUTLINE * 0.7} />
		<circle cx={180} cy={97} r={44} fill={C.white} {...stroke} />
		<polygon points={pts([[166, 74], [166, 120], [204, 97]])} fill={C.outline} />
		<path d="M 8 202 L 352 202 L 334 238 Q 330 248 318 248 L 42 248 Q 30 248 26 238 Z" fill={C.light} {...stroke} />
		<rect x={150} y={202} width={60} height={12} rx={4} fill={C.bezel} />
	</g>
);

// A columned building (a university) in a 340 x 270 box, top left at the origin.
export const Building: React.FC = () => (
	<g>
		<polygon points={pts([[16, 92], [170, 8], [324, 92]])} fill={C.pink} {...stroke} />
		<circle cx={170} cy={62} r={15} fill={C.yellow} {...stroke} />
		<rect x={28} y={90} width={284} height={28} fill={C.white} {...stroke} />
		{[50, 117, 187, 254].map((x) => (
			<rect key={x} x={x} y={118} width={36} height={98} fill={C.white} {...stroke} />
		))}
		<rect x={18} y={214} width={304} height={26} fill={C.white} {...stroke} />
		<rect x={4} y={238} width={332} height={28} fill={C.white} {...stroke} />
	</g>
);

// A round seal of radius r centred on the origin: a scalloped rim, ribbon tails under it
// and a check in the middle.
export const Seal: React.FC<{r: number}> = ({r}) => {
	const n = FX.templates.certificate.seal.points;
	const rim: [number, number][] = Array.from({length: 2 * n}, (_, i) => {
		const a = (i * Math.PI) / n;
		const rr = i % 2 === 0 ? r : r * 0.86;
		return [rr * Math.cos(a), rr * Math.sin(a)];
	});
	const tail = (dir: number): [number, number][] => [
		[dir * r * 0.15, r * 0.4],
		[dir * r * 0.62, r * 1.45],
		[dir * r * 0.42, r * 1.32],
		[dir * r * 0.3, r * 1.5],
		[dir * r * 0.0, r * 0.6],
	];
	const tick: [number, number][] = [
		[-0.32 * r, 0.02 * r],
		[-0.08 * r, 0.26 * r],
		[0.34 * r, -0.24 * r],
	];
	return (
		<g>
			<polygon points={pts(tail(-1))} fill={C.pink} {...stroke} />
			<polygon points={pts(tail(1))} fill={C.pink} {...stroke} />
			<polygon points={pts(rim)} fill={C.pink} {...stroke} />
			<circle r={r * 0.64} fill={C.yellow} {...stroke} />
			<polyline points={pts(tick)} fill="none" stroke={C.outline} strokeWidth={r * 0.16} strokeLinecap="round" strokeLinejoin="round" />
		</g>
	);
};

// A mouse pointer, its tip at the origin, about 60 tall.
export const Cursor: React.FC = () => (
	<polygon
		points={pts([
			[0, 0],
			[0, 50],
			[13, 38],
			[23, 60],
			[33, 55],
			[23, 34],
			[40, 34],
		])}
		fill={C.white}
		{...stroke}
	/>
);

// One coin seen from the side, `rx` x `ry` face, `th` thick: its rim band, then the top
// face (centre cx, y).
export const Coin: React.FC<{cx: number; y: number; rx: number; ry: number; th: number}> = ({cx, y, rx, ry, th}) => (
	<g>
		<path
			d={`M ${cx - rx} ${y} L ${cx - rx} ${y + th} A ${rx} ${ry} 0 0 0 ${cx + rx} ${y + th} L ${cx + rx} ${y} A ${rx} ${ry} 0 0 1 ${cx - rx} ${y} Z`}
			fill={C.coinSide}
			{...stroke}
		/>
		<ellipse cx={cx} cy={y} rx={rx} ry={ry} fill={C.yellow} {...stroke} />
		<ellipse cx={cx} cy={y} rx={rx * 0.62} ry={ry * 0.62} fill="none" stroke={C.coinSide} strokeWidth={OUTLINE * 0.8} />
	</g>
);

// A cinema seat from the front in a size x size box, top left at the origin: backrest,
// cushion, two armrests.
export const Seat: React.FC<{size: number; fill: string; outlinePx: number}> = ({size: s, fill, outlinePx}) => {
	const part = (px: number, py: number, w: number, h: number, r: number) => (
		<rect x={px * s} y={py * s} width={w * s} height={h * s} rx={r * s} fill={fill} stroke={C.outline} strokeWidth={outlinePx} />
	);
	return (
		<g>
			{part(0.2, 0.04, 0.6, 0.62, 0.2)}
			{part(0.16, 0.58, 0.68, 0.24, 0.08)}
			{part(0.04, 0.4, 0.17, 0.54, 0.08)}
			{part(0.79, 0.4, 0.17, 0.54, 0.08)}
		</g>
	);
};

// --- Georgia -------------------------------------------------------------------------

const GEO = georgia as unknown as {refLatDeg: number; outline: [number, number][]; cities: Record<string, [number, number]>};
const KX = Math.cos((GEO.refLatDeg * Math.PI) / 180);
const LON0 = Math.min(...GEO.outline.map((p) => p[0]));
const LAT1 = Math.max(...GEO.outline.map((p) => p[1]));
const SPAN_X = (Math.max(...GEO.outline.map((p) => p[0])) - LON0) * KX;
const SPAN_Y = LAT1 - Math.min(...GEO.outline.map((p) => p[1]));

// The map's height for a width.
export const georgiaHeight = (width: number) => (width * SPAN_Y) / SPAN_X;

// [lon, lat] -> map pixels for a map `width` wide, top left at the origin.
export const georgiaPoint = (lonLat: [number, number], width: number): [number, number] => {
	const k = width / SPAN_X;
	return [(lonLat[0] - LON0) * KX * k, (LAT1 - lonLat[1]) * k];
};

// A city's [lon, lat], or null for one the map doesn't know.
export const cityLonLat = (city: string): [number, number] | null => GEO.cities[city.trim()] ?? null;

// A city's map position, or null for one the map doesn't know.
export const cityPoint = (city: string, width: number): [number, number] | null => {
	const lonLat = cityLonLat(city);
	return lonLat ? georgiaPoint(lonLat, width) : null;
};

// A closed curve through the points (Catmull-Rom, as cubic Beziers).
const smoothPath = (p: [number, number][]): string => {
	const n = p.length;
	const at = (i: number) => p[((i % n) + n) % n];
	const f = (v: number) => v.toFixed(1);
	const segments = p.map((_, i) => {
		const [a, b, c, d] = [at(i - 1), at(i), at(i + 1), at(i + 2)];
		const c1 = [b[0] + (c[0] - a[0]) / 6, b[1] + (c[1] - a[1]) / 6];
		const c2 = [c[0] - (d[0] - b[0]) / 6, c[1] - (d[1] - b[1]) / 6];
		return `C ${f(c1[0])} ${f(c1[1])} ${f(c2[0])} ${f(c2[1])} ${f(c[0])} ${f(c[1])}`;
	});
	return `M ${f(p[0][0])} ${f(p[0][1])} ${segments.join(' ')} Z`;
};

// The outline of Georgia, `width` wide, top left at the origin, smoothed through its points.
export const GeorgiaMap: React.FC<{width: number; fill: string; outlinePx?: number}> = ({width, fill, outlinePx = OUTLINE}) => (
	<path d={smoothPath(GEO.outline.map((p) => georgiaPoint(p, width)))} fill={fill} {...stroke} strokeWidth={outlinePx} />
);
