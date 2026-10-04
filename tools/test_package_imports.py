"""Can the integration actually be loaded?

Every other check imports or execs ONE module with whatever stubs that module
happens to need. Nothing has ever loaded the whole component the way Home
Assistant does - as a package, with real relative imports between its own
files. A module that imports a sibling by a name that no longer exists, or that
one of the newer files needs a stub the older ones did not, would pass every
check and then answer HTTP 500 on install.

So this builds the shipped component as a real package directory and imports
every module in it, with `homeassistant` stubbed at the level each module's own
imports demand, and reports what could not be loaded and why.

Nothing is written into the repo; the package is assembled in a temp directory
from the files as they are on disk.
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
import traceback
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# The root comes from argv, not a literal. It did not at first, which is how
# this check's own counter-proof passed against a deliberately broken copy: the
# check read the real repository and reported green. A check that cannot be
# pointed at a broken copy is a check that cannot be tested.
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


def _stub_class(name: str):
    """A permissive stand-in that also works as a base class.

    Home Assistant's own base classes take keyword arguments in the class
    header - `class PurgeEngineConfigFlow(ConfigFlow, domain=DOMAIN)` - and the
    default `object.__init_subclass__` rejects them. A stub that cannot be
    subclassed that way reports a package defect that does not exist, which is
    worse than no check: this file failed its first two runs on exactly that.
    """
    def __getattr__(cls, item):
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(item)
        return _stub_class(f"{name}.{item}")

    def __getitem__(cls, item):
        return _stub_class(f"{name}[{item}]")

    def __mro_entries__(cls, bases):
        return (_Stub,)

    def __init_subclass__(cls, **kw):
        pass

    return _Meta(name, (_Stub,), {"__getattr__": classmethod(__getattr__),
                                  "__getitem__": classmethod(__getitem__),
                                  "__mro_entries__": classmethod(__mro_entries__),
                                  "__init_subclass__": classmethod(__init_subclass__)})


class _Meta(type):
    def __call__(cls, *a, **kw):
        return super().__call__()

    def __getattr__(cls, item):
        return _stub_class(f"{cls.__name__}.{item}")

    def __getitem__(cls, item):
        return _stub_class(f"{cls.__name__}[{item}]")


class _Stub(metaclass=_Meta):
    def __init__(self, *a, **kw):
        pass

    def __init_subclass__(cls, **kw):
        pass

    def __getattr__(self, item):
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(item)
        return _stub_class(f"{type(self).__name__}.{item}")

    def __call__(self, *a, **kw):
        return _Stub()

    def __getitem__(self, item):
        return _stub_class(f"{type(self).__name__}[{item}]")

    def __setitem__(self, item, value):
        return None

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __repr__(self):
        return "<stub>"


def permissive(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__path__ = []                       # type: ignore[attr-defined]
    m.__getattr__ = lambda item: _stub_class(f"{name}.{item}")   # type: ignore[attr-defined]
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def module(name: str, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def install_stubs() -> list[str]:
    """Stub `homeassistant` as far as a real import chain needs.

    Returns the names that had to be invented, so a module needing something
    unexpected is visible rather than silently satisfied.
    """
    import datetime as _dt

    made: list[str] = []

    def need(name, **attrs):
        made.append(name)
        return permissive(name, **attrs)

    ha = need("homeassistant")
    ha.__path__ = []                     # type: ignore[attr-defined]
    core = need("homeassistant.core", HomeAssistant=type("HomeAssistant", (), {}),
                Event=type("Event", (), {}), State=type("State", (), {}),
                ServiceCall=type("ServiceCall", (), {"data": {}}),
                SupportsResponse=type("SupportsResponse", (), {
                    "NONE": "none", "OPTIONAL": "optional", "REQUIRED": "required"}),
                ServiceRegistry=type("ServiceRegistry", (), {}),
                CoreState=type("CoreState", (), {"running": "RUNNING",
                                                 "stopping": "STOPPING"}),
                callback=lambda f: f, HassJob=object, Context=type("Context", (), {}),
                CALLBACK_TYPE=object, split_entity_id=lambda s: s.split(".", 1))
    need("homeassistant.const", __version__="2026.8.3",
         EVENT_HOMEASSISTANT_STOP="homeassistant_stop",
         ATTR_ENTITY_ID="entity_id", ATTR_FRIENDLY_NAME="friendly_name",
         CONF_ENTITY_ID="entity_id", CONF_FRIENDLY_NAME="friendly_name",
         Platform=type("Platform", (), {}), MAJOR_VERSION=2026, MINOR_VERSION=8)
    util = need("homeassistant.util")
    util.__path__ = []                   # type: ignore[attr-defined]
    dt_mod = need("homeassistant.util.dt",
                  utcnow=lambda: _dt.datetime.now(_dt.timezone.utc),
                  as_local=lambda d: d,
                  get_default_time_zone=lambda: _dt.timezone.utc,
                  UTC=_dt.timezone.utc,
                  parse_datetime=lambda s: _dt.datetime.fromisoformat(
                      s.replace("Z", "+00:00")),
                  now=lambda: _dt.datetime.now(_dt.timezone.utc))
    util.dt = dt_mod
    need("homeassistant.util.uuid", random_uuid_hex=lambda: "0" * 32)
    helpers = need("homeassistant.helpers")
    helpers.__path__ = []                # type: ignore[attr-defined]

    class _Reg:
        @staticmethod
        def async_get(hass):
            return None

        class RegistryEntryDisabler:
            USER = "user"
            INTEGRATION = "integration"
            CONFIG_ENTRY = "config_entry"

    need("homeassistant.helpers.entity_registry", async_get=_Reg.async_get,
         RegistryEntryDisabler=_Reg.RegistryEntryDisabler,
         async_entries_for_config_entry=lambda *a, **k: [])
    er = sys.modules["homeassistant.helpers.entity_registry"]
    er.RegistryEntry = type("RegistryEntry", (), {"async_get": staticmethod(
        lambda e: None), "async_update_entity": staticmethod(lambda *a, **k: None),
        "async_remove": staticmethod(lambda *a: None)})

    class _Store:
        def __init__(self, hass, version, key, **kw):
            self.key = key

        async def async_load(self):
            return None

        async def async_save(self, data):
            pass

    need("homeassistant.helpers.storage", Store=_Store, StoreException=Exception)
    need("homeassistant.helpers.event",
         async_track_time_interval=lambda *a, **k: (lambda: None),
         async_track_state_change_event=lambda *a, **k: (lambda: None),
         EventType=type("EventType", (), {}))
    need("homeassistant.helpers.dispatcher",
         async_dispatcher_send=lambda *a, **k: None,
         async_dispatcher_connect=lambda *a, **k: (lambda: None))
    need("homeassistant.helpers.typing", ConfigType=dict)
    need("homeassistant.helpers.update_coordinator",
         DataUpdateCoordinator=type("DataUpdateCoordinator", (), {}))
    need("homeassistant.exceptions",
         HomeAssistantError=Exception, ServiceValidationError=type(
             "ServiceValidationError", (Exception,), {}),
         HomeAssistantNotReady=type("HomeAssistantNotReady", (Exception,), {}),
         ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}),
         ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}))
    need("homeassistant.config_entries", ConfigEntry=type("ConfigEntry", (), {}),
         ConfigFlow=type("ConfigFlow", (), {}),
         OptionsFlow=type("OptionsFlow", (), {}),
         SOURCE_REAUTH=type("S", (), {"REAUTH": "reauth"}),
         ConfigFlowResult=dict, ConfigEntryState=type("ConfigEntryState", (), {}))
    need("homeassistant.data_entry_flow", FlowResultType=type("FlowResultType", (), {}))
    need("homeassistant.loader", async_get_integration=lambda *a, **k: None)
    need("homeassistant.setup", async_setup_component=lambda *a, **k: True)

    comp = need("homeassistant.components")
    comp.__path__ = []                   # type: ignore[attr-defined]
    for domain, attrs in (
        ("recorder", {"get_instance": lambda hass: None}),
        ("trace", {}),
        ("persistent_notification", {"async_create": lambda *a, **k: None,
                                    "async_dismiss": lambda *a, **k: None,
                                    "async_create_once": lambda *a, **k: None}),
        ("api", {}),
    ):
        d = need(f"homeassistant.components.{domain}")
        setattr(comp, domain, d)
    tr = sys.modules["homeassistant.components.trace"]
    tr.DOMAIN = "trace"
    tr.STORAGE_KEY = "trace.saved_traces"
    tr.STORAGE_VERSION = 1
    tr.TRACE_CONFIG_SCHEMA = dict
    tr.CONF_STORED_TRACES = "stored_traces"
    tr.__all__ = []
    const = need("homeassistant.components.trace.const", DATA_TRACE=object(),
                 DATA_TRACE_STORE=object(), DATA_TRACES_RESTORED=object(),
                 CONF_STORED_TRACES="stored_traces", DEFAULT_STORED_TRACES=5)
    tr.const = const
    util2 = need("homeassistant.components.trace.util",
                 async_list_traces=None, async_restore_traces=None,
                 async_get_trace=None)
    tr.util = util2

    srv = need("homeassistant.helpers.service", async_extract_config_entry_ids=None)
    return made


with tempfile.TemporaryDirectory() as td:
    t = Path(td)
    pkg = t / "custom_components" / "ha_optimizer"
    shutil.copytree(COMPONENT, pkg,
                    ignore=shutil.ignore_patterns("__pycache__"))
    # translations/ and the json files are data, not modules, but they travel
    # in the package and a missing one breaks a tab.
    (t / "custom_components" / "__init__.py").write_text("", encoding="utf-8")

    stubs = install_stubs()
    check("homeassistant was stubbed", len(stubs) > 15, f"only {len(stubs)} stubs made")

    sys.path.insert(0, str(t))
    modules = sorted(p.stem for p in pkg.glob("*.py") if p.stem != "__init__")
    print(f"\n  modules in the shipped package: {len(modules)}")

    loaded, failed = [], []
    for name in modules:
        full = f"custom_components.ha_optimizer.{name}"
        try:
            importlib.import_module(full)
            loaded.append(name)
        except Exception as exc:                      # noqa: BLE001
            tb = traceback.format_exc().strip().splitlines()[-1]
            failed.append((name, f"{type(exc).__name__}: {exc}", tb))

    for name, why, tb in failed:
        print(f"  FAIL  {name}  ->  {why}")
    check("every module in the package imports", not failed,
          f"{len(failed)} failed: " + ", ".join(n for n, _, _ in failed))
    check("the package's own relative imports resolve", len(loaded) == len(modules),
          f"{len(loaded)} of {len(modules)} loaded")

    # A package that imports fine can still be missing a file the panel needs.
    data = [p.name for p in pkg.iterdir() if p.suffix == ".json"]
    print(f"  data files shipped: {data}")
    check("manifest.json is present", "manifest.json" in data)
    tr_dir = pkg / "translations"
    check("translations/ is present", tr_dir.is_dir(),
          "its absence is what made 1.7.1 un-installable")
    if tr_dir.is_dir():
        print(f"  translations: {sorted(p.name for p in tr_dir.iterdir())}")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
