"""The golden test's driver (tests/test_golden_example.py runs it in a subprocess, so the example channel's
settings are the only ones any module sees): the example channel's whole chain on fakes, up to the render.

    python driver.py <repo root> <work dir>

Writes <work dir>/report.json: what each step made."""

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

REPO, WORK = Path(sys.argv[1]), Path(sys.argv[2])
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# The channel is a copy: its database, scripts and files are written in the work dir.
CHANNEL = WORK / "channel"
shutil.copytree(REPO / "channels" / "example", CHANNEL)

from reelline.config import conf, load_config, set_config  # noqa: E402

set_config(load_config(CHANNEL, use_env=False))

import replies  # noqa: E402
from reelline.pipeline import db, ingest  # noqa: E402
from reelline.pipeline.fetch import Fetcher  # noqa: E402
from reelline.pipeline.slices import run_chain  # noqa: E402
from reelline.providers.fakes import FakeLLM, FakeTTS  # noqa: E402

LOG: list[str] = []

# No network at all: a step that reaches for a real service fails here (loopback is fine: Remotion's own server).
import socket  # noqa: E402

_connect = socket.socket.connect


def _no_network(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in ("127.0.0.1", "::1", "localhost"):
        raise AssertionError(f"the golden test tried to reach {address}")
    return _connect(self, address)


socket.socket.connect = _no_network  # type: ignore[method-assign]


class Canned(FakeLLM):
    """A FakeLLM answering by purpose (replies.TABLE); a value is the answer, a function makes it from the request."""

    def __init__(self, role="writer", _shared=None):
        super().__init__([], role=role, _shared=_shared)

    def for_role(self, role):
        return Canned(role=role, _shared=self._shared)

    def _answer(self, method, ledger, purpose, prompt_version, items, **request):
        if purpose not in replies.TABLE:
            last = str((request.get("messages") or [{}])[-1].get("content", ""))[:1500]
            raise AssertionError(f"the golden test has no canned answer for {purpose}; last message: {last}")
        answer = replies.TABLE[purpose]
        self._shared["replies"].append(answer(request) if callable(answer) else answer)
        return super()._answer(method, ledger, purpose, prompt_version, items, **request)


class FakeResponse:
    def __init__(self, url, body=b"", status=200, content_type="text/html; charset=utf-8"):
        self.url, self.status_code, self._body = url, status, body
        self.headers = {"Content-Type": content_type}

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i : i + size]

    def close(self):
        pass


class FakeWeb:
    """requests.Session stand-in: the feed and its article; every other URL (robots.txt) is a 404."""

    def __init__(self, routes):
        self.routes, self.headers, self.requests = routes, {}, []

    def get(self, url, headers=None, **kwargs):
        self.requests.append(url)
        if url in self.routes:
            body, kind = self.routes[url]
            return FakeResponse(url, body, content_type=kind)
        return FakeResponse(url, b"not found", 404)


def main() -> None:
    now = datetime.now(UTC)
    sources = [s["url"] for s in __import__("yaml").safe_load((CHANNEL / "sources.yaml").read_text())]
    feed = (HERE / "feed.xml").read_text().replace("{{PUBDATE}}", format_datetime(now - timedelta(hours=3)))
    article = (HERE / "article.html").read_bytes()
    web = FakeWeb(
        {
            sources[0]: (feed.encode(), "application/rss+xml"),
            "https://www.nasa.gov/news-release/rover-finds-lake-layers/": (article, "text/html; charset=utf-8"),
        }
    )
    ingest.Fetcher = lambda **kw: Fetcher(session=web, sleep=lambda s: None, log=lambda m: None)

    # Only the first source answers: the second feed 404s, which ingest counts as one source failing.
    llm = Canned()
    tts = FakeTTS()
    clients = {r: llm for r in ("assessor", "briefer", "writer", "stylist", "caption", "endcard", "checker")}
    conn = db.connect()
    slot_date = (now + timedelta(days=1)).date().isoformat()
    out = {"log": LOG}
    outcome = run_chain(
        conn, slot_date, conf().channel.slots[0], start="ingest", stop="save", log=LOG.append, clients=clients
    )
    out["chain"] = {"status": outcome.status, "stopped_at": outcome.stopped_at, "detail": outcome.detail}
    out["purposes"] = [r["purpose"] for r in llm.requests]
    if outcome.status != "ok":
        raise SystemExit(f"the script chain stopped: {out['chain']}")
    row = conn.execute("SELECT * FROM scripts").fetchone()
    out["script_file"] = row["path"]

    # The voice (the TTS fake takes the channel's voice setting) and the render in its cheap mode: the script's
    # real checks, the Remotion cover still and a Reel frame through the placeholder host, and a 4-second
    # low-res video made from the cover (a full render needs lip sync, which only Georgian has).
    from reelline.media.script_markup import parse
    from reelline.paths import stored_path
    from reelline.pipeline.produce import Rendered
    from reelline.pipeline.script import display_text
    from reelline.steps import voice

    script_path = stored_path(row["path"])
    text = display_text(parse(script_path.read_text(encoding="utf-8")))
    speech = voice.run(text, tts=tts, voice="example-voice")
    out["tts"] = {
        "requests": len(tts.requests),
        "words": len(tts.requests[0]["text"].split()),
        "bytes": len(speech.wav),
    }
    wav = WORK / "voice.wav"
    wav.write_bytes(speech.wav)

    sys.path.insert(0, str(REPO / "engine" / "scripts"))
    import render_reel

    def renderer(path, background, mood, regen, **options):
        from reelline.media.outro import load_facts, load_outro

        script = parse(path.read_text(encoding="utf-8"))
        facts = load_facts(render_reel.facts_path(path), load_outro())  # the end card's lines, checked as a render does
        out["endcard"] = facts
        headline, source = render_reel.cover_headline(script)
        bg, spec = render_reel.load_background(background)
        public, work = WORK / "remotion_public", WORK / "render"
        work.mkdir()
        render_reel.stage_public_dir(public, wav, wav.name, bg)
        cover = render_reel.render_cover(
            work,
            render_reel.cover_props(script, headline, render_reel.load_anchors(), mood, bg.file, spec, None, False),
            public,
        )
        mp4 = WORK / "reel.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-i",
                str(cover),
                "-i",
                str(wav),
                "-t",
                "4",
                "-r",
                "30",
                "-vf",
                "scale=1080:1920",
                "-pix_fmt",
                "yuv420p",
                str(mp4),
            ],
            check=True,
        )
        out["headline"] = [headline, source]
        still, _cover, _spec = render_reel.background_check(background, send=False)  # a Reel frame
        out["render"] = {"cover": str(cover), "still": str(still), "mood": mood, "background": background}
        return Rendered(path.stem, mp4, cover, 4.0, None, None)

    outcome = run_chain(
        conn,
        slot_date,
        conf().channel.slots[0],
        start="render",
        stop="publish",
        log=LOG.append,
        clients=clients,
        produce={"renderer": renderer, "background": "studio"},
    )
    out["produce"] = {"status": outcome.status, "stopped_at": outcome.stopped_at, "detail": outcome.detail}

    # The review is automatic: its decision arrives as the approval bot gets it (a tap in the review inbox),
    # then the publish step puts the reel in the local folder.
    from reelline.pipeline.approve_bot import ApproveBot
    from reelline.review import inbox
    from reelline.review.auto import AutoReview

    review = conn.execute("SELECT * FROM reviews ORDER BY id DESC").fetchone()
    inbox.drop({"type": "tap", "data": f"r:{review['id']}:ap", "user": "auto"})

    def no_process(
        job_id,
    ):  # nothing is started and no pid recorded: the job stays queued for the later bot's inline run
        return SimpleNamespace(poll=lambda: 0, pid=None)

    bot = ApproveBot(
        conn, AutoReview(out=LOG.append, sleep=lambda s: None), source_names={}, log=LOG.append, spawn=no_process
    )
    bot.poll_once(0)
    due = datetime.fromisoformat(conn.execute("SELECT due_at FROM publications").fetchone()["due_at"])

    from reelline.pipeline.approve_bot import worker

    def inline(job_id):  # the job's worker, run here instead of as a process
        worker.run_job(conn, job_id, later.tg, worker.Steps(conn, {}, LOG.append), LOG.append)
        return SimpleNamespace(poll=lambda: 0, pid=0)

    later = ApproveBot(  # the same bot a minute after the slot: the waiting publication is due
        conn,
        AutoReview(out=LOG.append, sleep=lambda s: None),
        source_names={},
        log=LOG.append,
        now=lambda: due + timedelta(minutes=1),
        spawn=inline,
    )
    later.tick()
    out["review"] = dict(conn.execute("SELECT * FROM reviews ORDER BY id DESC").fetchone())
    tables = ("stories", "assessments", "picks", "briefs", "scripts", "captions", "reels", "reviews", "llm_calls")
    out["rows"] = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    script_row = conn.execute("SELECT * FROM scripts").fetchone()
    out["passes"] = list(json.loads(script_row["passes"]))
    out["brief"] = conn.execute("SELECT markdown FROM briefs").fetchone()["markdown"]
    out["caption"] = dict(conn.execute("SELECT hook, youtube_title, hashtags FROM captions").fetchone())
    out["sources"] = {r["id"]: r["consecutive_failures"] for r in conn.execute("SELECT * FROM sources")}
    out["story"] = dict(conn.execute("SELECT source_id, title, word_count FROM stories").fetchone())
    out["reel"] = dict(conn.execute("SELECT status, background, mood FROM reels").fetchone())
    out["publications"] = [dict(r) for r in conn.execute("SELECT * FROM publications")]
    out["published"] = sorted(
        str(p.relative_to(CHANNEL / "out" / "published"))
        for p in (CHANNEL / "out" / "published").rglob("*")
        if p.is_file()
    )
    sidecar = next((CHANNEL / "out" / "published").rglob("*.mp4.json"))
    out["sidecar"] = json.loads(sidecar.read_text(encoding="utf-8"))
    (WORK / "report.json").write_text(json.dumps(out, indent=1, default=str))


try:
    main()
except BaseException:
    print("\n".join(LOG[-25:]), file=sys.stderr)
    raise
