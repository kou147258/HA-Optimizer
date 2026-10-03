# 🧹 HA Optimizer

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
![version](https://img.shields.io/badge/version-1.7.12-blue)
![HA](https://img.shields.io/badge/Home%20Assistant-2024.11+-green)
![license](https://img.shields.io/badge/license-MIT-lightgrey)
![Python](https://img.shields.io/badge/Python-3.11+-yellow)
![languages](https://img.shields.io/badge/UI-English%20%2B%20%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-blueviolet)
![themes](https://img.shields.io/badge/themes-11%20built--in-ff69b4)

> 🇻🇳 **Phiên bản tiếng Việt:** [README_vi.md](README_vi.md)

> ℹ️ **This is the actively maintained line of HA Optimizer.** The original
> project by [@doanlong1412](https://github.com/doanlong1412) has been
> unmaintained since April 2026; this fork carries it forward and is where
> fixes and releases land. **Install from `kou147258/HA-Optimizer`**, not from
> the original repository — that one will not receive HA 2026.8 support, the
> Chinese translation, or the delete-path safety fixes. Original author's
> work and MIT licence are preserved and credited below.

**The smart cleanup, analysis and health-check integration for Home Assistant.**

Most Home Assistant instances accumulate hundreds of dead entities, broken automations, database bloat, and silently-failing devices over time — and nobody notices until something breaks. **HA Optimizer** surfaces all of it automatically, so you can clean up with confidence.

> ⚡ *Set it up once. Let it scan. Know exactly what's cluttering your HA instance — and clean it up safely.*

---

## 📸 Preview

![Preview 1](assets/preview1.png)
![Preview 2](assets/preview2.png)
![Preview 3](assets/preview3.png)

---

## 🔥 Why You Need This

| Problem | HA Optimizer |
|---|---|
| 💀 Dead entities from removed devices | Detects & flags them with risk level |
| 🤖 Broken automations nobody knows about | Dead code scan — triggers/actions pointing to nothing |
| 🗄️ Recorder DB growing out of control | Finds top writers, suggests YAML optimizations |
| 📊 Dashboard cards calling unavailable entities | Full Lovelace audit |
| 🌩️ Entities spamming state updates 100×/minute | State storm detector |
| 🔌 Integration that keeps disconnecting | Integration health scorer with reconnect analysis |
| ❓ "Is my HA acting weird today?" | Fingerprint anomaly detection vs your own history |
| 🧩 Add-ons scattered across HA settings | Unified add-on panel with live CPU/RAM monitoring |
| 🖥️ No visibility into host resource usage | Real-time CPU / RAM / Disk gauges always on screen |

---

## ✨ Features

### 🔍 Smart Entity Scanner
- Scans **all entities, automations, scripts, and helpers** in one pass
- Assigns **risk levels** (Low / Medium / High) so you know what's safe to delete
- Detects: stale entities (no change in N days), orphaned registry entries, suspicious naming patterns (`test_`, `temp_`, `backup_`, etc.), and YAML-defined entities that can't be auto-deleted
- **Safety first** — smoke detectors, door/window sensors, locks, motion sensors, CO/gas detectors are **never** suggested for deletion (configurable)
- Outputs a `health_score` (0–100) for your HA instance

### 🗑️ Safe Purge Engine with Soft Delete
- **Soft delete by default** — disables entities instead of deleting them, fully reversible
- **Trash bin tab** — all soft-deleted entities are listed with timestamps, remaining days before auto-expiry, and a one-click restore button
- **Auto-expiry** — trash is cleaned up automatically after N days (configurable)
- Handles automations and scripts correctly (UI-created vs YAML-defined)
- Detects already-disabled entities and still tracks them properly

### 🧩 Add-on Manager *(new)*
A full-featured add-on control panel built right into the optimizer — no more jumping between HA menus.

- Lists **all installed add-ons** sorted by priority: updates available first, then running, then stopped
- Shows **live CPU % and RAM usage** per add-on, refreshing every 5 seconds automatically — no page reload needed
- **One-click actions**: Update, Start, Stop, and open add-on details — all without leaving the panel
- Clearly highlights add-ons with **pending updates** (old version struck-through, new version highlighted in blue)
- Summary chips at the top: total count, running count, stopped count, available updates count

### 🖥️ Real-time System Resource Gauges *(new)*
Always visible at the top of every tab — you never lose sight of your host's health while using any feature.

- **Three animated semi-circle gauges** for CPU, RAM, and Disk usage
- **Gradient color arc** that flows green → orange → red as load increases (0 → 50% → 100%)
- **Animated needle** that glides smoothly to the exact usage value
- Shows absolute values beneath each gauge (e.g. `6.6 GB / 23.2 GB` for RAM)
- Displays OS name, hostname, HA version, and kernel version
- **Refreshes every 5 seconds** automatically when the Add-ons tab is open; available on all tabs via the Refresh button

### 📡 Fingerprint Anomaly Detection *(unique)*
Compares today's HA behaviour **against your own historical baseline** (up to 30 days). Uses statistical methods (σ or IQR depending on available data) to detect:
- Abnormal spike in state writes (DB load surge)
- Unusual automation trigger volume
- Integration reconnect storms
- HA lifecycle event anomalies (unexpected restarts, reloads)

Confidence level grows with more baseline days (20% → 99%). Completely private — compares only against **your own** past data, never against other users.

### 🗄️ Recorder DB Analyzer
- Queries the recorder SQLite/MySQL database directly
- Identifies **top-writing entities** (DB bloat culprits)
- Detects **wasteful records** — many writes, few distinct states
- Generates a ready-to-paste **YAML snippet** for `recorder:` optimizations
- Domain-level write statistics

### 📊 Lovelace Dashboard Analyzer
- Reads `.storage/lovelace*` config files
- Flags: heavy/complex cards, missing entities, duplicate entity references, uninstalled custom cards, Jinja2 template cards, WebSocket push pressure
- Cross-references with recorder data to identify dashboard-driven DB waste

### 🌩️ State Storm Detector
- Finds entities updating state **abnormally fast** vs their domain baseline
- Includes severity rating, ratio vs normal, and suggested fixes
- Catches misconfigured sensors before they fill your database

### 🤖 Automation Dead Code Analyzer
- Scans all UI-created automations for **broken references**
- Checks: triggers pointing to removed devices, actions calling deleted entities/services, conditions using non-existent entity states
- Silent failures in automations are exposed before they cause problems

### 🔌 Integration Health Scorer
- Analyzes **7 days of recorder data** per integration
- Scores each integration (0–100) based on reconnect frequency and unavailability patterns
- Flags abnormal disconnection bursts vs rolling average
- Detailed score breakdown showing exactly which factors caused deductions
- Diagnosis messages: "📶 Possible RF interference or device too far from hub"

### 🎨 11 Built-in Themes *(new)*
Switch the entire panel's look with one click — your preference is saved automatically.

| Theme | Style |
|---|---|
| 🌌 Deep Space | Dark navy + electric blue (default) |
| 🟣 Midnight Purple | Deep dark + violet |
| 🌲 Forest Dark | Dark green + emerald |
| 🌅 Sunset | Warm dark + orange |
| 🌊 Ocean Light | Light blue — bright mode |
| 🪨 Slate Pro | Dark indigo + purple accent |
| 🌹 Rose Gold | Dark crimson + rose |
| ⚡ Cyber Neon | Near-black + cyan glow |
| 🟡 Amber Dark | Dark sepia + golden amber |
| 🧊 Arctic | Icy white — bright mode |
| 🧛 Dracula | Classic Dracula dark + soft purple |

### 🌍 2 Interface Languages
The entire panel UI — every label, button, message, and error — is fully translated into English and Simplified Chinese. Switch instantly from the language selector in the top bar; your choice persists across sessions. Backend diagnostics (dashboard findings, state-storm advice, health diagnoses, fingerprint anomalies) are translated too.

**Supported:** 🇬🇧 English · 🇨🇳 简体中文

With nothing stored, the panel follows Home Assistant's own language: `zh-Hans` (or any `zh-*`) opens in Chinese, everything else opens in English.

> **Adding a language:** copy the `en` block inside `const I18N` in `panel.html`, paste it as a new `xx: { … }` entry, add it to the `LANGUAGES` array above, then run `python3 tools/check_i18n.py` — it verifies key coverage, `{placeholders}` and HTML-tag parity across every language, and that every key the Python backend emits actually resolves. The same check runs in CI.

---

## 🛠️ Installation

### Method 1: HACS (Recommended)
**Step 1** — Add this repository to HACS:

[![Open HACS Repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=kou147258&repository=HA-Optimizer&category=integration)

> If the button doesn't work, add manually:
1. Open HACS → **Integrations** → click the **⋮** menu → **Custom repositories**
2. Add this repository URL and select category **Integration**
3. Find **HA Optimizer** in the HACS store and click **Download**
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** → search for **HA Optimizer**
6. Complete the setup wizard

### Method 2: Manual

1. Download or clone this repository
2. Copy the `ha_optimizer/` folder into `config/custom_components/`:
   ```
   config/
   └── custom_components/
       └── ha_optimizer/
           ├── __init__.py
           ├── const.py
           ├── config_flow.py
           ├── scanner.py
           ├── purge_engine.py
           ├── store.py
           ├── fingerprint.py
           ├── manifest.json
           ├── services.yaml
           ├── strings.json
           └── panel.html
   ```
3. Restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration** → search for **HA Optimizer**

---

## ⚙️ Configuration

During setup you will be asked for:

| Setting | Default | Description |
|---|---|---|
| Auto-scan interval (days) | `7` | Set to `0` to disable automatic scanning |
| Stale days threshold | `30` | Days without state change before an entity is flagged |
| Enable soft delete | `true` | Disable entities before permanently deleting (reversible) |
| Soft delete days | `7` | Days in trash before auto-permanent deletion |
| Exclude device classes | *(safety defaults)* | Comma-separated list of device classes to never suggest deleting |

All settings can be changed at any time via **Settings → Devices & Services → HA Optimizer → Configure**.

---

## 🚀 Using the Panel

Open the **🧹 HA Optimizer** panel from the HA sidebar. The panel connects automatically using the HA WebSocket session — **no token or extra authentication required**. Everything is done through the UI — no YAML or manual service calls needed.

> ✅ **No Long-Lived Access Token needed.** The panel uses Home Assistant's own authenticated connection, the same one your browser already has open.

The panel has **9 tabs** across the top:

---

### 📋 Scan Tab — Overview & Cleanup

The main tab. See your system health at a glance and manage unused entities.

1. **Click `🔍 Start Scan`** — the scanner analyzes all entities, automations, scripts and helpers (takes a few seconds)
2. The **Overview Dashboard** appears showing:
   - **Health Score** gauge (0–100)
   - Total entities, candidates to review, breakdown by risk (🔴 High / 🟡 Medium / 🟢 Low)
   - Trash count and last scan timestamp
3. The table lists all flagged items. You can **filter** by risk, type or source, and **search** by name or entity_id
4. **Tick the checkboxes** to select items, then use the floating action bar at the bottom:
   - **🗑️ Disable** → soft delete (reversible — entity moves to Trash tab)
   - **❌ Hard Delete** → permanent removal ⚠️ irreversible
   - **✕ Deselect** → cancel

> ⚠️ Always **backup your HA** before using Hard Delete.

---

### 📊 Recorder Tab

1. Click **`📊 Analyze Recorder`**
2. See DB size, top-writing entities, wasteful records and write stats by domain
3. Copy the **ready-to-paste YAML block** into `configuration.yaml` under `recorder:` and restart HA to reduce DB growth

---

### 🖥️ Dashboard Tab

1. Click **`🖥️ Analyze Dashboard`**
2. The panel reads your Lovelace `.storage/lovelace*` files and reports: heavy cards, missing/unavailable entities, duplicate references, uninstalled custom cards, Jinja2 template cards
3. Issues are marked **Critical** or **Warning**

> ℹ️ Only UI-mode dashboards stored in `.storage/lovelace*` are supported. YAML-mode dashboards cannot be read automatically.

---

### ⚡ State Storm Tab

1. Click **`⚡ Detect State Storms`**
2. Entities updating far more frequently than their domain baseline are listed with severity, ratio vs normal, and fix suggestions
3. These are the most common cause of database bloat and slow Lovelace

---

### 🔍 Dead Code Tab

1. Click **`🔍 Analyze Dead Code`**
2. UI-created automations are scanned for broken references: triggers pointing to removed devices, actions targeting deleted entities/services, conditions using non-existent states
3. Each automation with issues shows a direct **"Open Editor"** link to fix it immediately

---

### 💚 Health Tab

1. Click **`💚 Check Integration Health`**
2. Each integration gets a score (0–100) based on 7 days of reconnect and unavailability data
3. Problem devices show: reconnect count today vs daily average, battery level (if available), and diagnosis messages
4. Status badges: **Good** / **Warning** / **Critical**

---

### 🫆 Fingerprint Tab

Compares today's HA behaviour against your **own** historical baseline — private to your instance, never compared to other users.

**First-time setup:**

1. Click **`📥 Collect Baseline`** — saves yesterday's metrics snapshot
2. Repeat daily, or it runs automatically at **00:05** every night
3. After **3–7 days**, results become meaningful (confidence reaches 75%+)

**Running an analysis:**

1. Click **`🫆 Analyze Fingerprint`**
2. Results show confidence level, anomaly count, hours elapsed today (extrapolated to 24h for fair comparison)
3. Each anomaly shows today's value vs baseline average with the method used (σ or IQR)
4. ✅ green = normal · ⚠️ orange = anomaly detected

---

### 🧩 Add-ons Tab

Full add-on control panel with live host resource data.

- Lists all add-ons with status, version, and update availability
- Live **CPU % and RAM** per running add-on, auto-refreshing every 5 seconds
- **System gauges** at the top always show host CPU / RAM / Disk
- One-click **Update / Start / Stop** without leaving the panel

> ℹ️ Requires Home Assistant OS or Supervised (Supervisor API). Not available on Container or Core installs.

---

### 🗑️ Trash Tab

All soft-deleted entities appear here with the date they were disabled.

- **♻️ Restore** — re-enables the entity and removes it from trash
- **❌ Hard Delete** — permanently removes from HA
- Entities are auto-hard-deleted after the configured number of days (default: 7)

---

### Automation Example — Weekly Scan & Notify

```yaml
automation:
  alias: "HA Optimizer - Weekly Scan"
  trigger:
    - platform: time
      at: "03:00:00"
    - platform: template
      value_template: "{{ now().weekday() == 6 }}"  # Sunday
  action:
    - service: ha_optimizer.scan
    - wait_for_trigger:
        platform: event
        event_type: ha_optimizer_scan_complete
      timeout: "00:05:00"
    - service: notify.mobile_app_your_phone
      data:
        title: "🧹 HA Optimizer"
        message: >
          Scan complete. Found {{ trigger.event.data.statistics.candidates_found }}
          candidates. Health score: {{ trigger.event.data.statistics.health_score }}/100
```

---

## 📋 Services Reference

| Service | Description |
|---|---|
| `ha_optimizer.scan` | Full scan — entities, automations, scripts, helpers |
| `ha_optimizer.purge` | Disable (soft) or permanently delete entities |
| `ha_optimizer.restore` | Re-enable a soft-deleted entity |
| `ha_optimizer.get_results` | Return last scan results as service response |
| `ha_optimizer.analyze_recorder` | Recorder DB deep analysis + YAML suggestions |
| `ha_optimizer.analyze_dashboard` | Lovelace dashboard audit |
| `ha_optimizer.analyze_storms` | State storm / high-frequency writer detection |
| `ha_optimizer.analyze_dead_code` | Broken trigger/action/condition scanner |
| `ha_optimizer.analyze_health` | Integration health scoring (7-day window) |
| `ha_optimizer.analyze_fingerprint` | Anomaly detection vs personal baseline |
| `ha_optimizer.analyze_addons` | Add-on list + live CPU/RAM + host resource data |
| `ha_optimizer.collect_baseline` | Manual baseline snapshot collection |

> **All twelve can be called from an automation, a script or Developer Tools → Actions** — none of them is blocked. What that is worth differs per service:
>
> - `scan` and `collect_baseline` **write their results to storage**, so scheduling them is genuinely useful; the panel reads what they stored.
> - The seven `analyze_*` services and `get_results` are **compute-and-return**: they answer with the data and store nothing. A scheduled call from an automation runs the analysis and discards the result. Use these from the panel, or from a tool that can surface a service response.

### Telling enabled automations apart from disabled ones

The results table has a fourth filter for it, next to risk / type / source:

| Filter | What it selects |
|---|---|
| 全部状态 | everything |
| 启用中 | automations and other results that are currently enabled |
| 已禁用 | automations and other results that are currently disabled |

Every automation row also carries a badge — ✅ 启用中 or ⛔ 已禁用 — so you can see the split without touching the filter. Rows in other categories show the badge only when they are disabled.

A disabled automation is a **state fact, not a risk judgement**: it does not change its risk level. "Disabled" and "high risk" are different questions, and the panel keeps them separate.

### 🗑️ The trash, and the two bulk buttons

Every soft delete lands in the trash, where it stays disabled and reversible. The tab shows **how many are in there** and, for each one, **how long until it is removed automatically** — sorted soonest-first, because that is the row that will disappear on its own.

| Button | What it does | Gate |
|---|---|---|
| ♻️ 一键恢复全部 | Re-enables everything in the trash and puts it back in the scan list | A plain confirm — nothing is destroyed |
| 🗑️ 清空回收站 | **Permanently** removes everything in the trash | You must **type the number of entries** before the button arms |

The asymmetry is the point. Restoring everything is the undo button for a purge that removed the wrong batch, so it should be one click away. Emptying the trash is the one bulk action here that cannot be undone and leaves no second copy, so one Enter key is not enough of a gate.

**The automatic expiry stays on as a floor** — after `soft_delete_days` (default 30) an entry in the trash is removed even if you never touch it, with a warning log, a persistent notification and an event. The countdown column makes that visible instead of surprising. Set `soft_delete_days: 0` if you would rather empty it yourself.

Anything a bulk operation *could not* really remove — a YAML-only automation, a safety device class — **stays in the trash** and is reported, rather than being reported as gone. A disabled entity nobody tracks is a ghost that nothing would ever restore or finish.

The two actions are also available as `ha_optimizer.restore_all` and `ha_optimizer.empty_trash`.

---

## 🛡️ Safety

- **Soft delete is the default** — entities are disabled, not removed. Fully reversible.
- **Safety device classes are hardcoded** — smoke, CO/gas, moisture, motion, occupancy, door, window, lock, vibration, sound, battery, problem sensors are **never** suggested.
- **YAML entities are flagged, never auto-deleted** — they require manual action.
- **Risk scoring** — every result has a risk level so you make informed decisions.

---

## 🖥️ Compatibility

| | |
|---|---|
| Home Assistant | 2024.11+ |
| Database | SQLite (default) and MySQL/MariaDB |
| Config | UI config flow — no YAML required |
| Dependencies | None — uses only HA built-ins |
| Python | 3.11+ |

> **Why 2024.11+?** That is the version Home Assistant stopped handing the config entry to the options flow's constructor and started injecting it on the instance instead. The options dialog works on both sides of that split, but this is the oldest version actually exercised here, and `manifest.json` says so — an integration that declares no minimum gets installed on versions whose APIs it does not use, and then fails in the one dialog you reach to find out.

> The panel also asks for a service response when it calls an analysis service (`return_response`, HA 2023.7+). That was the old floor, and it was the wrong one to advertise: the services kept working on old versions while the **settings dialog** raised `AttributeError` on anything before 2024.11.

---

## 📋 Changelog

### v1.0.0 — Initial Release
- 🔍 Smart entity scanner with risk levels and health score
- 🗑️ Soft delete + restore + auto-expiry trash bin tab
- 📡 Fingerprint anomaly detection (σ / IQR, 30-day baseline)
- 🗄️ Recorder DB analyzer with YAML suggestions
- 📊 Lovelace dashboard auditor
- 🌩️ State storm detector
- 🤖 Automation dead code analyzer
- 🔌 Integration health scorer with reconnect analysis
- 🧩 Add-on manager with live CPU/RAM per add-on (5s auto-refresh)
- 🖥️ Real-time system gauges (CPU / RAM / Disk) — always visible
- 🎨 11 built-in themes, saved per session
- 🌍 13 UI languages, fully translated
- ⚙️ Full UI config flow with options
- 🔐 **No Long-Lived Access Token required** — panel authenticates via HA WebSocket session

---

## 📄 License

MIT License — free to use, modify, and distribute.
If you find this useful, please ⭐ **star the repo** — it helps a lot!

---

## 🙏 Credits

Designed and developed by **[@doanlong1412](https://github.com/doanlong1412)** from 🇻🇳 Vietnam — this fork stands on that work.

**Maintained by [@kou147258](https://github.com/kou147258)**, which took over in October 2026 after the original went unmaintained: Simplified Chinese translation, English + Chinese only, HA 2026.8+ frontend compatibility, three delete-path data-loss fixes, and a guard suite in CI.

---

## ☕ Support

If HA Optimizer saves you time, the most useful thing you can do is **report
what breaks** — issues in this repository are read and acted on:

👉 [Open an issue](https://github.com/kou147258/HA-Optimizer/issues)

If you would rather support the original author directly, their
[PayPal](https://www.paypal.com/paypalme/doanlong1412) is still linked in the
original project.
