## The common causes now follow your language

The seven explanations under **What usually causes this** were English text
hardcoded in the backend, and the panel printed them as-is — so a Chinese label
sat directly above an English paragraph, on every failing automation, every
time you looked at it.

The backend now sends a translation key and the panel renders it through the
same table as everything else, so it follows the language you picked in the
panel. A key with no translation in your language would now show the key name
rather than silently falling back to English — visible, which is the point.

## Running an automation no longer looks like nothing happened

`上次运行` was taken from the trace bucket first. That bucket only reaches disk
when Home Assistant stops, so it lags — often by hours. `last_triggered`, which
is live and comes from the automation's own state, was only used when there was
no stored run at all.

So you would run an automation, watch the timestamp not move, and have no way to
tell that from a page that had not refreshed. The newer of the two is shown now,
and when they disagree the row says so:

> 已于 … 触发，但已存储的运行记录只到 …。HA 只在停止时把 trace 写盘，所以最近几小时的运行还不在里面——重启后就会出现。

That is a limit of Home Assistant's trace storage, not something this integration
can work around — but it can be said out loud instead of looking like a bug.

---

## 常见原因现在跟随你的语言

**「这类错误的常见原因」下面那七条解释是后端硬编码的英文**，面板原样打印 —— 所以
每次你查看一个失败的自动化，都会看到一行中文标签，紧接着一段英文。

后端现在只发翻译 key，面板走和其他所有文案一样的翻译表，所以它跟随你在面板里选的
语言。某个 key 在你的语言下没有翻译时，现在会显示 key 名字，而不是悄悄退回英文
—— 看得见，这正是重点。

## 跑了自动化不再像是"什么都没发生"

`上次运行` 以前优先取 trace 桶。而 trace 桶只在 HA 停止时才落盘，所以它经常落后
好几个小时；实时的 `last_triggered`（来自自动化自身状态）只有在完全没有已存运行
记录时才被用到。

于是你会：运行一个自动化 → 看着时间戳不动 → 而且**无法区分**这是页面没刷新还是真
没跑。现在显示两者中较新的那个；两者矛盾时，这一行会说明：

> 已于 … 触发，但已存储的运行记录只到 …。HA 只在停止时把 trace 写盘，所以最近几小时的运行还不在里面——重启后就会出现。

这是 Home Assistant trace 存储本身的限制，这个集成绕不过去 —— 但它可以**说出口**，
而不是看起来像 bug。
