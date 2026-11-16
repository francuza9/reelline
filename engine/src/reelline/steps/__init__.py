"""The pipeline's steps, one module each (the contract: reelline/pipeline/step.py; the order and
switches: reelline/pipeline/chain.py). Each is a thin wrapper over the logic in reelline/pipeline/ and
reelline/media/; the ones that make sense without the database also have a module-level run() on
plain inputs (write, style, proofread, factcheck, cards, fx, render)."""
