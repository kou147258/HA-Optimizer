## 1.7.37's fix did not work, and this is the one that does

**If you installed 1.7.37, upgrading to 1.7.38 is what actually removes an
automation with hard delete. 1.7.37 still deleted nothing** — it just said so
honestly instead of claiming the thing was YAML.

The 1.7.37 path used `homeassistant.helpers.automation_config.async_get_collection`.
That module does not exist: the GitHub contents API returns 404 for it on
2026.8.3, and code search finds nothing. Every call took the `ImportError`
branch and fell through to "I do not recognise this". Honest, and useless.

I inferred that name instead of reading it. The check I wrote for it passed,
because a stub built to match my guess proves the guess is self-consistent.

### What it actually does

Read off the 2026.8.3 source this time. A UI automation is **not a config entry
at all** — the editor writes it to `automations.yaml`, which is why its registry
entry carries no `config_entry_id`, which is why the original code concluded
"YAML" and gave up. The endpoint the editor uses does two things:

1. read `automations.yaml`, drop the entry whose `id` matches, write it back;
2. then remove the entity registry row.

That is now what the delete does, in that order, with the same loader and
writer Home Assistant uses. Nothing is written at all when nothing matched.

The rest of 1.7.37 stands and is still in this release: the YAML claim is only
made when the registry says `platform == "yaml"`, and anything that cannot be
removed is disabled and recorded in the trash so the panel can put it back.

---

## 1.7.37 的修复没生效，这版才真的生效

**如果你装了 1.7.37，升到 1.7.38 才真的会彻底删除自动化。1.7.37 仍然什么都没
删** —— 只是从"谎称是 YAML"变成了"如实说做不到"。

1.7.37 走的是 `homeassistant.helpers.automation_config.async_get_collection`。
**这个模块不存在**：在 2026.8.3 上 contents API 返回 404，代码搜索也没有。每次调
用都会走 `ImportError` 分支，落到"我不认识这个"。很诚实，但没用。

那个名字是我推断出来的，不是读出来的。而我为它写的检查通过了 —— 因为照着我的推
断搭的桩，只能证明我的推断自洽。

### 它实际做的事

这次是从 2026.8.3 源码读的。**UI 自动化根本不是 config entry** —— 编辑器把它写
进 `automations.yaml`，所以它的注册表条目不带 `config_entry_id`，所以最初那段代
码得出"是 YAML"然后放弃。编辑器用的那个端点做两件事：

1. 读 `automations.yaml`，去掉 `id` 匹配的那一条，写回；
2. 然后删掉实体注册表那一行。

现在删除走的就是这两步，顺序相同，用的是 HA 自己的读写器。**什么都没匹配上时，
文件一个字节都不会写。**

1.7.37 的其余部分依然成立，也在这个版本里：只有注册表说 `platform == "yaml"`
才会说它是 YAML；凡是删不掉的，一律禁用并写进回收站，面板可以还原。
