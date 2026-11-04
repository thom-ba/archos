"""Fails on purpose: proves the merge gate holds on a heavy-only failure. Never lands."""


def test_the_gate_holds():
    raise AssertionError("deliberate failure for the B gate test")
