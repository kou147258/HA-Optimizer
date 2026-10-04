Scans your instance for entities, automations and helpers nothing references, and
lets you put them away — first into a **trash you can undo**, and only then, if
you want, out of the registry for good.

Install from **Assets** below (`ha_optimizer-zh-cn-*.zip`) by copying it into
`config/custom_components/ha_optimizer/`, then restart Home Assistant. It is a
custom integration, not an add-on.

## What it does

- **Overview scan** — walks the entity registry and marks what is unreferenced,
  what has not changed state in weeks, and what an automation disabled itself.
- **Trash, not a cliff** — a purge disables the entity and records it. Restore
  puts it back. Auto-purge is off by default, so nothing disappears on its own
  unless you turn it on.
- **Recorder, dashboard and state-storm analysis** — the entities writing the
  most rows, the cards that break or never resolve, the writers updating far
  faster than they should.
- **Automation runs** — the last outcome per automation, the consecutive failure
  count, and a plain-language guess at the cause, read from Home Assistant's own
  traces.
- **Fingerprint** — compares today against your own last 30 days rather than
  against other people, and says when a day is not comparable instead of
  averaging it in anyway.
- **Bilingual** — English and 简体中文, switchable in the panel.

## Worth knowing in this version

**Deleting works again.** From 1.7.32 to 1.7.41 the purge service failed with a
bare `HTTP 500` — a loop variable in the service handler shadowed the config
entry the handler closes over, so every soft *and* hard delete raised
`UnboundLocalError` before doing anything. 1.7.36/1.7.38 also fixed hard delete
for automations, which had been silently doing nothing while reporting the
automation as "defined in YAML".

**Read this before you look at the fingerprint tab.** The stored baseline days
were measured over a window this version can no longer identify, so they are
excluded from the average rather than averaged in — a busy evening and a busy
morning land in different buckets, and the two were measured 8% apart. The
baseline therefore starts near zero and refills over about a week. The panel says
so, and says how many days were dropped and why.

**62 automated checks run against this tree, and they are the reason several of
these bugs were found at all** rather than by you. They are worth keeping in mind
when reading a green result: the harness itself had to be repaired three times
this session, because a suite that believes a check's exit code can be made to
believe anything by one line of code.

---

Scans your instance for entities, automations and helpers nothing references, and
lets you put them away — first into a **trash you can undo**, and only then, if
you want, out of the registry for good.

从下方 **Assets** 安装（`ha_optimizer-zh-cn-*.zip`）：把压缩包解压到
`config/custom_components/ha_optimizer/`，然后重启 Home Assistant。这是自定义
集成，不是加载项。

## 它做什么

- **概览扫描** —— 遍历实体注册表，标出没有被引用、很久没有状态变化、或者被自动化自己禁用的东西。
- **是回收站，不是悬崖** —— 清理会禁用实体并留下记录，恢复可以还原。**自动清理默认关闭**，
  除非你主动打开，否则不会有东西自己消失。
- **记录器 / 仪表盘 / 状态风暴分析** —— 写入最多的实体、会导致卡片报错或一直转圈的卡片、
  更新频率明显异常的写入者。
- **自动化运行情况** —— 每个自动化最近一次的结果、连续失败次数，以及基于 Home Assistant
  自带 trace 的通俗原因推测。
- **指纹** —— 把今天和你自己过去 30 天比，而不是和别人比；**不可比的日子会被排除而不是
  照样平均**。
- **中英双语** —— English / 简体中文，在面板里切换。

## 这个版本值得注意的地方

**删除又能用了。** 从 1.7.32 到 1.7.41，清理服务一直报 `HTTP 500` —— 服务处理函数里的一个
循环变量遮蔽了它闭包里引用的 config entry，于是**软删除和硬删除**都抛
`UnboundLocalError`，而且是在动手之前就抛，什么都没做。1.7.36 / 1.7.38 还修掉了
自动化的硬删除 —— 它之前什么都没删，却报告说那个自动化"是 YAML 定义的"。

**看指纹页签之前请先读这段。** 已存的基线日子是用本版本**无法识别**的窗口测出来的，所以
它们被排除在平均之外，而不是照样平均进去 —— 忙的傍晚和忙的上午落在不同的桶里，两者
实测差 8%。因此基线会从接近 0 开始，大约一周补齐。面板会说明这一点，也会告诉你排除了
多少天、为什么。

**这个代码树上有 62 项自动检查**，上面这几个 bug 之所以被发现、而不是被你发现，靠的就是
它们。**读到一个"全绿"时请把这件事记在心里**：这一套检查自己也修了三次，因为一个只
相信子进程退出码的检查，任何人都能用一行代码骗过它。
