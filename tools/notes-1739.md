## 类型列和类型筛选下拉框现在也跟随语言

The scan table's type column had a Chinese header reading 类型 above cells
reading `Entity`, `Auto`, `Helper` and `Script` — and the filter dropdown above
the table had the same four words, in English, with no translation hook at all
while the "all types" option directly above them had one.

The category label was a literal inside the render function:

```js
const catLabel = { entity: '⚡ Entity', ..., automation: '🤖 Auto', ... }
```

so it never reached the dictionary and never could be translated. Both maps now
go through `t()`, and the four `<option>`s carry `data-i18n` like every other
control. Nothing errored about this before: the text was correct, in one
language, permanently.

**A fourth class of label defect** is now guarded in `test_ui_labels.py` — a
label spelled out in JavaScript is English in every language, forever — along
with the eight new keys being present in *both* tables, and each filter option
actually carrying the hook. Its counter-proof puts each of those three back and
requires the check to go red; the control, the unmutated tree, stays green.

---

## 类型列和类型筛选下拉框现在也跟随语言

扫描表的类型列，表头是中文的「类型」，格子里的却是 `Entity`、`Auto`、`Helper`、
`Script`；表格上方的筛选下拉框同样是这四个英文词，而且**完全没有翻译钩子** —— 紧挨
着的「全部类型」那个选项反而有。

原因是分类标签是渲染函数里的一个字面量：

```js
const catLabel = { entity: '⚡ Entity', ..., automation: '🤖 Auto', ... }
```

它从来没进过字典，也就永远无法被翻译。现在两个映射都走 `t()`，四个 `<option>`
和其他控件一样带上 `data-i18n`。

**`test_ui_labels.py` 现在守着第四类标签缺陷** —— 在 JavaScript 里拼写出来的标签，
在**任何**语言下都是英文 —— 同时守着八个新 key 在**中英两表都存在**，以及每个筛
选项确实带上了钩子。它的反证把这三种情况分别放回去，要求检查变红；未改动的对照
保持绿。
