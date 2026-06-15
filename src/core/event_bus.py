"""
Event bus -- core layer.

In-process typed publish/subscribe carrying common.models messages.
Upgradeable to Redis / ZeroMQ if layers split into separate processes.

Boundary: transport only, no business logic.
Status: stub -- no logic yet.
"""

