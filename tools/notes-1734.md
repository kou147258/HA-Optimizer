## What changed, and what you will notice

**Your fingerprint baseline will read 0 days for about a week after upgrading, and the panel will say why.**

That is not a reset bug. The days already in storage were measured over a
window this version cannot identify — the profiler used to file a "day" as
16:00-to-16:00 local time, because SQLite reads a naive day-boundary string as
UTC. The code was corrected long ago to use local midnight, but the stored days
were written by the old code and nothing recorded which window produced them.
They were being averaged against a correct local-day measurement, and a busy
evening lands in a different bucket than a busy morning; measured 8% apart.

A measurement whose window is unknown is not a slightly wrong measurement, so
those days are now left out of the average instead of being averaged into it.
The panel shows the count and the reason. The baseline fills back in on its own
as days are measured by the current rule.

**The "well past its own baseline" tag can now appear in the morning.** It
compared today's writes since midnight against a stored full-day peak, so an
entity had to exceed *twice* a whole day's traffic before the tag appeared —
impossible before about twenty hours in, which is every hour anyone is awake to
read it. Today's figure is now projected to a day before the comparison, using
the same helper the totals already use. The tag reports the observed count and
the projection separately, so nothing prints a projection as if it had been
counted.

## The check suite was reporting green on its own say-so

Worth knowing because it changes how much the green in this project's history is
worth: the harness believed a check if its process exited 0. An audit injected a
real defect and made the check covering it end with `sys.exit(0)` — it printed
`7/10 passed` and three `FAIL` lines, and 47 checks reported `ok pass`. One line
disarmed all of them.

A check now has to say it passed, in a way that cannot be faked: the count it
prints has to add up, a failure line is a failure whatever the tool, and silence
is not consent. The harness trusts its own reading of the tool rather than the
tool's exit code.

Three checks were also only looking at one spelling of the thing they guard —
they collected contract keys from *any* returned dict, only understood the
ternary form of a no-op conditional, and treated one i18n definition form as "not
a definition" and silently skipped the nine call sites under it.

## Render coverage was 12 of 50

The panel's render smoke test reported "14 of 14 render entry points called".
`render*` is a naming convention, not a coverage guarantee: 50 top-level
functions assign to a DOM property, 12 are executed by a test. The other 38 are
now declared by name with the reason each is not driven, and a new one that
nobody declares fails the check. The count is printed on every run, so the
coverage number can no longer disagree with the code in silence.

Nothing in this section changes what the integration does. It changes how much
the absence of a bug report is worth.

---

## 你会看到什么

**升级后指纹基线会显示 0 天，大约持续一周，面板会说明原因。**

这不是重置出错。store 里已有的日子是用本版本**无法识别**的窗口测出来的 ——
profiler 曾经把"一天"记成 16:00→16:00 本地时间，因为 SQLite 把朴素的日期边界
字符串当 UTC 读。代码早就改成用本地午夜了，但那些日子是旧代码写的，没有任何
字段记录它们测的是哪个窗口。它们一直被拿去和正确的本地日测量值平均，而忙的
傍晚和忙的上午落在不同的桶里 —— 实测两者差 8%。

窗口未知的测量不是"误差小一点的测量"，所以这些日子现在被**排除**而不是被平均
进去。面板会显示被排除的天数和原因。随着按当前规则测量的日子累积，基线会自行
补齐。

**"远超自身基线"的标签现在早上就能出现了。** 它拿今天从午夜起的写入数去比存下
来的整天峰值，所以要超过**两整天**的流量才报警 —— 二十点之前根本不可能，而那
正是唯一有人醒着会去看它的时段。现在先按总写入已经在用的同一个 helper 外推到
一天再比较。标签会把观测值和推算值分开报，不会把外推值当成实测值印出来。

## 检查套件以前在"自己说通过"的时候报绿

值得知道，因为这会改变这个项目历史上"绿灯"的分量：harness 以前只看去检查的进程
退出码。一次审计往组件里注入了一个真实缺陷，又让覆盖它的检查以 `sys.exit(0)`
结尾 —— 它打印 `7/10 passed` 和三条 `FAIL`，47 项检查全部报 `ok pass`。一行代码
就把它们全部解除武装了。

现在一个检查必须以无法伪造的方式说自己通过了：它自己打印的计数要对得上，失败行
无论来自哪种工具都算失败，沉默不算同意。harness 相信自己对工具输出的判读，而不是
工具的退出码。

还有三个检查只认它们所守之物的一种拼法 —— 从**任意**返回字典里收契约键、只认
恒等条件的三元形式、把一种 i18n 定义形式当作"不是定义"从而静默跳过它底下的九个
调用点。

## 渲染覆盖实际是 50 个里的 12 个

面板渲染冒烟测试报的是"14/14 渲染入口已调用"。`render*` 是命名约定，不是覆盖
保证：有 50 个顶层函数给 DOM 属性赋值，只有 12 个被测试真正执行。其余 38 个现在
按名字逐个声明并写明各自不被驱动的原因，而新增一个没人声明的会让检查变红。这个
数字每次运行都会打印，覆盖数字从此没法在沉默中与代码不一致。

这一节不改变集成做任何事，它改变的是"没有收到问题报告"这件事的分量。
