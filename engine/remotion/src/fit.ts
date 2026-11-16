// Shrink-to-fit layout for headline text (TV cards in Scene.tsx, the cover headline
// in Cover.tsx). Pure: text measurement comes in as a function, so the tests run this
// file under plain Node (tests/test_headline_fit.py). Keep it free of imports.

export type FitSpec = {
	// Largest size tried first, then shrunk in steps down to minFontPx.
	maxFontPx: number;
	minFontPx: number;
	stepPx: number;
	maxLines: number;
	lineHeight: number;
	// Text stroke width in em; counted into each line's width.
	strokeEm: number;
};

export type FitBox = {width: number; height: number};

// Width in pixels of `text` at `fontPx`.
export type Measure = (text: string, fontPx: number) => number;

export type HeadlineFit = {fontPx: number; lines: string[]};

// Greedy wrap of units (words, or words that must share a line) at `fontPx`; null if
// a single unit is wider than maxWidth.
export const wrapUnits = (units: string[], fontPx: number, maxWidth: number, spec: FitSpec, measure: Measure): string[] | null => {
	const stroke = spec.strokeEm * fontPx;
	const fits = (s: string) => measure(s, fontPx) + stroke <= maxWidth;
	const lines: string[] = [];
	for (const unit of units) {
		if (!fits(unit)) {
			return null;
		}
		const last = lines[lines.length - 1];
		if (last !== undefined && fits(`${last} ${unit}`)) {
			lines[lines.length - 1] = `${last} ${unit}`;
		} else {
			lines.push(unit);
		}
	}
	return lines;
};

// Largest font size (stepping down from maxFontPx) at which the units wrap into at
// most maxLines lines that fit the box; null if none down to minFontPx does.
export const fitHeadline = (units: string[], box: FitBox, spec: FitSpec, measure: Measure): HeadlineFit | null => {
	if (units.length === 0) {
		return null;
	}
	for (let fontPx = spec.maxFontPx; fontPx >= spec.minFontPx; fontPx -= spec.stepPx) {
		const lines = wrapUnits(units, fontPx, box.width, spec, measure);
		if (lines && lines.length <= spec.maxLines && lines.length * fontPx * spec.lineHeight <= box.height) {
			return {fontPx, lines};
		}
	}
	return null;
};

// fitHeadline, failing loudly: `what` names the text's use in the error.
export const fitHeadlineOrThrow = (units: string[], box: FitBox, spec: FitSpec, measure: Measure, what: string): HeadlineFit => {
	const fit = fitHeadline(units, box, spec, measure);
	if (!fit) {
		throw new Error(
			`${what} "${units.join(' ')}" doesn't fit ${Math.round(box.width)}x${Math.round(box.height)}px in ${spec.maxLines} lines at ${spec.minFontPx}px`,
		);
	}
	return fit;
};

// --- a stack of text blocks (the end card, remotion/src/EndCard.tsx) ----------------------

// One block of a stack: its wrap units at `width`, sized by `spec` (maxFontPx shrinking in
// stepPx steps to minFontPx, at most maxLines lines); padY is added to its text height (a
// panel's padding), minHeight is the least it takes (an icon), gapBefore the space above it.
// oneLineMinFontPx: one line is preferred down to this size, before wrapping into more.
// Blocks with the same `group` share one size, the smallest any of them needs.
export type StackBlock = {
	units: string[];
	spec: FitSpec;
	width: number;
	padY: number;
	minHeight: number;
	gapBefore: number;
	oneLineMinFontPx?: number;
	group?: string;
};

export type StackFit = {blocks: HeadlineFit[]; heights: number[]; height: number; step: number};

// The block at most `cap` px: on one line down to its oneLineMinFontPx, else wrapped into its
// maxLines at the largest size that does it; null if not even at minFontPx.
const fitBlock = (b: StackBlock, cap: number, measure: Measure): HeadlineFit | null => {
	const box = {width: b.width, height: Infinity};
	const oneLineMin = b.oneLineMinFontPx === undefined ? null : Math.min(cap, Math.max(b.spec.minFontPx, b.oneLineMinFontPx));
	const oneLine = oneLineMin === null ? null : fitHeadline(b.units, box, {...b.spec, maxFontPx: cap, minFontPx: oneLineMin, maxLines: 1}, measure);
	return oneLine ?? fitHeadline(b.units, box, {...b.spec, maxFontPx: cap}, measure);
};

// The blocks shrunk together: at step k each block may be at most max(minFontPx,
// maxFontPx - k * stepPx) and takes fitBlock()'s size under that, a group its smallest. The
// first k at which the whole stack (gaps included) fits boxHeight; null if a block can't wrap
// into its lines even at its minFontPx, or the stack is too tall with every block at its minFontPx.
export const fitStack = (blocks: StackBlock[], boxHeight: number, measure: Measure): StackFit | null => {
	for (let step = 0; ; step++) {
		const caps = blocks.map((b) => Math.max(b.spec.minFontPx, b.spec.maxFontPx - step * b.spec.stepPx));
		const capped = blocks.every((b, i) => caps[i] === b.spec.minFontPx);
		let fits = blocks.map((b, i) => fitBlock(b, caps[i], measure));
		// A group takes its smallest size; a block that then lands smaller lowers it again.
		for (let changed = true; changed; ) {
			if (fits.some((f) => f === null)) {
				return null;
			}
			const least = new Map<string, number>();
			blocks.forEach((b, i) => {
				if (b.group !== undefined) {
					least.set(b.group, Math.min(least.get(b.group) ?? Infinity, (fits[i] as HeadlineFit).fontPx));
				}
			});
			changed = false;
			fits = fits.map((f, i) => {
				const g = blocks[i].group;
				if (g === undefined || (f as HeadlineFit).fontPx === least.get(g)) {
					return f;
				}
				changed = true;
				return fitBlock(blocks[i], least.get(g) as number, measure);
			});
		}
		const done = fits as HeadlineFit[];
		const heights = done.map((f, i) =>
			Math.max(blocks[i].minHeight, f.lines.length * f.fontPx * blocks[i].spec.lineHeight + blocks[i].padY),
		);
		const height = heights.reduce((sum, h, i) => sum + h + blocks[i].gapBefore, 0);
		if (height <= boxHeight) {
			return {blocks: done, heights, height, step};
		}
		if (capped) {
			return null;
		}
	}
};

// Each caption chunk's line height (Captions.tsx), from the chunks' font sizes in order, as every
// reel has shown it: `lineHeight` until the first chunk drawn at another size than the first
// one, the font's own ('normal', tighter) from there on. Until 2026-10-25 the caption block set
// the `font` shorthand, which resets line-height, and React set it again on every size change
// but never lineHeight (that didn't change), so a render's tab lost `lineHeight` at its first
// size change. Computed now, not left to the order a tab drew its frames in, so a frame looks
// the same however its render started: scripts/render_reel.py renders a reel in segments for
// the share arrow and joins them without a jump.
export const captionLineHeights = (sizesPx: number[], lineHeight: number): Array<number | 'normal'> => {
	const resized = sizesPx.findIndex((px) => px !== sizesPx[0]);
	return sizesPx.map((_, i) => (resized === -1 || i < resized ? lineHeight : 'normal'));
};
