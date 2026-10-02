# Changelog

All notable changes to **HA Optimizer** will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.6.0] - 2026-10-02

The config flow is finally translated, automations can be sorted by whether
they are enabled, and two long-standing label bugs that made the panel
misreport what it was showing came out of it.

### Fixed
- 🇨🇳 **The config-flow and options dialogs were never translated.** `strings.json` was complete but English-only and there was no `translations/` directory, so the setup dialog showed the raw field keys — `scan_interval_days`, `stale_days_threshold`, `enable_soft_delete` — in a Chinese panel. `translations/zh-Hans.json` now carries all 57 strings: every field label, every field description, both step titles, the error and abort reasons, and all twelve service names and descriptions. `check_i18n.py` gained a check for this whole namespace, and it names the offending field rather than dumping a key diff.
- 🏷️ **The type filter was off by one, and the source filter had YAML and Registry swapped.** Option labels were applied **positionally**, from a key list written separately from the `<option>` markup. `filterCat`'s keys were ordered entity/automation/script/helper while the options are entity/helper/automation/script, so choosing 「自动化」 filtered `helper`; `filterYaml`'s two keys were swapped, so 「YAML」 displayed the Registry label. Both now match on the option's `value`, so the two lists can no longer drift apart. Twenty-five checks in `test_panel_sync.py` cover every filter select, and all eight re-injected defects turn them red.
- 🕐 **Two timestamps were formatted with `toLocaleString('vi-VN')`** — upstream's source language — so a Chinese or English user saw upstream's date conventions regardless of what they had chosen. They follow the panel's own language now.

### Added
- 🤖 **Enabled and disabled automations can be told apart.** The scanner already knew: it appended a `reason_auto_disabled` reason. But that is a string, so the panel could not filter on it. `ScanResult.disabled` existed and was already in `to_dict()` and already populated for entities — automations were the only category that never filled it in, and the panel never read the field at all. Automations now record it from two independent sources (the entity registry, and the state machine, which is what covers YAML automations that have no registry entry), the results table has a fourth filter, and every automation row carries a ✅/⛔ badge. Other categories show the badge only when disabled, so a table of entities does not become a wall of "enabled".
- 🔧 **`tools/test_filters.js`** — 12 checks that lift the **actual** filter predicate out of `panel.html` and run it, rather than re-implementing it. A rendered click test is not evidence here: the browser tool does not drive a `<select>`'s inline `onchange` reliably, and the pre-existing risk filter does not respond to it either, so anything that claimed to verify the filter by clicking would have been measuring the tool.

### Not changed
- **A disabled automation is a state fact, not a risk judgement.** It gets its own badge and its own filter; the risk level still only means "how long since it triggered / is the name suspicious". Raising the risk level would have changed the existing statistics and everyone's habits for no extra information.

---

## [1.5.2] - 2026-10-02

Two defects found by auditing the running integration against a live Home
Assistant 2026.8.3 instance. Both were pre-existing, neither was caused by the
version, and both fail quietly rather than loudly.

### Fixed
- 🧹 **An upgrade could leave the old panel being served, silently.** `_copy_panel_to_www()` decided whether the panel needed copying by comparing mtimes and skipped whenever the served copy was not older than the source. Nothing in a HACS install sets mtime to anything meaningful, and the copy itself used `shutil.copy2`, which preserves the source mtime — so the check could only be right by accident, and when it guessed wrong the new panel was installed and the old one kept being served with nothing in the log. This is the function behind the "the panel 404s and nobody knows why" report earlier. It now compares **content**, writes beside the target and renames over it (a half-written file is a 404 in the iframe), and logs a **warning** with both byte counts when it replaces a file that did not match.
- 🔌 **Seven services were callable from the panel and nowhere else.** `analyze_recorder`, `analyze_dashboard`, `analyze_storms`, `analyze_dead_code`, `analyze_health`, `analyze_addons` and `analyze_fingerprint` were registered with `SupportsResponse.ONLY`, which Home Assistant enforces: the caller *must* ask for a response. Only the panel does — it adds `?return_response` on all three of its call paths. Automations, scripts, blueprints and the Developer Tools action picker have no way to ask, so all seven answered HTTP 400. `scan`, `get_results` and `collect_baseline` were already `OPTIONAL`, so this was an inconsistency rather than a deliberate design. They are now `OPTIONAL` too: identical for the panel, and "run a weekly health check from an automation" finally works.

### Added
- 🔧 **`tools/test_panel_sync.py`** — 14 checks, no Home Assistant required. It covers the copy decision on the exact case that shipped broken (identical bytes, destination stamped *later*), on a stale-and-newer panel, on a stale-and-older one, that unrelated files in the target directory are never deleted, that no scratch file is left behind, and that no service is registered `ONLY`. All four defects are confirmed to turn it red when re-injected. Wired into the existing `i18n` CI job, which gates releases.

### Not changed
- `purge` and `restore` are deliberately not part of this release's live testing. They were exercised only as far as their schema gate (`purge` with no `entity_ids` → HTTP 400, handler never runs; `purge` with an empty list → 200 and nothing deleted). The destructive path is still covered only by the 17 checks in `tools/test_purge_safety.py`.

### Correction to the 1.5.2 release notes
- 📝 The published notes said the analysis services "persist their results either way, so you can also fire one on a schedule and read the outcome later from the panel". **That is wrong, and it was checked rather than assumed.** `handle_analyze_*` is `return await analyzer.async_analyze()` and nothing more; only `handle_scan` (`async_save_scan_results`) and `collect_baseline` (fingerprint store) write anything. Verified on a live instance: after running all seven analyzers, `get_results` still reports exactly `results, soft_deleted, statistics` — no analyzer output appears anywhere. So a scheduled `analyze_health` runs the analysis and throws the result away. The `OPTIONAL` change is still correct — the services were returning HTTP 400 to every caller that could not ask for a response — but its practical reach is the panel and any tool that can surface a response, not automations looking for stored results. The README now says this per service instead of implying otherwise.

---

## [1.5.1] - 2026-10-02

The fork takes over as the maintained line. The original project has been
unmaintained since April 2026, so the links that pointed users at it now point
here instead. No behaviour change.

### Changed
- 🔗 **`manifest.json` points at this repository.** `documentation`, `issue_tracker` and `codeowners` all named the original author. Home Assistant renders these directly in the integration page, so an installed copy was sending users to a repository that will never ship a fix. They are now `kou147258/HA-Optimizer` / `@kou147258`.
- 🔗 **The README's HACS install button pointed upstream.** Following it installed the abandoned version. It now points at this repository, and a notice at the top of the README says which line is maintained and why.
- 🙏 **Credits kept, donate link re-scoped.** The original author keeps full credit for the design and the original code, and their PayPal is still reachable — but the "buy me a coffee" button is gone, because that money goes to them and the fork's maintainer is someone else. The support section now asks for issue reports instead.

---

## [1.5.0] - 2026-10-02

Interface languages trimmed to **English + 简体中文**. This is a deliberate
narrowing of 1.4.0, not an accident: the eleven other dictionaries are gone
from `panel.html`, the `LANGUAGES` table and the HA-language map, which takes
the shipped panel from 510 KB to 252 KB. Vietnamese went with them, so this
fork no longer speaks the upstream author's first language.

### Added
- 🇨🇳 **The theme names are translated.** The eleven theme entries (Deep Space, Midnight Purple, …) had a hardcoded English `name` next to a translated `descKey`, so a Chinese user saw "深色 + 蓝" described underneath a button reading "Deep Space". Each theme now carries a `nameKey` and both the menu items and the current-theme button resolve through `t()`.

### Fixed
- 🌐 **Changing the language now updates the theme name on the button.** `_applyTranslations()` rebuilt the theme *menu* but never the `#themeCurrentName` label, which is written in `setTheme()` only. The menu followed the language; the button stayed on whatever language the page booted in — which is why "Deep Space" survived into a fully Chinese panel.
- 🏳️ **The static shell is English, not Vietnamese.** The panel's HTML was written in the upstream source language, so it painted Vietnamese for a moment before the first `_applyTranslations()` pass, and the language button's initial flag was still 🇻🇳. All 80 `data-i18n` elements now ship their English text, `<html lang>` is `en`, the flag is 🇬🇧, and the "shipped in Vietnamese, so only re-apply for other languages" condition is gone — every language re-applies now.
- 💥 **`tVal()` could throw.** It fell back to `I18N['vi']` for the bare-key lookup; with `vi` removed that expression is `undefined`, and `dict[val]` on it is a `TypeError` on any backend string that happened to be a key. It falls back to `en` now.

### Changed
- ✂️ **Eleven dictionaries removed:** vi, de, fr, nl, pl, sv, hu, cs, it, pt, sl. `_HA_LANG_MAP` keeps only `zh`; any other HA language resolves to the English default.

---

## [1.4.0] - 2026-10-02

This release supersedes the earlier `1.3.0` tag, which was pushed before the
i18n completion work, the panel-language change and the purge-safety fixes
landed. Nothing from 1.3.0 is lost; 1.4.0 is everything plus the rest.

### Added
- 🇨🇳 **简体中文 (zh-CN)** — full translation, added as the 13th interface language. The dictionary now carries 422 keys, identical across all 13 languages, with matching `{placeholders}` and HTML tags.
- 🗣️ **The panel follows Home Assistant's own language.** It used to open in Vietnamese no matter what HA was set to. An explicit choice in the language menu still wins; with nothing stored, it reads `hass.language` and maps it onto a translation, falling back to English. Retried once shortly after load, because the parent document may not expose `<home-assistant>` at `DOMContentLoaded`.
- 🗑️ **The donate button is removed**, along with its CSS and its per-language re-apply.
- 🔧 **`tools/check_i18n.py`** — fails on key drift, on a `dashLbl*` family that has drifted into another language, on a key repeated inside a block (the last definition silently wins), and on prose that carries Vietnamese diacritics without `data-i18n` or `t()`.
- 🔧 **`tools/check_version.py`** — fails when the four places that state the version disagree.
- 🔁 **`tools/test_purge_safety.py`** — 17 checks covering the delete path, runnable without Home Assistant. A reverse test re-introduces each of the six original defects and confirms the suite goes red for every one.

### Fixed
- 🏷️ **Version numbers now agree.** `manifest.json` said `1.2.2` while `const.py` and both READMEs still said `1.0.0`, so the sidebar and the docs could not be reconciled with what HACS installs. All four are maintained by `tools/check_version.py` now. (The `1.0.0` release date in this changelog was also a year off — v1.0 was published 2026-04-21, not 2025-04-19 — and is corrected.)
- 🔴 **The automatic trash expiry no longer deletes silently.** It runs unattended every 6 hours and hard-deletes anything soft-deleted longer than the configured window, irreversibly, and the only trace was one `info` log line. Every batch is now logged at warning level, raised as a persistent notification listing what went, and announced on the event bus. Entities the engine could not actually remove stay in the trash records instead of becoming untracked ghosts.
- 🔴 **The purge engine now refuses every safety device class.** `purge_engine.py` carried a second, hand-maintained copy of `SAFETY_DEVICE_CLASSES` that had drifted: it was missing `door`, `window`, `motion`, `occupancy`, `vibration` and `sound`. The scanner never suggested deleting those, but the layer that actually performs the deletion would have. Both now read the single definition in `const.py`.
- 🟠 **A failed automation/script hard delete is no longer reported as a success.** The fallback path disabled the entity and returned `True`, so the UI showed a completed delete for an entity that was still there — and because it was not added to the trash, nothing would ever restore or finish it. The function now returns an explicit `removed` / `disabled` / `not_found` status, failures land in a new `disabled_only` bucket, and the purge service keeps them tracked. Deletion now resolves the owning config entry through the entity registry instead of matching an entity-id slug against `unique_id`/`entry_id`, and the two dead `hass.data["automation_storage"]` / `hass.data["script_storage"]` lookups (keys that do not exist in Home Assistant, with unused imports) are gone.
- 🔁 New `tools/test_purge_safety.py` locks all three in with 17 checks that run without Home Assistant, plus a reverse test confirming each of the six original defects is caught when re-introduced. Wired into CI.
- 🛑 **Recorder database access no longer runs on the wrong executor.** Every analysis (`scan`, `analyze_recorder`, `analyze_dashboard`, `analyze_storms`, `analyze_health`) read the recorder database via `hass.async_add_executor_job()`. Home Assistant flags that as *"Detected that custom integration 'ha_optimizer' accesses the database without the database executor"* — the query runs on the general-purpose pool instead of the recorder's own, so it contends with the recorder's own writes for the connection. All six database-touching entry points now dispatch through `get_instance(hass).async_add_executor_job()` via a new `_async_run_in_db_executor()` helper, which keeps the previous graceful degradation if recorder is not set up. The two filesystem scans (`_scan_references`, `AutomationDeadCodeTracer._run`, which reads `.storage/core.automation`) deliberately stay on the general executor — putting them on the recorder's would cause the contention in the other direction.
- 🌍 **Eight of the thirteen dictionaries were showing a different language in the Dashboard tab.** The `dashLbl*` label family had been shuffled between dictionaries and six of them ended up holding someone else's text, with the chain running fr→German, nl→French, pl→Dutch, sv→Polish, hu→Swedish, cs→Hungarian; Italian and Slovenian had a second, foreign copy of the whole family pasted in after their own. Every one of them had a complete, correct key set, so key parity, placeholder parity and `t()` resolution all passed — the dashboard just rendered "Kritisch" to a French user and "Entités manquantes" to a Dutch one. All eight are now written in their own language, and `check_i18n.py` gained a vocabulary-drift check that compares each language's `dashLbl*` words against its own other keys; it names all eight on the pre-fix tree and stays quiet afterwards.
- 🇨🇿🇻🇳🇵🇱 Removed the duplicate-key defect described below for Italian (a Czech block overriding it) and Slovenian (a Portuguese block overriding it); Polish, French, Dutch, Swedish, Hungarian and Czech had the wrong language in the only copy.
- 🛡️ `tools/check_i18n.py` now fails on a key repeated inside a language block, not just on keys that are missing or extra. A duplicate is invisible to a key-parity check — the count matches, every key resolves — yet it silently decides which translation wins. Verified it fails on the tree before this fix.
- 📱 **Custom-panel safe-area handling (Home Assistant 2026.8+).** The frontend now applies safe-area padding to custom-panel iframes by default. The panel sized itself with `min-height: 100vh`, i.e. against the viewport, so it overflowed the padded container — a spurious scrollbar on every device and clipped content on notched ones. It now sizes against its containing block (`html { height: 100% }` + `min-height: 100%`), which is correct both before and after 2026.8. The panel does not add safe-area padding of its own, because the frontend already does and doing both would double it.
- 🌍 **Backend results are now translatable in all 13 languages.** `scanner.py` and `fingerprint.py` used to embed English (and, in `_diagnose()`, Vietnamese) prose directly in the payloads the panel renders. They now emit i18n keys — a bare key string, or `{key, params}` for parameterised text — which the panel already knew how to resolve through `tVal()`. This unblocks the Dashboard, State Storm, Dead Code, Health and Fingerprint tabs, which previously showed English to every language user regardless of the `dash_*` / `storm_*` / `dead_*` / `health_*` / `fp_*` translation entries that already existed. 23 new keys were added to cover the messages that had no existing entry.
- 🇩🇪🇵🇹 **Completed the German and Portuguese dictionaries** — both were missing 39 keys (the whole `dashLbl*` dashboard-label family plus `recorderLoading`) and silently fell back to Vietnamese for those labels. The remaining eight languages were each missing `recorderLoading`.
- 🪟 **Health tab diagnosis filtering** no longer inspects rendered text. It used `dg.startsWith('✅')` / `dg.includes('battery')` to hide the "operating normally" and battery entries, which only ever worked for the Vietnamese source strings. It now filters on the i18n key via a new `_i18nKeyOf()` helper, so it behaves correctly in every language.
- 🧹 **Remaining hardcoded UI copy removed** from the panel: the connection-error banner, the `+N more` / `Registry` / `N failed` labels, the `N cards` counter, the fingerprint metric label and unit (now resolved through the existing `fp_metric_*` / `fp_unit_*` keys), and the Vietnamese-only Health tab breakdown rows and counters.
- 🐛 Fixed two Vietnamese translations (`fingerprintDesc`, `fingerprintCollectHint`) that had an unterminated `<span data-i18n="...">` wrapper baked into the string, injecting a duplicate span into the DOM.
- 🗑️ `HEAVY_CARD_SEVERITY` and `METRIC_LABELS` now hold i18n keys instead of English prose, so the card-type and metric descriptions follow the selected language.

---

## [1.0.0] - 2026-04-21

### Added
- 🔍 **Smart Entity Scanner** — full scan of entities, automations, scripts and helpers with risk-level scoring (Low / Medium / High) and a `health_score` (0–100) for the HA instance
- 🗑️ **Safe Purge Engine** — soft delete (disable, reversible) and hard delete with trash tracking, timestamped entries and configurable auto-expiry
- ♻️ **Restore Service** — re-enable any soft-deleted entity with one service call
- 📡 **Fingerprint Anomaly Detection** — compares today's HA behaviour against a personal 30-day rolling baseline using σ / IQR statistics; detects spikes in state writes, automation triggers, unavailability events and HA lifecycle events
- 🗄️ **Recorder DB Analyzer** — queries SQLite and MySQL/MariaDB directly; identifies top-writing entities, wasteful records and generates ready-to-paste YAML optimizations
- 📊 **Lovelace Dashboard Auditor** — reads `.storage/lovelace*`; flags heavy cards, missing entities, duplicate references, uninstalled custom cards and Jinja2 template cards
- 🌩️ **State Storm Detector** — finds entities updating state abnormally fast vs their domain baseline, with severity rating and fix suggestions
- 🤖 **Automation Dead Code Analyzer** — scans UI-created automations for broken triggers, actions and conditions referencing removed entities/devices/services
- 🔌 **Integration Health Scorer** — 7-day reconnect and unavailability analysis per integration with battery-level diagnosis
- ⚙️ **Full UI Config Flow** — setup and options entirely through the HA UI, no YAML required
- 🛡️ **Safety hardcodes** — smoke, CO/gas, moisture, motion, door, window, lock and battery device classes are never suggested for deletion
- 🔄 **Auto-scan** — configurable interval (days); set to 0 to disable
- ⏰ **Daily baseline collection** — automatic fingerprint snapshot at 00:05 each day
- 🔗 **Sidebar panel** — dedicated HA Optimizer panel registered in the HA sidebar

---

*Previous versions not tracked (initial release).*
