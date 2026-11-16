// Pure layout for the fact-graphic templates (remotion/src/fx/): the direction to a
// destination as on a world map, abroad's layout and the steps list. Text widths and
// sizes come in as numbers, so the tests run this file under plain Node
// (tests/test_fx_layout.py). Keep it free of imports.

export type Pt = [number, number];
export type Rect = {x: number; y: number; w: number; h: number};

const RAD = Math.PI / 180;

// The way from `from` to `to` ([lon, lat] in degrees) on a world map (Mercator, so a
// constant compass course), in degrees counterclockwise from east: 0 east, 90 north,
// 180 west, -90 south. Longitude goes the shorter way round.
export const mapDirectionDeg = (from: Pt, to: Pt): number => {
	const dLon = ((((to[0] - from[0] + 180) % 360) + 360) % 360) - 180;
	const psi = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * RAD) / 2));
	return Math.atan2(psi(to[1]) - psi(from[1]), dLon * RAD) / RAD;
};

// A direction in degrees (as above) as a screen unit vector (y down).
export const screenDir = (deg: number): Pt => [Math.cos(deg * RAD), -Math.sin(deg * RAD)];

const move = (r: Rect, dx: number, dy: number): Rect => ({x: r.x + dx, y: r.y + dy, w: r.w, h: r.h});

export const union = (rs: Rect[]): Rect => {
	const x = Math.min(...rs.map((r) => r.x));
	const y = Math.min(...rs.map((r) => r.y));
	return {x, y, w: Math.max(...rs.map((r) => r.x + r.w)) - x, h: Math.max(...rs.map((r) => r.y + r.h)) - y};
};

// How far apart two rects are: the larger of the gaps along x and along y (negative when
// they overlap).
export const rectGap = (a: Rect, b: Rect): number =>
	Math.max(a.x - (b.x + b.w), b.x - (a.x + a.w), a.y - (b.y + b.h), b.y - (a.y + a.h));

// How far `moving`, slid by d * u, has to go (d >= 0) to end up at least `gap` from every
// rect in `fixed` and stay clear as it goes on: per pair, the nearest d at which it has
// passed the fixed rect along x or y in the direction it moves.
export const clearDistance = (fixed: Rect[], moving: Rect[], u: Pt, gap: number): number => {
	let d = 0;
	for (const a of fixed) {
		for (const b of moving) {
			const along: number[] = [];
			if (u[0] > 1e-9) along.push((a.x + a.w + gap - b.x) / u[0]);
			if (u[0] < -1e-9) along.push((a.x - gap - (b.x + b.w)) / u[0]);
			if (u[1] > 1e-9) along.push((a.y + a.h + gap - b.y) / u[1]);
			if (u[1] < -1e-9) along.push((a.y - gap - (b.y + b.h)) / u[1]);
			d = Math.max(d, Math.max(0, Math.min(...along)));
		}
	}
	return d;
};

const bezier = (p: [Pt, Pt, Pt], t: number): Pt => [
	(1 - t) ** 2 * p[0][0] + 2 * (1 - t) * t * p[1][0] + t ** 2 * p[2][0],
	(1 - t) ** 2 * p[0][1] + 2 * (1 - t) * t * p[1][1] + t ** 2 * p[2][1],
];

// Distance from a point to a rect (0 inside it).
const pointRectDist = (p: Pt, r: Rect): number =>
	Math.hypot(Math.max(r.x - p[0], 0, p[0] - (r.x + r.w)), Math.max(r.y - p[1], 0, p[1] - (r.y + r.h)));

// --- abroad ------------------------------------------------------------------------------

export type AbroadSpec = {
	// Georgia: the map's size and the flight's start in map pixels; its label under the map,
	// or over it when the flight heads down more steeply than originLabelAboveSin.
	mapW: number;
	mapH: number;
	from: Pt;
	originLabelW: number;
	originLabelH: number;
	originLabelGapPx: number;
	originLabelAboveSin: number;
	// The destination pin: head radius pinR, its centre pinH above the tip.
	pinR: number;
	pinH: number;
	outlinePx: number;
	// The destination's name: over or under the pin, whichever lands nearer it.
	labelW: number;
	labelH: number;
	labelGapPx: number;
	// The pin is at least flightGapPx clear of Georgia and minFlightPx from the start.
	flightGapPx: number;
	minFlightPx: number;
	// Anything else kept gapPx apart.
	gapPx: number;
	// The arc bows out (on the upper side) by liftRatio of the flight's length at its
	// control point; the plane (planeR round) stops clearPx short of the pin.
	liftRatio: number;
	planeR: number;
	clearPx: number;
	// Samples along the flight for the plane's clearance.
	samples: number;
};

export type AbroadLayout = {
	width: number;
	height: number;
	// Top left of the map, and the origin label's box.
	map: Pt;
	originLabel: Rect;
	// The pin's head centre and its tip.
	head: Pt;
	tip: Pt;
	// The flight: a quadratic curve from the start to the pin's head; the plane stops at
	// `stop` along it.
	path: [Pt, Pt, Pt];
	stop: number;
	label: Rect;
	labelAbove: boolean;
};

// Georgia and the destination on a line in the destination's direction (deg, as
// mapDirectionDeg), the flight from Georgia's start city to the pin, the destination's
// name next to the pin clear of everything else; everything moved so the box starts at 0, 0.
export const abroadLayout = (deg: number, s: AbroadSpec): AbroadLayout => {
	const u = screenDir(deg);
	const o = s.outlinePx;
	// Around the start (0, 0).
	const mapRect: Rect = {x: -s.from[0] - o, y: -s.from[1] - o, w: s.mapW + 2 * o, h: s.mapH + 2 * o};
	const labelAboveMap = u[1] > s.originLabelAboveSin;
	const originLabel: Rect = {
		x: mapRect.x + mapRect.w / 2 - s.originLabelW / 2,
		y: labelAboveMap ? mapRect.y - s.originLabelGapPx - s.originLabelH : mapRect.y + mapRect.h + s.originLabelGapPx,
		w: s.originLabelW,
		h: s.originLabelH,
	};
	const georgia = [mapRect, originLabel];
	// The pin around its head centre.
	const pinAt = (head: Pt): Rect => ({x: head[0] - s.pinR - o, y: head[1] - s.pinR - o, w: 2 * (s.pinR + o), h: s.pinR + s.pinH + 2 * o});
	const d = Math.max(s.minFlightPx, clearDistance(georgia, [pinAt([0, 0])], u, s.flightGapPx));
	const head: Pt = [d * u[0], d * u[1]];
	const pin = pinAt(head);

	// The bow: towards the normal on the upper side (a vertical flight bows to one side).
	const n: Pt = u[0] >= 0 ? [u[1], -u[0]] : [-u[1], u[0]];
	const lift = s.liftRatio * d;
	const path: [Pt, Pt, Pt] = [[0, 0], [head[0] / 2 + n[0] * lift, head[1] / 2 + n[1] * lift], head];
	let stop = 1;
	while (stop > 0.5 && pointRectDist(bezier(path, stop), pin) < s.planeR + s.clearPx) {
		stop -= 0.005;
	}
	const plane = Array.from({length: s.samples + 1}, (_, i) => {
		const p = bezier(path, (stop * i) / s.samples);
		return {x: p[0] - s.planeR, y: p[1] - s.planeR, w: 2 * s.planeR, h: 2 * s.planeR};
	});

	// The name: centred on the pin, slid sideways to stay over what's already there, then
	// over (or under) the pin and anything below (above) it that it spans.
	const core = union([...georgia, pin, ...plane]);
	const w = s.labelW;
	const centred = head[0] - w / 2;
	const x = w <= core.w ? Math.min(Math.max(centred, core.x), core.x + core.w - w) : Math.min(Math.max(centred, core.x + core.w - w), core.x);
	const spans = (r: Rect) => r.x < x + w + s.gapPx && r.x + r.w > x - s.gapPx;
	const others = [...georgia, ...plane].filter(spans);
	const aboveY = Math.min(pin.y - s.labelGapPx, ...others.map((r) => r.y - s.gapPx)) - s.labelH;
	const belowY = Math.max(pin.y + pin.h + s.labelGapPx, ...others.map((r) => r.y + r.h + s.gapPx));
	const labelAbove = pin.y - (aboveY + s.labelH) < belowY - (pin.y + pin.h);
	const label: Rect = {x, y: labelAbove ? aboveY : belowY, w, h: s.labelH};

	const box = union([...georgia, pin, ...plane, label]);
	const dx = o - box.x;
	const dy = o - box.y;
	const at = (p: Pt): Pt => [p[0] + dx, p[1] + dy];
	return {
		width: box.w + 2 * o,
		height: box.h + 2 * o,
		map: [mapRect.x + o + dx, mapRect.y + o + dy],
		originLabel: move(originLabel, dx, dy),
		head: at(head),
		tip: at([head[0], head[1] + s.pinH]),
		path: [at(path[0]), at(path[1]), at(path[2])],
		stop,
		label: move(label, dx, dy),
		labelAbove,
	};
};

// --- steps -------------------------------------------------------------------------------

export type StepsSpec = {
	// The box the list fills (design pixels); it only grows past it when even labelMinPx
	// labels don't fit.
	boxW: number;
	boxH: number;
	// Circle radius: rRatio of the label size, kept within rMin..rMax.
	rRatio: number;
	rMin: number;
	rMax: number;
	outlinePx: number;
	// The connecting line between two circles: at least minLinePx, stretched up to
	// maxLinePx to fill the box's height.
	minLinePx: number;
	maxLinePx: number;
	// One size for every label, the largest from labelMaxPx down (by labelStepPx) at which
	// the list fits the box: each label labelGapPx right of its circle, neighbouring rows'
	// labels rowGapPx apart. A line of text is lineHeight of the size tall.
	labelMaxPx: number;
	labelMinPx: number;
	labelStepPx: number;
	labelGapPx: number;
	rowGapPx: number;
	lineHeight: number;
};

// The circles' centres at (cx, ys[i]); each label starts at labelX, vertically centred on
// its circle, on one line (lines 1) or, a label of two or more words, two (lines 2).
export type StepsLayout = {width: number; height: number; r: number; labelPx: number; cx: number; ys: number[]; labelX: number; lines: number[]};

// 2-4 circles top to bottom, a label to the right of each. labelEm: per label, its width
// at 1 px (outline included) on one line and, for a label that can break, its wider line
// on two (else null). One size for all. Every label stays on one line, the size shrinking
// for the longest; only when even labelMinPx doesn't fit that way do the labels too wide
// for one line go onto two, the size starting again from labelMaxPx.
export const stepsLayout = (labelEm: [number, number | null][], s: StepsSpec): StepsLayout => {
	const n = labelEm.length;
	const o = s.outlinePx;
	// wrap: 'none' keeps every label on one line; 'fit' breaks one only when that makes it
	// fit the width, 'all' every one too wide for one line.
	const place = (px: number, wrap: 'fit' | 'all' | 'none') => {
		const r = Math.min(s.rMax, Math.max(s.rMin, s.rRatio * px));
		const d = 2 * (r + o);
		const labelX = d + s.labelGapPx;
		const avail = s.boxW - labelX;
		const lines = labelEm.map(([one, two]) =>
			two !== null && one * px > avail && (wrap === 'all' || (wrap === 'fit' && two * px <= avail)) ? 2 : 1,
		);
		const ws = labelEm.map(([one, two], i) => (lines[i] === 2 ? (two as number) : one) * px);
		const hs = lines.map((k) => k * s.lineHeight * px);
		const pitchMin = Math.max(d + s.minLinePx, ...hs.slice(1).map((h, i) => (hs[i] + h) / 2 + s.rowGapPx));
		const top = Math.max(d / 2, hs[0] / 2);
		const bottom = Math.max(d / 2, hs[n - 1] / 2);
		const pitch = Math.max(pitchMin, Math.min(d + s.maxLinePx, (s.boxH - top - bottom) / (n - 1)));
		const width = labelX + Math.max(...ws);
		const height = top + (n - 1) * pitch + bottom;
		const fits = width <= s.boxW + 1e-6 && height <= s.boxH + 1e-6;
		return {fits, layout: {width, height, r, labelPx: px, cx: r + o, ys: labelEm.map((_, i) => top + i * pitch), labelX, lines}};
	};
	for (const wrap of ['none', 'fit'] as const) {
		for (let px = s.labelMaxPx; px >= s.labelMinPx; px -= s.labelStepPx) {
			const p = place(px, wrap);
			if (p.fits) {
				return p.layout;
			}
		}
	}
	// Even the smallest labels don't fit: the list grows past the box (and is scaled down
	// into it), with or without breaking the long labels, whichever needs less scaling.
	const shrink = (l: StepsLayout) => Math.min(s.boxW / l.width, s.boxH / l.height);
	const [a, b] = [place(s.labelMinPx, 'all').layout, place(s.labelMinPx, 'none').layout];
	return shrink(a) >= shrink(b) ? a : b;
};
