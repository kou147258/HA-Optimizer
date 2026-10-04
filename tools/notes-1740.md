## What the sweep found

Asked whether the whole panel supports both languages, so the whole panel was
measured rather than the part that was reported:

- **567 entries in each table** — English and Chinese are the same size, no
  orphans on either side.
- **Every key referenced from code is present in both tables.** No fallback to
  English, and no `t()` returning a key name.
- **Every entry the sweep swept for turned out to be one of:** a placeholder in
  the markup or a template that `t()` replaces at runtime (47 of them, including
  the recorder's `Entity` / `Records` headers and the whole ticker row), CSS, a
  class name, a console-log prefix, or a comment.

Two real strings came out of it and are fixed here:

- **`Connecting…`** in the connection indicator, shown for a moment before its
  script replaces it. It now goes through the dictionary like every other
  element — it was a flash of English in a Chinese panel, which is short but is
  still English.
- **`(unversioned copy)`** in the build stamp, shown when the panel is opened
  without a `?v=`. This one **cannot** use the dictionary: it is set by a script
  that runs before the main script defines `t()`, which is the whole reason
  that block exists — it reports the build before the panel has measured
  itself. So the two words are spelled out in both languages there, with the
  reason recorded. A translation that code cannot reach would be a dictionary
  entry lying about being translated.

Three candidates were checked and are correct as they are: `English` in the
language picker is the language's own name (`LANGUAGES` lists `简体中文` the
same way), and `CPU` / `RAM` / `Disk` / `/ 100` are hardware labels and a number.

## The method is worth keeping

The first version of the sweep scanned the whole file with `> ... <` and matched
straight through the `<script>` body, so every line of the panel's own
**comments** came back as candidate user-visible text — the defect list was
mostly my own prose explaining the defects. Stripping JavaScript comments first
is what turned 200+ candidates into a list short enough to actually read.

---

## 这次全量排查的结果

被问到整个面板是否都支持中英双语，所以量的是**整个面板**，不是被报出来的那一列：

- **两张表各 567 条** —— 中英条目数相同，没有单边残留。
- **代码里引用的每一个 key，两张表里都存在。** 没有回退到英文的情况，也没有
  `t()` 返回 key 名的情况。
- **被扫到的每一条，要么是运行时会被 `t()` 替换掉的静态占位**（47 条，包括记录
  页的 `Entity` / `Records` 表头和整条滚动提示）、**要么是 CSS、类名、控制台
  日志前缀、或者注释**。

扫出两条真问题，本次已修：

- **连接状态里的 `Connecting…`** —— 脚本随后会用 `t('healthConnected')` 替换
  它，但替换前那一瞬间是英文。现在和其他元素一样走字典。它只是一闪而过，但
  仍然是英文。
- **构建版本号里的 `(unversioned copy)`** —— 面板在没有 `?v=` 打开时显示。这
  一条**不能用字典**：它由一个在主脚本定义 `t()` **之前**就运行的脚本设置，而
  那正是这个块存在的原因 —— 它要在面板自我测量之前先报告构建信息。所以这两个
  词在那个位置直接用两种语言拼出来，并把理由写在代码里。**一份代码根本取不到
  的翻译，是一条谎称自己已被翻译的字典条目。**

另有三条核对后确认现状正确：语言选择器里的 `English` 是该语言的本名（`LANGUAGES`
同样列着 `简体中文`），以及 `CPU` / `RAM` / `Disk` / `/ 100` 是硬件缩写和数字。

## 这个排查方法值得留着

第一版扫描用 `> ... <` 扫全文，直接穿过 `<script>`，于是面板自己的**注释**全部
被当成"用户可见文字"返回 —— 缺陷清单里大部分是我自己解释缺陷的散文。**先把
JavaScript 注释剥掉**，才把两百多条候选压成一份短到能真读一遍的清单。
