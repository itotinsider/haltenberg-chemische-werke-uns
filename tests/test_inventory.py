"""The committed inventory/ must match what the code generates.

A failure here is not necessarily a bug: it means an interface changed (a tag, register, OPC UA
node, scenario or API field was added, renamed or removed). If that was intended, regenerate the
inventory and commit it with the change, so the PR shows the interface diff:

    docker compose run --rm tests python tools/inventory.py
"""

import pytest

import inventory


def test_inventory_is_up_to_date():
    diff = inventory.diff()
    if diff:
        shown = "\n".join(diff[:150]) + (f"\n... {len(diff) - 150} more diff lines" if len(diff) > 150 else "")
        pytest.fail("inventory/ differs from the code. If the interface change is intended, run\n"
                    "    docker compose run --rm tests python tools/inventory.py\n"
                    "and commit inventory/ with it.\n\n" + shown, pytrace=False)
