## Hard delete now actually deletes an automation, and stops lying about why

Found by creating a throwaway automation on a live instance and running the
whole lifecycle through it. Soft delete and restore both worked. Hard delete
returned:

```json
"yaml_manual": [{ "note": "This automation is defined in YAML and must be removed manually" }]
```

…and the automation was still there afterwards, enabled. Nothing had been
removed, and the reason offered was one the registry actively contradicts — the
same entity's `is_yaml_entity` was `false`.

**Why.** On Home Assistant 2026.8 the entity registry entry for an automation
made in the UI carries **no `config_entry_id` at all** (`platform` is `None`
too). The delete path took "no config entry" as proof that the automation was
YAML-defined and gave up. A YAML automation is one whose registry entry says
`platform == "yaml"`; this one was created through the same REST API the
automation editor itself uses.

The scanner in this project asks the same question a different way, and the two
answers disagreed. One of them was a guess about the registry presented as a
fact, and the user was shown it.

**What changed.** The delete now goes through the same UI-config collection the
editor's own REST endpoint is built on — the path that was verified to work on a
live instance. If that is not reachable, the result is a plain failure with the
reason it actually failed, not an invented one. "This is YAML" is now only said
when the registry says `platform == "yaml"`.

Whatever cannot be removed is disabled and recorded in the trash, so the panel
can put it back. That is one rule for every case, including the YAML one: a
YAML guess must not change what happens to the entity.

## Why you did not see this sooner

`test_purge_safety.py` covers this path and it was green. It was asking "are
there at least two places that record?" — and commenting one of them out left
two, so the check stayed green while one branch disabled an entity and recorded
nothing. That is the one state nothing in this integration can undo, reachable
by deleting a single line.

The check is now structural: **every branch that puts an entity into
`disabled_only` must also record it, in the same branch, after it.** Its
counter-proof had also gone quiet — its anchor matched two occurrences after
this change and it refused to run, saying so, which is the right way round.

This one only showed up because a real automation was put through a real
deletion. No amount of reading the code had found it: the failure needs a
2026.8 registry to be standing there.

---

## 彻底删除现在真的会删，并且不再编造理由

我在你的实例上建了一个一次性自动化，跑完整生命周期发现的。软删和恢复都正常；**彻底
删除**返回：

```json
"yaml_manual": [{ "note": "This automation is defined in YAML and must be removed manually" }]
```

然后那个自动化**还在**，还是启用状态。什么都没删，而给出的理由被注册表本身否定
—— 同一个实体的 `is_yaml_entity` 是 `false`。

**原因。** 在 Home Assistant 2026.8 上，通过 UI 创建的自动化，其实体注册表条目
**根本不带 `config_entry_id`**（`platform` 也是 `None`）。删除路径把"没有 config
entry"当成了"所以是 YAML 定义的"的证明，然后放弃。YAML 自动化的判据是注册表条目
`platform == "yaml"`；而这个是通过自动化编辑器自己用的那个 REST API 创建的。

本项目的扫描器用另一套判据问同一个问题，两个答案不一致。其中一个是关于注册表的
**猜测，却被当成事实呈现给了用户**。

**改动。** 删除现在走编辑器自己的 REST 端点所基于的那个 UI 配置集合 —— 也就是在实机
上验证过能删的那条路径。如果那条路不可达，结果就是一个如实的失败和它真正失败的原
因，而不是编造一个。只有当注册表说 `platform == "yaml"` 时，才会说"这是 YAML"。

凡是删不掉的，一律禁用并写进回收站，面板可以还原。这对所有情况是**同一条规则**，包
括 YAML 那个：是不是 YAML 的猜测，不该改变对实体本身怎么处理。

## 为什么之前没发现

`test_purge_safety.py` 覆盖这条路径，而且是绿的。它问的是"记录的地方有没有至少两
处" —— 注释掉一处还剩两处，检查照样绿，而那一支已经把实体禁用了却什么都没记。这
正是本集成唯一无法挽回的状态，**删一行代码就能到达**。

现在这个检查是结构性的：**每一支把实体放进 `disabled_only` 的分支，都必须在同一
支里、在它之后记录。** 它的反证也曾安静下来 —— 这次改动之后它的锚点匹配了两处，它
拒绝执行并明说了，方向是对的。

这一条只有把一个真实的自动化送进一次真实的删除才暴露出来。**光读代码是找不到的**：
它需要一个站在那里的 2026.8 注册表。
