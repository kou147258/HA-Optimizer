## Two more, from the two screenshots

**The recorder tab** showed `ENTITY` and `RECORDS` as column headers. They are
literals in the markup with no `data-i18n`, while every other header in the
panel has one. They now go through the dictionary.

**The fingerprint tab** showed `measured before windows were recorded: 2` — the
sentence the backend returns, printed raw. That is the same defect the scan
reason was, one module over: `fingerprint.py` was sending English prose where it
should have sent a key. The reasons are now `{key, params}` and render through
`escapeHtml(tVal(...))`.

## And a hole behind that second one

The exclusion reasons interpolate `params` that came from the backend, and they
went into the markup unescaped. A backend string became markup — the same hole
the area id had in 1.7.33, one layer over, and found only because the payload
in the render check was given a real probe instead of a string that was never in
the data.

## About the sweep that missed both

The earlier dictionary sweep put these in the "this has a translation" bucket,
because an English value with the same words exists in the table. **A value
existing is not a key being used** — the same confusion as the scan reason, and
the same mistake twice. What it proves is that a value-based sweep cannot answer
the question it was asked; the rule that does answer it is structural, and is
`source.reason_keys` from 1.7.41.

This is the third finding in a row that a screenshot caught and an automated
sweep did not. The sweeps are worth keeping — they found the category column and
the connection status — but they are not a substitute for a person looking at the
screen.

---

## 来自两张截图的另外两条

**记录器页签**的列头显示 `ENTITY` 和 `RECORDS`。它们是标记里的字面量，没有
`data-i18n`，而面板里其他每一个表头都有。现在走翻译表。

**指纹页签**显示 `measured before windows were recorded: 2` —— 后端返回的句
子，被原样打印。这和扫描器那条原因是**同一个缺陷，只是差一个模块**：
`fingerprint.py` 在该发 key 的地方发了英文散文。现在这些原因以
`{key, params}` 发送，并通过 `escapeHtml(tVal(...))` 渲染。

## 第二条背后还有一个洞

排除原因会插值来自后端的 `params`，而它们未经转义就进了标记。**一个后端字符串
变成了标记** —— 和 1.7.33 修的 area id 是同一个洞，只是深了一层；能发现它，
是因为渲染检查里的 payload 换成了一个真的探针，而不是一个从来不在数据里的
字符串。

## 关于没抓到这两条的扫描

之前那次字典扫描把它们归进了"这条有翻译"那一类，因为表里确实有一个英文值以
同样的词开头。**值存在，不等于 key 被用上** —— 和扫描器那条原因一模一样的混
淆，而且错了两次。它证明的是：**基于值的扫描回答不了它被问的那个问题**。真正
能回答的是结构性的规则，也就是 1.7.41 的 `source.reason_keys`。

**连续第三条是截图抓到、扫描没抓到的。** 扫描仍然值得保留 —— 它找到了类型列
和连接状态 —— 但它替代不了有人真的看一眼屏幕。
