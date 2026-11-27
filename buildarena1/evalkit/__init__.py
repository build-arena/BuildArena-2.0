"""Evaluation of the BuildArena 1.0 tasks on 2.0: rebuild a tested model's
machine with BuildArena-2.0, place it the way BuildArena 1.0 did, drive it, and
score the 2.0 telemetry with a port of the 1.0 judges.

Nothing in this package talks to a model API. The control-agent call for the
transport category is an injected callable (see ``evalkit.sample``).
"""
