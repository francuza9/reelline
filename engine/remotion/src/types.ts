import screenSpec from './screen.json';

export type MouthShape = 'X' | 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G' | 'H';

// One entry of the `mouthCues` array (Rhubarb JSON format); times in seconds.
export type MouthCue = {
	start: number;
	end: number;
	value: MouthShape;
};

type Point = [number, number];

type EyeAnchor = {
	center: Point;
	radius_inner: number;
	radius_outer: number;
};

// Subset of assets/anchors.json used by the composition. All coordinates are asset-canvas pixels.
export type Anchors = {
	canvas: {width: number; height: number};
	mouth: {
		anchor: Point;
		png_size: Point;
		// Mood -> rest mouth image name (mouths/<name>.png); also has a "note" entry.
		rest_by_mood: Record<string, string>;
	};
	eyes: {left: EyeAnchor; right: EyeAnchor};
	stalks: {
		left: {top: Point; base: Point};
		right: {top: Point; base: Point};
		sway: {method: string; cut_row_y: number};
	};
	colors: {yellow: string; outline: string};
	chair: {seat_line_y: number; wheel_bottom_y: number; center_x: number};
};

// Caption word/chunk from scripts/captions.py; times in seconds.
export type CaptionWord = {
	text: string;
	start: number;
	end: number;
};

export type CaptionChunk = {
	chunk_start: number;
	chunk_end: number;
	lines: CaptionWord[][];
};

// Card for the TV on the wall; times in seconds. One type for now. Only drawn when
// SCENE.showCards is on (off by default); still built from @card for other uses.
export type HeadlineCard = {
	type: 'headline';
	text: string;
	// Wrap units: lines break only between these (words that must stay together are
	// one unit). Defaults to the whitespace-separated words of `text`.
	units?: string[];
	start: number;
	end: number;
};

export type Card = HeadlineCard;

// Picture on the wall TV for one paragraph (from out/reels/<name>/images.json); times
// in seconds. Crop and focal point are normalized (see reelline/media/images/framing.py).
export type ScreenImage = {
	// Path relative to the Remotion public dir, e.g. "screen_images/0590f6b001d0.png".
	src: string;
	start: number;
	end: number;
	// Natural pixel size of the file.
	width: number;
	height: number;
	// cover: photo filling the face via `crop`; contain: logo centered on `panelColor`.
	mode: 'cover' | 'contain';
	crop: {x: number; y: number; w: number; h: number} | null;
	focal: {x: number; y: number} | null;
	panelColor: string | null;
	// Seeds the Ken Burns origin jitter.
	seed: string;
};

// One of the end card's fields (remotion/src/outro.json fields, in the card's order).
export type EndCardField = 'name' | 'what' | 'who' | 'deadline' | 'cost' | 'apply';

// One fact line on the end card (reelline/media/outro.py ordered()).
export type EndCardFact = {field: EndCardField; text: string};

// The outro's place in the reel (reelline/media/outro.py outro_window) and what its end card shows;
// times in seconds. The card opens at `start`, over the host's last line (props from before
// 2026-10-25: after the speech), and the reel ends at `end`.
export type OutroWindow = {
	start: number;
	end: number;
	// The voice's end: the card holds from here to `end`, music only (missing: older props).
	speechEnd?: number;
	// The line the card came in on (outro.json startAt), for the record and QA.
	startAt?: 'tag' | 'share';
	// The card's fact lines in its order; empty: the footer only.
	facts: EndCardFact[];
	// The script points to the card (a @details line): for the record and QA.
	details?: boolean;
};

// The EndCard still (scripts/render_endcard_preview.py): the card as it holds, for a design check.
export type EndCardStillProps = {facts: EndCardFact[]; debugSafeArea: boolean};

// The opening (remotion/src/opening.ts): whether the first word gets an opener animation,
// and which. null (props from before it): none.
// Props rendered with opening-v2 also carry a hookTitle, ignored.
export type OpeningProps = {
	burst: boolean;
	// The opener animation (OPENING.animations key; reelline/media/script_markup.py opener_animation()).
	// Missing (opening-v2 props) with burst: "outrage".
	animation?: string | null;
	// The opening emotion (reelline/media/script_markup.py OPENING_EMOTIONS), for the record and QA.
	emotion?: string | null;
};

// remotion/src/screen.json with the background's sidecar merged over it
// (reelline/media/backgrounds.py screen_spec()): the wall TV's geometry, image and idle tunables.
export type ScreenSpec = typeof screenSpec;

export type ReelProps = {
	// Path relative to the Remotion public dir.
	audioSrc: string;
	mouthCues: MouthCue[];
	anchors: Anchors;
	// Key of anchors.mouth.rest_by_mood; picks the mouth shown when the cue is X.
	mood: string;
	// Audio loudness per video frame, 0..1 (see reelline/media/loudness.py).
	loudness: number[];
	captions: CaptionChunk[];
	// File name under backgrounds/ in the Remotion public dir, e.g. "bedroom_night.png".
	background: string;
	// The wall TV for this background.
	screen: ScreenSpec;
	cards: Card[];
	// Wall-TV pictures, in time order, non-overlapping.
	screenImages: ScreenImage[];
	// End segment after the speech (null: no outro). The composition runs to its end.
	outro: OutroWindow | null;
	opening: OpeningProps | null;
	// Fact graphics (reelline/media/fx.py placed()), in time order, apart; missing (props from before them): none.
	fx?: FxEvent[];
	// The motion pass (reelline/media/motion.py plan()); missing (props from before it): none.
	motion?: MotionProps;
	// The share arrow (remotion/src/ShareArrow.tsx, reelline/media/share.py); null or missing: none.
	share?: ShareProps | null;
	// The cold open before the reel (remotion/src/ColdOpen.tsx, reelline/media/gags.py props()); null or
	// missing: none. Every time above is the reel's own: the reel plays from the gag's last frame.
	gag?: GagProps | null;
};

// A pose of the cold open: its file under the public dir and its box in output pixels.
export type GagPose = {src: string; left: number; top: number; width: number; height: number};

// ReelProps.gag (reelline/media/gags.py props()), in seconds from the video's first frame: the action pose
// from 0 (from behind, facing the TV, with view "back"), the caught pose from caughtS, the reel from
// durationS. screen: the wall TV's picture over the whole gag (null: its idle face).
export type GagProps = {
	name: string;
	durationS: number;
	caughtS: number;
	view: 'front' | 'back';
	action: GagPose;
	caught: GagPose;
	screen: ScreenImage | null;
};

// One of the platforms the reel goes out on, each with its own share arrow (share.json platforms).
export type SharePlatform = 'instagram' | 'tiktok' | 'youtube' | 'facebook';

// ReelProps.share (reelline/media/share.py share_props()), in seconds: the share line from lineStart
// to lineEnd; the arrow is on from start (drawing itself) to end (gone, after its exit from
// lineEnd), pointing at `platform`'s share button. platform null: no arrow (the same timing,
// for the record).
export type ShareProps = {
	lineStart: number;
	lineEnd: number;
	start: number;
	end: number;
	platform: SharePlatform | null;
};

// ReelProps.motion, in seconds, each list in time order (remotion/src/motion.ts draws them).
export type MotionProps = {
	// Push-ins landing on punchlines (and, why "beat", the beats' fillers: motion.json beats):
	// up to `scale` from start to peak, held to holdEnd, back by end.
	zooms: {start: number; peak: number; holdEnd: number; end: number; scale: number; paragraph?: number; why?: string}[];
	// Reaction poses (motion.json reaction.poses) from start to end.
	reactions: {start: number; end: number; pose: string; paragraph?: number}[];
	// Glances up at the TV, from start (the dart) to end (back at the camera).
	glances: {start: number; end: number; why?: string}[];
	// The camera (remotion/src/camera.ts, motion.json framing): each shot from start to the
	// next one's, the first from 0. Missing (props from before the framing): the wide frame.
	// angle: the camera's angle (remotion/src/angles.ts, angles.json rigs); missing: front.
	shots?: {start: number; shot: string; angle?: string}[];
	// Lean-ins on emphasis: in from start to peak, held to holdEnd, back by end.
	leans?: {start: number; peak: number; holdEnd: number; end: number; paragraph?: number; why?: string}[];
	// Eye-stalk reactions (motion.json stalks.poses) from start to end.
	stalks?: {start: number; end: number; pose: string; paragraph?: number}[];
	// Squash-and-stretch bounces from `at` (motion.json bounce).
	bounces?: {at: number; paragraph?: number; why?: string}[];
};

export type CoverProps = {
	anchors: Anchors;
	// As in ReelProps: rest mouth mood, background file name and its wall TV.
	mood: string;
	background: string;
	screen: ScreenSpec;
	// The reel's first wall-TV picture (shown at its first frame), or null for the idle face.
	image: ScreenImage | null;
	headline: string;
	// Wrap units: lines break only between these (reelline/media/script_markup.py card_units).
	units: string[];
	// Draw the profile grid's safe area (for checking the layout).
	debugSafeArea: boolean;
};

// Animated fact graphics (remotion/src/Fx.tsx and remotion/src/fx/, tunables in
// fx.json): one fact per template, every value a prop. A script's @fx lines become
// ReelProps.fx (reelline/media/fx.py); the FxPreview composition shows the samples.
export type FxDeadline = {
	template: 'deadline';
	// Day of the month and the month's name, as the calendar page shows them.
	day: number;
	month: string;
	// The days-left tag beside the calendar; null or missing: none. Counted from the
	// reel's publish date (its slot date), never the render date.
	daysLeft?: number | null;
};

export type FxAge = {template: 'age'; from: number; to: number; unit: string};

export type FxMoney = {template: 'money'; value: number; unit: string};

export type FxPlaces = {template: 'places'; value: number; unit: string};

// One condition, one line (at most fx.json condition.maxChars characters).
export type FxCondition = {template: 'condition'; text: string};

// The stamp's text; null or missing: fx.json free.text ("უფასო").
export type FxFree = {template: 'free'; text?: string | null};

// A city by its name (nominative). Cities in remotion/src/fx/georgia.json get a pin on
// the map; any other name gets the label with a pin beside it, no map position.
export type FxLocation = {template: 'location'; city: string};

// Where the plane flies from Georgia, as the name shows: a country (nominative). Names in
// remotion/src/fx/destinations.json set its direction (as on a world map); any other
// flies at fx.json abroad.unknownDeg.
export type FxAbroad = {template: 'abroad'; destination: string};

export type FxDuration = {template: 'duration'; value: number; unit: string};

// The start date; label null or missing: fx.json start.label ("სტარტი").
export type FxStart = {template: 'start'; day: number; month: string; label?: string | null};

// Text on the diploma; null or missing: fx.json certificate.label ("სერტიფიკატი").
export type FxCertificate = {template: 'certificate'; label?: string | null};

// The prize as it should read: an amount ("5000 ლარი") or a place ("1-ლი ადგილი").
export type FxPrize = {template: 'prize'; text: string};

// The domain typed into the address bar ("edu.aris.ge").
export type FxApply = {template: 'apply'; url: string};

// Online (a laptop) or in person (a building); label null or missing: fx.json
// format.labels[mode].
export type FxFormat = {template: 'format'; mode: 'online' | 'onsite'; label?: string | null};

// 2-4 very short labels (1-2 words), one per step (fx.json steps minSteps..maxSteps),
// shown in a row under the numbers: the longer they are, the smaller they all get.
export type FxSteps = {template: 'steps'; labels: string[]};

export type FxData =
	| FxDeadline
	| FxAge
	| FxMoney
	| FxPlaces
	| FxCondition
	| FxFree
	| FxLocation
	| FxAbroad
	| FxDuration
	| FxStart
	| FxCertificate
	| FxPrize
	| FxApply
	| FxFormat
	| FxSteps;

export type FxTemplate = FxData['template'];

// tv: inside the wall TV's face, replacing its picture; popout: jumps out of the TV,
// plays larger in front of the scene, then goes back in.
export type FxPlacement = 'tv' | 'popout';

// One fx in a reel; times in seconds (the whole stretch, transitions included).
export type FxEvent = {
	data: FxData;
	placement: FxPlacement;
	start: number;
	end: number;
	// The script paragraph (1-based) whose @fx it is; for the record and QA.
	paragraph?: number;
	// The frame-0 card (paragraph 1's; fx.json opening): out and settled from `start` (0), no
	// spring out of the TV, only the settle; it flies back as usual.
	frame0?: boolean;
};

export type FxPreviewProps = {
	// A key of fx.json preview.samples.
	sample: string;
	placement: FxPlacement;
};
