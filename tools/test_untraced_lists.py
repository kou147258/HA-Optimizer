"""Run the real analyzer against a fake instance with NO traces.

The 1.7.20 defect was structural: the listing code sat after the branch that
returns early when there are no traces, which is the normal case. Reading the
code proves the branch is gone; running it proves the list comes out.
"""
from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
COMP = Path(r"C:\Users\43457\.minimax\sessions\mvs_beb6ae963fc14d89832b6a57e91f762d\workspace\HA-Optimizer\custom_components\ha_optimizer")

# ── stubs ──
class Entry:
    def __init__(self, entity_id, name, disabled=False, ceid=None):
        self.entity_id, self.domain = entity_id, entity_id.split(".")[0]
        self.name, self.original_name, self.disabled = name, name, disabled
        self.config_entry_id = ceid or f"ce-{name}"


class EntReg:
    def __init__(self, entries):
        self.entities = {e.entity_id: e for e in entries}

    def values(self):
        return self.entities.values()

    @staticmethod
    def async_get(hass):
        return ENT_REG


class State:
    def __init__(self, entity_id, state, attrs):
        self.entity_id, self.state, self.attributes = entity_id, state, attrs


class Hass:
    def __init__(self, states, er):
        self.states_map = {s.entity_id: s for s in states}
        self.data = {}
        self.states = types.SimpleNamespace(get=self.states_map.get)


ENT_REG = None


def load():
    src = (COMP / "automation_runs.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    src = src.replace("from homeassistant.core import HomeAssistant", "HomeAssistant = object")
    src = src.replace("from homeassistant.helpers import entity_registry as er", "er = None")
    ns = {"_ER": EntReg, "_STATE": State, "_HASS": Hass}
    exec(compile(src, "automation_runs.py", "exec"), ns)  # noqa: S102
    return ns


async def main():
    ns = load()
    global ENT_REG
    ENT_REG = EntReg([Entry("automation.11111", "11111", ceid="ce-11111"),
                 Entry("automation.22222", "22222", disabled=True, ceid="ce-22222")])
    states = [State("automation.11111", "on",
                    {"friendly_name": "11111", "last_triggered": "2026-10-03T12:31:59+00:00"}),
              State("automation.22222", "off",
                    {"friendly_name": "22222", "last_triggered": "2026-10-03T05:45:03+00:00"})]
    hass = Hass(states, ENT_REG)

    # DATA_TRACE exists but is EMPTY - the exact situation on the instance.
    data_key = type("K", (), {})
    hass.data = {data_key: {}}

    # Make the module's helpers use our stubs.
    mod_ns = ns
    src = (COMP / "automation_runs.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    src = src.replace("from homeassistant.components.trace.const import DATA_TRACE",
                      "DATA_TRACE = _KEY")
    src = src.replace("from homeassistant.setup import async_setup_component",
                      "async_setup_component = _setup")
    src = src.replace("from homeassistant.helpers import entity_registry as er",
                      "er = _ER")
    src = src.replace("from homeassistant.core import HomeAssistant", "HomeAssistant = object")
    scope = {"_KEY": data_key, "_ER": ENT_REG, "_setup": _async_noop}
    exec(compile(src, "automation_runs.py", "exec"), scope)

    analyzer = scope["AutomationRunAnalyzer"](hass)
    result = await analyzer.async_analyze()

    rows = result["automations"]
    print(f"coverage : {result['coverage']}")
    print(f"列出的自动化: {len(rows)}")
    for r in rows:
        print(f"  {r['name']:10} enabled={r['enabled']} traced={r['traced']} "
              f"outcome={r['last_outcome']:10} last_triggered={r['last_triggered']}")
    print(f"summary  : total={result['summary'].get('total_automations')} "
          f"untraced={result['summary'].get('untraced')}")

    assert len(rows) == 2, f"expected both automations listed, got {len(rows)}"
    assert all(r["last_outcome"] == "untraced" for r in rows)
    assert all(r["last_triggered"] for r in rows)
    assert result["summary"]["untraced"] == 2
    assert "noteKey" in result["coverage"], "the note must be a key, not English prose"
    print("\nOK: with no traces the automations are still listed, marked unmeasured, "
          "with the facts that do exist")


async def _async_noop(hass, domain, config):
    return True


if __name__ == "__main__":
    asyncio.run(main())
