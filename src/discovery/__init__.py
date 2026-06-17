"""
Discovery -- the idea-generation plane.

Gathers buy candidates from multiple *free* signal sources (congressional
disclosures, the built technical strategies, news, fundamentals), scores them
deterministically, ranks them, and emits the best as risk-gated Proposals for
the phone to approve. It is a SUGGESTION layer: like the sentiment gate it can
surface and rank ideas but never originates an order -- every suggestion passes
the same risk gate and waits for human approval.

Boundary: places orders NO, holds trading credentials NO.
"""
