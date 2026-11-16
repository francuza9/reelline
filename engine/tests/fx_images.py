"""A helper shared by the image and produce tests."""

from types import SimpleNamespace


def generation_call_row(usage) -> dict:
    """The cost row the illustrator's adapter (reelline/providers/google.py generate()) records for
    an interaction with this usage (and no image)."""
    from reelline.providers import CostLedger, get_image_generator

    rows: list[dict] = []
    client = SimpleNamespace(
        interactions=SimpleNamespace(create=lambda **kw: SimpleNamespace(usage=usage, output_image=None))
    )
    assert get_image_generator("illustrator", client).generate("p", seed=1, ledger=CostLedger(rows=rows)) is None
    return rows[0]
