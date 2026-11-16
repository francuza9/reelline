"""The canned model answers of the golden test: one fixture story (NASA rover news), what each pass says."""

import re

EVIDENCE = "NASA's Perseverance rover has found 20 distinct rock layers"

DRAFT = (
    "20 rock layers were found on Mars. NASA's Perseverance rover spotted them at the edge of Jezero crater.\n\n"
    "The layers look like mud that settled slowly in an ancient lake. "
    "They are stacked like pages in a book and are about 3 meters thick in total.\n\n"
    "That means water stood in the crater for a long time. "
    "The rover measured each layer with its camera and a small radar.\n\n"
    "NASA says the team plans to take a sample from the thickest layer later this year. "
    "Perseverance has collected 30 samples so far."
)
STORY = (
    DRAFT + '\n\nOPENINGS:\n{"used_score": 7, "runners_up": [{"opening": "A lake once sat on Mars.", "score": 6}, '
    '{"opening": "Mars has pages like a book.", "score": 5}]}'
)
SCRIPT = """@cover: Mars had a lake

@style: energetic
@card: 20 rock layers on Mars
@image: Mars rover on red terrain
@fx: condition 20 rock layers
20 rock layers were found on Mars. NASA's Perseverance rover spotted them at the edge of Jezero crater.

@card: Layers stacked like pages
@image: layered rock cliff
@image: ancient lake bed | They
@fx: condition 3 meters thick
The layers look like mud that settled slowly in an ancient lake. They are stacked like pages in a book and are about 3 meters thick in total.

@details
@style: friendly
The details are waiting at the end.

@card: Water stood there for long
@image: rover camera on Mars
That means water stood in the crater for a long time. The rover measured each layer with its camera and a small radar.

@card: A sample from the thickest layer
@image: sealed sample tube
@fx: condition 30 samples
NASA says the team plans to take a sample from the thickest layer later this year. Perseverance has collected 30 samples so far.

@share
@style: warm
Send this to a friend who loves space.

That's the news."""

BRIEF = {
    "what_it_is": "NASA's Perseverance rover found 20 distinct rock layers at the edge of Jezero crater on Mars.",
    "what_participants_do": "not stated",
    "who_is_behind": "NASA's Jet Propulsion Laboratory",
    "purpose": "The layers look like sediment from an ancient lake, so water stood in the crater for a long time.",
    "who_can_apply_age": "not stated",
    "who_can_apply_status": "not stated",
    "requirements": "not stated",
    "deadline": "not stated",
    "cost": "not stated",
    "how_to_apply": "not stated",
    "where_to_apply": "not stated",
    "when": "not stated",
    "key_numbers": ["20 rock layers", "3 meters thick in total", "30 samples collected"],
    "caveats": [],
}
ENDCARD = {
    "name": "Perseverance rock layers",
    "what": "20 layers from an ancient Mars lake",
    "who": None,
    "deadline": None,
    "cost": None,
    "apply": None,
}
CAPTION = {
    "hook": "Mars once had a lake, and the rock layers show it.",
    "youtube_title": "Rover finds 20 rock layers from an ancient Mars lake",
    "hashtags": ["#example", "#mars", "#nasa", "#space"],
}


def assess(request: dict) -> dict:
    ids = [int(i) for i in re.findall(r'<story id="(\d+)">', request["prompt"])]
    item = {
        "banned": False,
        "banned_reason": None,
        "policy_angle": False,
        "topic": "other",
        "edu_type": "program",
        "opportunity": True,
        "open_status": "open",
        "provider": "NASA",
        "provider_tier": "government",
        "cost": "free",
        "marketing_or_low_credibility": False,
        "georgia_relevance": "open_to_georgia",
        "reach": "broad",
        "novelty": "new",
        "deadline": None,
        "deadline_quote": None,
        "event_date": None,
        "event_date_quote": None,
        "who_can_apply": None,
        "big_news": None,
        "niche": None,
        "tech_significance": None,
        "hook_strength": 7,
        "same_program_key": "perseverance jezero rock layers",
        "summary": "Perseverance finds 20 rock layers from an ancient lake on Mars.",
    }
    return {"stories": [{"id": i, **item} for i in ids]}


def factcheck(request: dict) -> dict:
    """Every numbered sentence of the prompt (S1, S2, ...) supported by the source."""
    text = request.get("prompt") or request["messages"][-1]["content"]
    numbers = [int(n) for n in re.findall(r"^S(\d+)[ (:]", text, re.MULTILINE)]
    inserted = [int(n) for n in re.findall(r"^S(\d+) \(inserted\)", text, re.MULTILINE)]
    return {
        "claims": [
            {"sentence": n, "claim": f"sentence {n}", "verdict": "supported", "evidence": EVIDENCE, "note": None}
            for n in numbers
        ],
        "inserted": [{"sentence": n, "news_assertion": False, "reason": "only about the viewer"} for n in inserted],
    }


TABLE = {
    "assess": assess,
    "brief": BRIEF,
    "story": STORY,
    "punchup_straight": SCRIPT,
    "factcheck": factcheck,
    "endcard": ENDCARD,
    "endcard_factcheck": factcheck,
    "fx_factcheck": factcheck,
    "caption": CAPTION,
}
