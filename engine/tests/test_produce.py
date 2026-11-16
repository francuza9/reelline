"""reelline/pipeline/produce.py, no network: the background rotation (sorted, `_` skipped,
wrap-around, a removed last background, manual override), the reels table's status
transitions with retry-then-alert, the caption assembled after the render with the
attributions present, and the review message (ok / flagged, draft-lock warnings).
The renderer and Telegram are mocked."""

import json
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from reelline.config import conf
from reelline.pipeline import caption as caption_mod
from reelline.pipeline import db
from reelline.pipeline.produce import (
    ProduceError,
    Rendered,
    RenderFailed,
    backgrounds_report,
    choose_background,
    last_background,
    produce,
    reel_report,
    rotation_pick,
    send_review_messages,
    status_block,
)
from reelline.pipeline.qa import Check, Report
from tests.fx_produce import SIZE, scripted
from tests.fx_script import CAPTION, FINAL, JOKE_1


def add_reel(w, script_id, background, status="rendered", source="rotation"):
    now = db.now_iso()
    with db.transaction(w.conn):
        w.conn.execute(
            """INSERT INTO reels (pick_id, script_id, name, background, background_source, mood, status, attempts,
                   created_at, updated_at) VALUES (?, ?, 'r', ?, ?, 'smug', ?, 1, ?, ?)""",
            (w.pick_id, script_id, background, source, status, now, now),
        )


# ---- Rotation ---------------------------------------------------------------------------


def test_rotation_is_sorted_skips_disabled_and_wraps(world, folder):
    w = world
    sid = scripted(w)
    assert last_background(w.conn) is None and rotation_pick(w.conn) == "bedroom_night"
    order = []
    for _ in range(4):
        name = rotation_pick(w.conn)
        order.append(name)
        add_reel(w, sid, name)
    assert order == ["bedroom_night", "office_night", "talkshow", "bedroom_night"]  # _draft never, wraps


def test_failed_renders_dont_move_the_rotation(world, folder):
    w = world
    sid = scripted(w)
    add_reel(w, sid, "bedroom_night")
    add_reel(w, sid, "office_night", status="render_failed")
    assert rotation_pick(w.conn) == "office_night"


@pytest.mark.parametrize(
    "last, removed, disabled, expected",
    [
        ("office_night", "office_night", None, "talkshow"),  # removed: continue from where it sorted
        ("talkshow", "talkshow", None, "bedroom_night"),  # removed last in order: wrap
        ("office_night", None, "office_night", "talkshow"),  # disabled (renamed to _office_night)
        ("a_first", None, None, "bedroom_night"),  # a name that was never there sorts first
    ],
)
def test_rotation_after_the_last_background_is_gone(world, folder, last, removed, disabled, expected):
    w = world
    sid = scripted(w)
    add_reel(w, sid, last)
    if removed:
        (folder / f"{removed}.png").unlink()
    if disabled:
        (folder / f"{disabled}.png").rename(folder / f"_{disabled}.png")
    assert rotation_pick(w.conn) == expected


def test_manual_override_is_recorded_and_the_rotation_continues_after_it(world, folder):
    w = world
    sid = scripted(w)
    add_reel(w, sid, "bedroom_night")
    choice = choose_background(w.conn, "talkshow")
    assert (choice.name, choice.source) == ("talkshow", "manual")
    add_reel(w, sid, choice.name, source=choice.source)
    auto = choose_background(w.conn, None)
    assert (auto.name, auto.source) == ("bedroom_night", "rotation")  # after talkshow: wrap
    assert choose_background(w.conn, "_draft").name == "_draft"  # disabled ones can be forced
    with pytest.raises(ProduceError, match="No background 'nope'"):
        choose_background(w.conn, "nope")


def test_no_enabled_backgrounds_fails_loudly(world, folder):
    for p in folder.glob("*.png"):
        if not p.name.startswith("_"):
            p.rename(folder / f"_{p.name}")
    with pytest.raises(ProduceError, match="No enabled backgrounds"):
        rotation_pick(world.conn)


def test_backgrounds_report(world, folder):
    w = world
    sid = scripted(w)
    (folder / "talkshow.json").write_text(json.dumps({"screen": {"y": 320}}))
    (folder / "ghost.json").write_text("{}")
    Image.new("L", SIZE).save(folder / "gray.png")
    add_reel(w, sid, "office_night")
    text = "\n".join(backgrounds_report(w.conn))
    assert "Enabled (rotation order):\n  bedroom_night\n  gray  (INVALID: Background gray.png is mode L" in text
    assert "  talkshow  (sidecar talkshow.json: screen.y=320)" in text
    assert "Disabled:\n  _draft" in text and "Orphan sidecar (no PNG): ghost.json" in text
    assert text.endswith("Last used: office_night\nNext: talkshow")


# ---- produce run ------------------------------------------------------------------------------


NAMES = {"gtu_news": "GTU"}
CREDIT = "Photo: Jane Doe / Wikimedia Commons, CC BY-SA 4.0"


@pytest.fixture(autouse=True)
def proofread_on(monkeypatch):
    """The review message has no warning line about a skipped proofread (a channel may run without one)."""
    monkeypatch.setattr(conf().steps.proofread, "enabled", True)


MOOD = conf().steps.produce.default_mood  # the mood a render gets when the script names none
# The reel's images.json: one Commons image on the TV, then the idle screen.
IMAGES = {
    "version": 1,
    "paragraphs": [
        {
            "paragraph": 1,
            "value": "GTU building",
            "status": "image",
            "image_id": "aaa111",
            "source": "commons",
            "license": "CC BY-SA 4.0",
            "author": "Jane Doe",
            "attribution_required": True,
        },
        {"paragraph": 2, "value": "none", "status": "none"},
    ],
}


class FakeReview:
    def __init__(self):
        self.reels, self.texts = [], []

    def send_reel(self, mp4, header):
        self.reels.append((mp4, header))

    def send_text(self, text):
        self.texts.append(text)

    def send_review(self, conn, reel_id, text):
        from reelline.review import machine as review

        self.texts.append(text)
        return review.create(conn, reel_id, text)


LOW_ROW = {"paragraph": 2, "start": 3.4, "word": "ფინედუს", "score": 0.21}


class FakeRenderer:
    """Fails `fail` times with RenderFailed, then writes the reel's files (images.json and
    attributions.txt where the caption reads them; `images` None: a --no-images render)
    and returns them, with `rows` as the check-these words (`reroll_rows` for a render
    that re-rolls paragraphs)."""

    def __init__(
        self, fail=0, error="remotion render exited with 1", rows=(), reroll_rows=(), image_calls=(), images=IMAGES
    ):
        self.fail, self.error, self.calls, self.images = fail, error, [], images
        self.rows, self.reroll_rows, self.image_calls = list(rows), list(reroll_rows), list(image_calls)

    def __call__(self, script_path, background, mood, regen=(), **options):
        self.calls.append((script_path.name, background, mood, list(regen)))
        self.options = getattr(self, "options", []) + [options]
        if len(self.calls) <= self.fail:
            raise RenderFailed(f"{self.error} (call {len(self.calls)})")
        work = caption_mod.REELS_DIR / script_path.stem
        work.mkdir(parents=True, exist_ok=True)
        for name in ("reel.mp4", "cover.png"):
            (work / name).write_bytes(b"x")
        images = work / "images.json"
        if self.images is None:
            images.unlink(missing_ok=True)
        else:
            images.write_text(json.dumps(self.images, ensure_ascii=False), encoding="utf-8")
        (work / "attributions.txt").write_text(CREDIT + "\n", encoding="utf-8")
        rows = self.reroll_rows if regen else self.rows
        (work / "check_these.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        tts = 0.004 if regen else 0.0123  # this render's fresh TTS, as a cost row like reelline/media/tts.py call_row()
        tts_calls = [
            {
                "purpose": "tts",
                "model": "gemini-3.8-flash-tts",
                "prompt_version": 0,
                "usage": {"input": 0, "output": 0, "thinking": 0},
                "cost_usd": tts,
            }
        ]
        return Rendered(
            script_path.stem,
            work / "reel.mp4",
            work / "cover.png",
            47.25,
            images if images.is_file() else None,
            work / "attributions.txt",
            rows,
            0.0123,
            tts,
            [] if regen else self.image_calls,
            tts_calls,
        )


class FakeChecker:
    """reelline/pipeline/qa.py Checker with canned reports (the last one repeats); records its subjects."""

    def __init__(self, *reports):
        from reelline.pipeline.qa import Report

        self.reports = list(reports) or [Report([])]
        self.subjects = []

    def run(self, subject):
        self.subjects.append(subject)
        return self.reports[min(len(self.subjects), len(self.reports)) - 1]


def run(w, renderer, checker=None, **kw):
    review = FakeReview()
    checker = checker or FakeChecker()
    result = produce(
        w.conn,
        "2026-10-01",
        "13:00",
        NAMES,
        renderer=renderer,
        review_factory=lambda: review,
        log=lambda m: None,
        checker_factory=lambda: checker,
        **kw,
    )
    reel = w.conn.execute("SELECT * FROM reels WHERE id = ?", (result.reel_id,)).fetchone()
    pick = w.conn.execute("SELECT status FROM picks WHERE id = ?", (w.pick_id,)).fetchone()[0]
    return result, reel, pick, review


def test_rendered_reel_is_stored_with_the_caption_assembled_after_the_render(ready, monkeypatch):
    w = ready
    if not conf().publish.credits_page.domain:
        monkeypatch.setattr(conf().publish.credits_page, "domain", "credits.example.org")
    renderer = FakeRenderer()
    result, reel, pick, review = run(w, renderer)
    assert result.status == reel["status"] == pick == "rendered"
    assert reel["opening_version"] == conf().render.opening_version
    assert renderer.calls == [(renderer.calls[0][0], "bedroom_night", MOOD, [])]  # rotation's first, default mood
    assert (reel["background"], reel["background_source"], reel["mood"], reel["attempts"]) == (
        "bedroom_night",
        "rotation",
        MOOD,
        1,
    )
    assert reel["duration_s"] == 47.25 and reel["tts_cost_usd"] == 0.0123 and reel["error"] is None
    assert reel["mp4_path"].endswith("reel.mp4") and reel["cover_path"].endswith("cover.png")
    assert reel["images_path"].endswith("images.json") and reel["attributions_path"].endswith("attributions.txt")
    assert json.loads(reel["check_these"]) == [] and reel["qa_status"] == "pass"
    assert reel["qa_path"].endswith(f"qa_{reel['id']}.json") and json.loads(reel["qa_failures"]) == []
    # The credit line only exists once the renderer has run: the caption was assembled after it.
    caption = reel["caption"]
    credits = conf().publish.credits_page
    line = credits.line.format(domain=credits.domain, reel_id=reel["id"])  # the credits page, not per-image lines
    source = conf().steps.caption.source_line.split("{")[0].strip()
    assert CREDIT not in caption and line in caption
    assert caption.index(source) < caption.index(line) < caption.index(conf().steps.caption.ai_line)
    assert caption.splitlines()[3] == line
    assert last_background(w.conn) == "bedroom_night"


def test_a_reel_without_images_gets_no_credits_line(ready):
    result, reel, pick, review = run(ready, FakeRenderer(images=None))
    assert result.status == "rendered" and reel["images_path"] is None and "სურათები" not in reel["caption"]


def test_retry_once_then_rendered(ready):
    w = ready
    renderer = FakeRenderer(fail=1)
    result, reel, pick, review = run(w, renderer)
    assert result.status == pick == "rendered" and reel["attempts"] == 2 and len(renderer.calls) == 2
    assert not any("Render failed" in t for t in review.texts)


def test_second_failure_is_render_failed_with_an_alert(ready):
    w = ready
    renderer = FakeRenderer(fail=2, error="FAIL remotion render exited with 1")
    result, reel, pick, review = run(w, renderer)
    assert result.status == reel["status"] == pick == "render_failed" and len(renderer.calls) == 2
    assert reel["attempts"] == 2 and "attempt 1:" in reel["error"] and "(call 2)" in reel["error"]
    assert reel["mp4_path"] is None and reel["caption"] is None
    assert review.reels == []
    (alert,) = review.texts
    assert alert.startswith(f"Reel #{reel['id']} · ⚠️ Render failed: pick #") and "background bedroom_night" in alert
    assert "(call 2)" in alert
    # A failed render doesn't move the rotation, and the pick can be retried.
    assert rotation_pick(w.conn) == "bedroom_night"
    result2, reel2, pick2, _ = run(w, FakeRenderer())
    assert result2.status == pick2 == "rendered" and reel2["background"] == "bedroom_night"


def test_no_send_sends_nothing(ready):
    w = ready
    result, reel, pick, review = run(w, FakeRenderer(), send=False)
    assert result.status == "rendered" and review.reels == review.texts == []


def test_failure_alert_respects_no_send(ready):
    w = ready
    result, reel, pick, review = run(w, FakeRenderer(fail=2), send=False)
    assert result.status == "render_failed" and review.texts == []
    assert reel["opening_version"] == conf().render.opening_version


def test_manual_background_is_recorded(ready):
    w = ready
    renderer = FakeRenderer()
    result, reel, pick, review = run(w, renderer, background="talkshow")
    assert (reel["background"], reel["background_source"]) == ("talkshow", "manual")
    assert rotation_pick(w.conn) == "bedroom_night"


def test_nothing_to_produce_fails_clearly(ready):
    w = ready
    with pytest.raises(ProduceError, match="No pick for 2026-10-01 18:00"):
        produce(w.conn, "2026-10-01", "18:00", NAMES, renderer=FakeRenderer(), log=lambda m: None)
    run(w, FakeRenderer(), send=False)
    with pytest.raises(
        ProduceError,
        match=r"is rendered \(reel #1\); produce takes a pick that is scripted or "
        "render_failed",
    ):
        run(w, FakeRenderer())
    db.update_pick(w.conn, w.pick_id, status="scripted")
    with db.transaction(w.conn):
        w.conn.execute("UPDATE scripts SET status = 'failed'")
    with pytest.raises(ProduceError, match="has no script that is ok or flagged"):
        run(w, FakeRenderer())


# ---- The review message ---------------------------------------------------------------------


def test_review_message_ok(ready):
    w = ready
    result, reel, pick, review = run(w, FakeRenderer())
    ((mp4, header),) = review.reels
    assert mp4.name == "reel.mp4" and header.startswith(f"Reel #{reel['id']} · ")
    first, caption, status = review.texts
    rule = "━" * 20  # the header: the caption's YouTube title as the story's short title
    assert first == f"{rule}\n🎬 Reel #{reel['id']} · 2026-10-01 13:00 · {CAPTION['youtube_title']}\n{rule}"
    assert status.startswith(f"Reel #{reel['id']} · rendered, script #")
    assert caption == reel["caption"]
    assert "Fact-check: ✅" in status and "⚠️" not in status and "Draft lock" not in status
    assert "QA ✅" in status and "Check these" not in status
    assert f"Background: bedroom_night (rotation), mood {MOOD}, 47.2s" in status
    llm = sum(
        r[0] or 0
        for r in w.conn.execute(
            "SELECT cost_usd FROM llm_calls WHERE script_id = ? AND purpose NOT LIKE 'tts%'", (reel["script_id"],)
        )
    )
    shown = re.search(
        r"Cost so far: \$([\d.]+) \(LLM \$([\d.]+) \+ images \$0\.0000 \+ QA \$0\.0000 \+ TTS \$0\.0123\)", status
    )
    assert (
        shown
        and float(shown[1]) == pytest.approx(llm + 0.0123, abs=1e-4)
        and float(shown[2]) == pytest.approx(llm, abs=1e-4)
    )
    assert "🎞 Fact graphics:" in status


def test_review_message_counts_a_general_definition(ready):
    w = ready
    counts = {"supported": 6, "unsupported": 0, "distorted": 0, "general_definition": 1}
    with db.transaction(w.conn):
        w.conn.execute(
            "UPDATE scripts SET factcheck = ? WHERE id = ?",
            (json.dumps({"rounds": [{"route": "first", "counts": counts}]}), w.script_id),
        )
    *_, review = run(w, FakeRenderer())
    assert "Fact-check: ✅ 6 claim(s) supported, 1 general definition" in review.texts[2]


def test_review_message_shows_the_end_card(ready):
    w = ready
    passes = json.loads(w.conn.execute("SELECT passes FROM scripts WHERE id = ?", (w.script_id,)).fetchone()[0] or "{}")
    passes["card"] = {
        "lines": {"name": "GITA-ს კონკურსი", "cost": "უფასო"},
        "dropped": [{"field": "who", "text": "17+ წლის", "reason": "distorted: age (it's 18+)"}],
    }
    with db.transaction(w.conn):
        w.conn.execute(
            "UPDATE scripts SET passes = ? WHERE id = ?", (json.dumps(passes, ensure_ascii=False), w.script_id)
        )
    *_, review = run(w, FakeRenderer())
    assert (
        "🪧 End card: ⚠️ 2 line(s) (name, cost); left off 1:\n  - who „17+ წლის“: distorted: age (it's 18+)"
    ) in review.texts[2]


def test_review_message_flagged_with_draft_lock_warnings(ready):
    w = ready
    lock = json.loads(w.conn.execute("SELECT draft_lock FROM scripts WHERE id = ?", (w.script_id,)).fetchone()[0])
    lock["changed"] = [{"draft": "ძველი წინადადება.", "final": "ახალი წინადადება.", "ratio": 0.8}]
    lock["dropped"] = ["გამოტოვებული წინადადება."]
    failures = [f'"{JOKE_1}": distorted, claim 4 (wrong number)']
    with db.transaction(w.conn):
        w.conn.execute(
            "UPDATE scripts SET status = 'flagged', draft_lock = ?, failures = ? WHERE id = ?",
            (json.dumps(lock, ensure_ascii=False), json.dumps(failures, ensure_ascii=False), w.script_id),
        )
    result, reel, pick, review = run(w, FakeRenderer())
    status = review.texts[2]
    assert f"Fact-check: ⚠️ 1 failed claim(s)\n  - {failures[0]}" in status
    assert (
        "Draft lock: ⚠️ 1 changed, 1 dropped\n  changed: ძველი წინადადება. -> ახალი წინადადება.\n"
        "  dropped: გამოტოვებული წინადადება."
    ) in status
    assert status == status_block(w.conn, reel)
    report = reel_report(w.conn, reel["id"])
    assert "== Caption\n" + reel["caption"] in report and status in report


def test_long_texts_are_split_for_telegram():
    from reelline.pipeline.produce import split_text

    text = "\n".join(["ა" * 30] * 10)
    parts = split_text(text, limit=100)
    assert all(len(p) <= 100 for p in parts) and "\n".join(parts) == text
    assert split_text("x" * 250, limit=100) == ["x" * 100, "x" * 100, "x" * 50]


def test_cli_exits_non_zero_when_the_render_failed(tmp_path, monkeypatch, capsys):
    import reelline.pipeline.produce as produce_mod
    from reelline.pipeline.produce import Produced

    monkeypatch.setattr(produce_mod, "produce", lambda *a, **k: Produced(7, "render_failed", "boom"))
    assert (
        produce_mod.main(
            ["--db", str(tmp_path / "x.db"), "run", "--slot", conf().channel.slots[0], "--date", "2026-10-01"]
        )
        == 1
    )
    assert "FAILED: reel #7 render_failed" in capsys.readouterr().out


# ---- QA (reelline/pipeline/qa.py run_flow, with the checks mocked) -------------------------------------


FAIL_EYES = Report(
    [Check("visual", "frame 2", "fail", None, None, ["frame 2 at 14.6s: captions overlap the host's eyes"])]
)
URL_WARN = Check("text", "source url", "warn", "GET HTTP 503", None, ["source URL unreachable (GET HTTP 503): u"])


def old_take(w, stem, n=2):
    tts = caption_mod.REELS_DIR / stem / "tts"
    tts.mkdir(parents=True, exist_ok=True)
    (tts / f"p{n:02d}.wav").write_bytes(b"old take")
    (tts / f"p{n:02d}.json").write_text(json.dumps({"key": "k", "input_tokens": 10, "output_tokens": 1000}))
    return tts


class RerollRenderer(FakeRenderer):
    """A re-roll writes a new take for each paragraph it redoes."""

    def __call__(self, script_path, background, mood, regen=(), **options):
        tts = caption_mod.REELS_DIR / script_path.stem / "tts"
        for n in regen:
            (tts / f"p{n:02d}.wav").write_bytes(b"new take")
            (tts / f"p{n:02d}.json").write_text(json.dumps({"key": "k", "input_tokens": 10, "output_tokens": 2000}))
        return super().__call__(script_path, background, mood, regen, **options)


def stem_of(w):
    return Path(w.conn.execute("SELECT path FROM scripts WHERE id = ?", (w.script_id,)).fetchone()[0]).stem


def test_reroll_keeps_the_better_new_take_and_shows_the_words_once(ready):
    w = ready
    tts = old_take(w, stem_of(w))
    still_low = {**LOW_ROW, "score": 0.25, "word": "ახალი"}
    renderer = RerollRenderer(
        rows=[LOW_ROW, {**LOW_ROW, "start": 4.0, "word": "მეორე", "score": 0.1}], reroll_rows=[still_low]
    )
    checker = FakeChecker(
        Report([Check("audio", "low-score words", "warn", None, None, ["check: ¶2 3.4s ახალი (0.25)"])])
    )
    result, reel, pick, review = run(w, renderer, checker)
    assert [c[3] for c in renderer.calls] == [[], [2]]  # one re-roll, the new take kept: no extra render
    assert (tts / "p02.wav").read_bytes() == b"new take" and not (tts / "qa_old_takes").exists()
    assert checker.subjects[0].check_these == [still_low] and checker.subjects[0].cover_fresh is True
    assert json.loads(reel["check_these"]) == [still_low]
    assert reel["tts_cost_usd"] == pytest.approx(0.0123 + 0.004)  # the first render's reel TTS + the re-roll's
    qa = json.loads(Path(caption_mod.REELS_DIR / stem_of(w) / f"qa_{reel['id']}.json").read_text(encoding="utf-8"))
    (p,) = qa["rerolls"][0]["paragraphs"]
    assert (p["paragraph"], p["kept"], p["old"]["low_words"], p["new"]["low_words"]) == (2, "new", 2, 1)
    assert p["old"]["tts_cost_usd"] < p["new"]["tts_cost_usd"]  # both takes' costs logged
    _, caption, status = review.texts
    assert status.count("ახალი (0.25)") == 1 and "Check these" not in status and "მეორე" not in status
    assert "QA ✅\n⚠️ check: ¶2 3.4s ახალი (0.25)\nRe-roll: ¶2 kept new (2 -> 1 low)" in status


def test_reroll_keeps_the_old_take_when_the_new_one_is_worse_and_renders_again(ready):
    w = ready
    tts = old_take(w, stem_of(w))
    renderer = RerollRenderer(rows=[LOW_ROW], reroll_rows=[LOW_ROW, {**LOW_ROW, "word": "x", "score": 0.05}])
    result, reel, pick, review = run(w, renderer)
    assert [c[3] for c in renderer.calls] == [[], [2], []]  # the old take back, rendered from the cache
    assert (tts / "p02.wav").read_bytes() == b"old take"
    qa = json.loads(Path(caption_mod.REELS_DIR / stem_of(w) / f"qa_{reel['id']}.json").read_text(encoding="utf-8"))
    assert qa["rerolls"][0]["paragraphs"][0]["kept"] == "old"
    assert [r["reason"] for r in qa["renders"]] == ["re-roll ¶2", "old take kept for ¶2"]


def test_equal_low_counts_keep_the_take_with_the_higher_minimum(ready):
    w = ready
    old_take(w, stem_of(w))
    renderer = RerollRenderer(rows=[{**LOW_ROW, "score": 0.2}], reroll_rows=[{**LOW_ROW, "score": 0.1}])
    run(w, renderer)
    assert [c[3] for c in renderer.calls] == [[], [2], []]  # old (0.2 > 0.1) kept


def test_the_tag_paragraph_is_never_rerolled(ready):
    w = ready
    from reelline.media.script_markup import parse

    tag = len(parse(FINAL).paragraphs)  # the last paragraph
    renderer = FakeRenderer(rows=[{**LOW_ROW, "paragraph": tag}])
    run(w, renderer)
    assert [c[3] for c in renderer.calls] == [[]]


def test_qa_failure_rerenders_once_then_goes_to_review_marked(ready):
    w = ready
    renderer, checker = FakeRenderer(), FakeChecker(FAIL_EYES)
    result, reel, pick, review = run(w, renderer, checker)
    assert [c[3] for c in renderer.calls] == [[], []] and len(checker.subjects) == 2
    assert result.status == reel["status"] == "rendered" and reel["qa_status"] == "fail"
    status = review.texts[2]
    assert "QA ❌ 1 failure(s):\n  - visual: frame 2 at 14.6s: captions overlap the host's eyes" in status
    assert "QA re-render after: visual: frame 2" in status
    assert len(review.reels) == 1  # the review still goes out


def test_qa_failure_fixed_by_the_rerender_passes(ready):
    w = ready
    checker = FakeChecker(FAIL_EYES, Report([URL_WARN, URL_WARN]))
    result, reel, pick, review = run(w, FakeRenderer(), checker)
    assert reel["qa_status"] == "pass" and json.loads(reel["qa_warnings"]) == [URL_WARN.lines[0]]  # deduplicated
    assert review.texts[2].count("source URL unreachable") == 1


def test_a_failed_reroll_keeps_the_old_take_and_goes_on(ready):
    w = ready

    class FailOnReroll(FakeRenderer):
        def __call__(self, script_path, background, mood, regen=(), **options):
            if regen:
                self.calls.append((script_path.name, background, mood, list(regen)))
                exc = RenderFailed("TTS paragraph 2: ReadTimeout (re-roll)")
                exc.paid_calls = []
                raise exc
            return super().__call__(script_path, background, mood, regen, **options)

    old_take(w, stem_of(w))
    renderer = FailOnReroll(rows=[LOW_ROW])
    result, reel, pick, review = run(w, renderer)
    assert result.status == reel["status"] == pick == "rendered" and reel["qa_status"] == "pass"
    assert [c[3] for c in renderer.calls] == [[], [2]]  # no extra render after the failed re-roll
    tts = caption_mod.REELS_DIR / stem_of(w) / "tts"
    assert (tts / "p02.wav").read_bytes() == b"old take" and not (tts / "qa_old_takes").exists()
    assert "Re-roll of ¶2 failed, old takes kept: RenderFailed: TTS paragraph 2: ReadTimeout" in review.texts[2]
    assert len(review.reels) == 1  # the review went out


def test_a_render_failing_on_the_qa_rerender_is_render_failed_with_an_alert(ready):
    w = ready

    class FailOnRerender(FakeRenderer):
        def __call__(self, script_path, background, mood, regen=(), **options):
            if self.calls:
                self.calls.append((script_path.name, background, mood, list(regen)))
                raise RenderFailed("remotion render exited with 1 (re-render)")
            return super().__call__(script_path, background, mood, regen, **options)

    result, reel, pick, review = run(w, FailOnRerender(), FakeChecker(FAIL_EYES))
    assert result.status == reel["status"] == pick == "render_failed" and reel["error"].startswith("QA render:")
    (alert,) = review.texts
    assert alert.startswith(f"Reel #{reel['id']} · ⚠️ Render failed during QA") and review.reels == []


def test_review_rendered_runs_qa_on_the_render_as_it_is_and_sends_the_review(ready):
    from reelline.pipeline.produce import review_rendered
    from reelline.review import machine as review_mod

    w = ready
    result, reel, pick, _ = run(w, FakeRenderer(rows=[LOW_ROW]), qa=False, send=False)  # QA never ran
    assert review_mod.for_reel(w.conn, reel["id"]) is None  # the worker stopped before the review
    renders_before = reel["mp4_path"]
    checker, review = FakeChecker(FAIL_EYES), FakeReview()
    out = review_rendered(
        w.conn, reel["id"], NAMES, checker_factory=lambda: checker, review_factory=lambda: review, log=lambda m: None
    )
    reel = w.conn.execute("SELECT * FROM reels WHERE id = ?", (reel["id"],)).fetchone()
    assert reel["qa_status"] == "fail" and json.loads(reel["qa_failures"]) == FAIL_EYES.failures
    assert reel["mp4_path"] == renders_before and len(checker.subjects) == 1  # no re-roll, no re-render
    assert checker.subjects[0].check_these == [LOW_ROW]
    r = review_mod.for_reel(w.conn, reel["id"])
    assert out == f"reel #{reel['id']}: QA ❌ ({FAIL_EYES.failures[0]}), review #{r['id']}"
    assert len(review.reels) == 1 and r["status"] == "pending"
    again = review_rendered(
        w.conn,
        reel["id"],
        NAMES,
        checker_factory=lambda: FakeChecker(),
        review_factory=lambda: review,
        log=lambda m: None,
    )
    assert again.endswith("review already sent") and len(review.reels) == 1


def test_review_rendered_refuses_a_reel_that_isnt_rendered(ready):
    from reelline.pipeline.produce import ProduceError, review_rendered

    w = ready
    result, reel, pick, _ = run(w, FakeRenderer(), send=False)
    w.conn.execute("UPDATE reels SET status = 'superseded' WHERE id = ?", (reel["id"],))
    with pytest.raises(ProduceError, match="superseded"):
        review_rendered(w.conn, reel["id"], NAMES, checker_factory=lambda: FakeChecker(), log=lambda m: None)


def test_no_qa_skips_it_loudly(ready):
    w = ready
    renderer, checker = FakeRenderer(rows=[LOW_ROW]), FakeChecker()
    result, reel, pick, review = run(w, renderer, checker, qa=False)
    assert checker.subjects == [] and [c[3] for c in renderer.calls] == [[]] and reel["qa_status"] == "skipped"
    status = review.texts[2]
    assert "QA ⚠️ skipped (--no-qa)" in status and "Check these (1):\n  ¶2 3.4s ფინედუს (0.21)" in status


def call_row(purpose, model, prompt_version, input_tokens, output_tokens, thinking_tokens, image_tokens=None):
    """A paid call's cost row as the adapters record it (reelline/providers/costs.py), at models.prices."""
    from reelline.providers import LLMUsage, cost_usd
    from reelline.providers.costs import call_row as row

    usage = LLMUsage(input_tokens, output_tokens, thinking_tokens, image_tokens)
    return row(purpose, model, prompt_version, usage, cost_usd(model, usage))


def test_image_and_qa_vision_costs_are_in_the_total(ready):
    w = ready
    img = [
        call_row("image_vision", "gemini-3.8-flash", 2, 1000, 100, 50),
        call_row("image_generate", "gemini-3.1-flash-image", 1, 100, 1290, 0, 1290),
    ]
    qa_call = call_row("qa_vision", "gemini-3.8-flash", 1, 2000, 60, 30)
    result, reel, pick, review = run(w, FakeRenderer(image_calls=img), FakeChecker(Report([], [qa_call])))
    rows = w.conn.execute(
        "SELECT purpose, cost_usd FROM llm_calls WHERE script_id = ? AND purpose IN "
        "('image_vision', 'image_generate', 'qa_vision')",
        (w.script_id,),
    ).fetchall()
    assert sorted(r[0] for r in rows) == ["image_generate", "image_vision", "qa_vision"]
    images = img[0]["cost_usd"] + img[1]["cost_usd"]
    assert images == pytest.approx((1000 * 0.75 + 150 * 3.75) / 1e6 + (100 * 0.5 + 1290 * 60) / 1e6)
    assert f"+ images ${images:.4f} + QA ${qa_call['cost_usd']:.4f} + TTS $0.0123)" in review.texts[2]


def test_relevance_calls_count_and_an_upper_bound_illustration_is_labeled(ready):
    from types import SimpleNamespace

    from tests.fx_images import generation_call_row

    w = ready
    usage = SimpleNamespace(
        total_input_tokens=60, total_output_tokens=1200, total_thought_tokens=0, output_tokens_by_modality=None
    )
    img = [call_row("image_relevance", "gemini-3.8-flash", 3, 700, 40, 0), generation_call_row(usage)]
    result, reel, pick, review = run(w, FakeRenderer(image_calls=img))
    images = img[0]["cost_usd"] + img[1]["cost_usd"]
    assert f"+ images ${images:.4f} (upper bound) + QA" in review.texts[2]


def test_a_new_reel_supersedes_the_old_one_of_the_same_pick(ready):
    w = ready
    first = run(w, FakeRenderer())[1]
    db.update_pick(w.conn, w.pick_id, status="scripted")
    second = run(w, FakeRenderer())[1]
    statuses = dict(w.conn.execute("SELECT id, status FROM reels").fetchall())
    assert statuses == {first["id"]: "superseded", second["id"]: "rendered"}


def test_an_over_limit_caption_no_longer_blocks_produce(ready, monkeypatch):

    w = ready
    monkeypatch.setattr(conf().steps.caption, "limits", {**conf().steps.caption.limits, "facebook": {"chars": 50}})
    over = Report(
        [
            Check(
                "text",
                "caption limits",
                "fail",
                None,
                None,
                ["post caption is 300 characters; the limit is 50 (facebook)"],
            )
        ]
    )
    result, reel, pick, review = run(w, FakeRenderer(), FakeChecker(over))
    assert result.status == "rendered" and reel["caption"] and len(review.reels) == 1
    assert "  - text: post caption is 300 characters; the limit is 50 (facebook)" in review.texts[2]


def test_the_video_caption_leaves_the_check_these_list_out():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import tempfile

    from send_to_phone import caption_lines

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "check_these.json").write_text(json.dumps([LOW_ROW], ensure_ascii=False), encoding="utf-8")
        assert any("Check these" in line for line in caption_lines(Path(tmp), "r", 30.0, "hdr"))
        assert not any("Check these" in line for line in caption_lines(Path(tmp), "r", 30.0, "hdr", False))


# ---- The evening batch: a held review --------------------------------------------------------


def test_an_evening_chains_review_is_held_until_the_delivery_then_sent_once(ready, monkeypatch):
    from reelline.pipeline import run_daily

    w = ready
    monkeypatch.setattr(conf().steps.run_daily, "evening_batch_from", "2026-10-01")
    before = run_daily.delivery_at("2026-10-01") - timedelta(minutes=5)
    run_daily.new_chain(w.conn, "2026-10-01", "13:00", before, mode="evening")
    result, reel, pick, review = run(w, FakeRenderer(), clock=lambda: before)
    assert result.status == "rendered" and review.texts == [] and review.reels == []  # held
    assert run_daily.held_reel(w.conn, "2026-10-01", "13:00")["id"] == reel["id"]
    assert run_daily.claim_review(w.conn, reel["id"], before)  # the delivery takes it...
    second = FakeReview()
    send_review_messages(w.conn, reel["id"], second)
    assert len(second.reels) == 1 and second.texts[-1].startswith(f"Reel #{reel['id']} · ")
    assert run_daily.held_reel(w.conn, "2026-10-01", "13:00") is None


def test_after_the_delivery_time_the_review_goes_out_when_ready(ready, monkeypatch):
    from reelline.pipeline import run_daily

    w = ready
    monkeypatch.setattr(conf().steps.run_daily, "evening_batch_from", "2026-10-01")
    after = run_daily.delivery_at("2026-10-01") + timedelta(minutes=5)
    run_daily.new_chain(w.conn, "2026-10-01", "13:00", after, mode="evening")
    result, reel, pick, review = run(w, FakeRenderer(), clock=lambda: after)
    assert len(review.reels) == 1 and not run_daily.claim_review(w.conn, reel["id"], after)


def test_review_cli_with_video_sends_the_whole_review_once(ready, monkeypatch, capsys):
    from reelline.pipeline import produce as produce_mod

    w = ready
    _, reel, _, _ = run(w, FakeRenderer(), send=False)
    sent = FakeReview()
    monkeypatch.setattr(produce_mod, "Review", lambda: sent)
    assert produce_mod.cmd_review(SimpleNamespace(reel_id=reel["id"], video=True), w.conn) == 0
    assert [m for m, _ in sent.reels] == [produce_mod.REPO_ROOT / reel["mp4_path"]]  # send_to_phone picks the upload
    assert sent.texts[1] == reel["caption"] and sent.texts[-1].startswith(f"Reel #{reel['id']} · ")
    with pytest.raises(produce_mod.ProduceError, match="already has pending review"):
        produce_mod.cmd_review(SimpleNamespace(reel_id=reel["id"], video=True), w.conn)
    assert len(sent.reels) == 1
