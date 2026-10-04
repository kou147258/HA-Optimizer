## 1.7.32 broke deleting, and it is fixed

**If you have been trying to purge entities since 1.7.32 and getting
`HTTP 500: Server got itself in trouble`, that was me, and here is what
happened.**

`_register_services(hass, entry)` receives the config entry as a parameter
called `entry`, and every service handler closes over it. In 1.7.32 I added a
check to the purge path — "did the engine actually leave this entity disabled,
or is the trash record about to go stale?" — and I named its loop variable
`entry` as well:

```python
ent_reg = er.async_get(hass)
for eid in entity_ids:
    entry = ent_reg.async_get(eid)        # shadows the config entry
    if entry is not None and not entry.disabled:
        refused.add(eid)
```

Python decides whether a name is local or from the enclosing scope when it
**compiles** the function, not where the assignment happens to sit. So the two
lines above, which legitimately want the config entry, silently became reads of
a local variable that had not been assigned yet:

```
UnboundLocalError: cannot access local variable 'entry' where it is not
associated with a value
```

Home Assistant passes that to the panel as a bare HTTP 500. The message names no
variable and no line, so nothing in it points at what to look for.

**Nothing was lost.** The line that raised is above the trash write, so the
purge did nothing at all — no entity was disabled and no record was written, and
the trash was still empty afterwards. Anything you tried to delete between 1.7.32
and now is untouched, and still there to delete.

**Both paths were dead.** Soft delete (to the trash, reversible) and hard delete
(permanent) go through the same handler, so neither worked. The rename also lines
up with `purge_engine.py`, which already called the same object `reg_entry` —
two files using two names for one thing is how a shadow like that survives in a
repo where the two files sit side by side.

## Why nothing caught it

Three checks cover the purge path, and all three passed. They read the source as
text or as a tree; the defect is a *scoping* rule, and a tree check sees a name
that is assigned, not a name that is read too early.

The one name-resolution check in the suite said `entry` was fine — which it
correctly concluded, because `entry` **is** defined, in the enclosing scope. It
was right about the name and wrong about the purge.

`tools/test_python_shadowing.py` is the general form of the rule: inside one
function body, a load that precedes every store of a name that the enclosing
scope also binds. Its first version passed on this very defect, because a
function *parameter* is not an `ast.Name` node and the check could not see that
`_register_services` bound `entry` at all. The counter-proof now pins all three
directions: the shipped defect, an arbitrary shadow of an arbitrary name in an
arbitrary file, and a control proving an ordinary local is not reported.

## What I checked on your instance

Called all ten services over the WebSocket API. Nine were fine; the purge was
the only failure, which is why it looked like "the panel is broken" rather than
"one path is broken".

Fingerprint from 1.7.34 is running correctly and reporting honestly:

```
baseline_days=1  stored=3  excluded={'measured before windows were recorded': 2}
```

That is the window-exclusion working: two stored days were measured over a
window this version cannot identify, they are out of the average, and the panel
is saying so. The baseline refills on its own as days are measured by the current
rule.

---

## 1.7.32 起删除功能一直是坏的，现在修好了

**如果你从 1.7.32 开始尝试清理实体、看到 `HTTP 500: Server got itself in
trouble`，那是我造成的，下面是经过。**

`_register_services(hass, entry)` 接收的参数就叫 `entry`，每个服务处理函数都闭包
引用它。1.7.32 我给 purge 路径加了一项检查 ——「引擎到底有没有把这个实体留在
禁用状态，回收站记录是不是要变陈旧」—— 我把它的循环变量也命名成了 `entry`：

```python
ent_reg = er.async_get(hass)
for eid in entity_ids:
    entry = ent_reg.async_get(eid)        # 遮蔽了外层的 config entry
    if entry is not None and not entry.disabled:
        refused.add(eid)
```

Python 在**编译**函数时就决定一个名字是局部变量还是来自外层作用域，而不是看赋值
出现在哪一行。于是上面那两行（它们真正想要的是 config entry）悄悄变成了读取一个
尚未赋值的局部变量：

```
UnboundLocalError: cannot access local variable 'entry' where it is not
associated with a value
```

Home Assistant 把这个交给面板就是一句光秃秃的 HTTP 500。消息里既没有变量名也没有
行号，**没有任何线索指向该去看哪里**。

**没有丢任何东西。** 抛异常的那行在写回收站记录**之前**，所以整个 purge 什么都没做
—— 没有实体被禁用，也没有记录被写入，事后回收站仍然是空的。你从 1.7.32 到现在的
任何一次删除尝试都只是无效操作，那些实体原封不动，还在等着你删。

**两条路径都是坏的。** 软删除（进回收站，可恢复）和硬删除（永久）走同一个处理
函数，所以两个都不能用。改名后也和 `purge_engine.py` 一致了 —— 那里早就叫
`reg_entry`；**同一个东西在两个文件里用两个名字，这就是它能活下来的原因**。

## 为什么没有任何检查抓到

purge 路径上有三个检查，它们全都通过了。它们以文本或语法树的方式读源码；而这个
缺陷是**作用域**规则 —— 语法树看得到「这个名字被赋过值」，看不到「这个名字被读
得太早」。

套件里唯一的名字解析检查说 `entry` 没问题 —— **它的结论是对的**，因为 `entry`
确实有定义，就在外层作用域。它对名字的判断没错，对 purge 的判断错了。

`tools/test_python_shadowing.py` 是这条规则的一般形式：同一个函数体内，某次读取
早于该名字的每一次赋值，而外层作用域也绑定了这个名字。它的第一版在这个缺陷上
是**绿的** —— 因为函数**参数**不是 `ast.Name` 节点，这个检查根本看不到
`_register_services` 绑定了 `entry`。现在反证钉住了三个方向：这个真实缺陷、任意
文件里任意名字的遮蔽、以及一个对照项证明普通局部变量不会被报出来。

## 我在你实例上查了什么

通过 WebSocket 调了全部十个服务。九个正常，purge 是唯一失败的那个 —— 这也是为什么
它看起来像「面板坏了」而不是「某条路径坏了」。

1.7.34 的指纹功能运行正确，并且如实上报：

```
baseline_days=1  stored=3  excluded={'measured before windows were recorded': 2}
```

这就是窗口排除在工作：有两个已存的日子是用本版本无法识别的窗口测出来的，它们被排
除在平均之外，面板也在说明原因。随着按当前规则测量的日子累积，基线会自行补齐。
