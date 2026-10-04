## You found the one my sweep got wrong

The 原因 column showed `Suspicious name: 'temp'` in English, next to translated
neighbours. That is the backend, not the panel: `scanner.py` appends a **key** for
every reason except one, and that one spells its sentence out —

```python
reasons.append(f"Suspicious name: '{pattern}'")                 # raw English
reasons.append({"key": "reason_auto_suspicious", "params": …})  # every other site
```

The panel prints whatever arrives, and it can only translate a key. The
dictionary has had `reason_suspicious_name` all along; the string was simply
never asked for. That was the whole defect.

**The previous sweep called this a false positive, and it was wrong.** It saw the
string, found a matching English dictionary value, and put it in the "this has a
translation" bucket — because `reason_suspicious_name` in English does start
with `Suspicious name`. The *value* existing is not the same as the *key* being
used, and the sweep compared the wrong two things. Screenshots beat the sweep
here, and the sweep's own conclusion should have been treated as a claim to
check rather than an answer.

## The rule now is structural

`tools/test_reason_keys.py` reads the AST and requires every value appended to a
`reasons` list to be a key present in **both** language tables, or a dict whose
`key` is. A sentence — plain, concatenated or f-string — fails and is named.

AST rather than a grep, because an f-string is exactly how this one got in, and
a grep looking for a reason key walks straight past it. Its counter-proof puts
back the f-string, a non-key string and an invented key; the unmutated tree
stays green.

The same sweep also found that the panel has **50 functions that write to the
document and 12 that tests actually run**. The other 38 are declared gaps, so
this rule covers the reasons the scanner produces — not every English sentence
on every screen. Keep looking; there may be more in the 38.

---

## 你找到的正是我上一轮判断错的那一条

「原因」列里 `Suspicious name: 'temp'` 是英文，而旁边的邻居都是中文。这是**后端**
的问题，不是面板：`scanner.py` 给每一条原因发的都是 **key**，只有这一条把句子
拼了出来 ——

```python
reasons.append(f"Suspicious name: '{pattern}'")                 # 硬编码英文
reasons.append({"key": "reason_auto_suspicious", "params": …})  # 其他每一处
```

面板照单打印收到的内容，而它只能翻译 key。字典里**一直有**
`reason_suspicious_name`，只是从来没人去要它。缺陷就这么多。

**上一轮我的扫描把它判成假阳性，那个判断是错的。** 它看到这串文字，在英文表里
找到一个开头相同的值，就归进了"这条有翻译"那一类 —— 因为 `reason_suspicious_name`
的英文值确实以 `Suspicious name` 开头。但**值存在**和**key 被用上**是两回事，
我比错了两个东西。这一次截图赢过扫描；而扫描自己的结论本来就应该被当成**待核实的
说法**，而不是答案。

## 规则现在是结构性的

`tools/test_reason_keys.py` 读 AST，要求所有塞进 `reasons` 列表的值要么是**两
张语言表里都存在**的 key，要么是 `key` 存在的字典。句子（无论普通、拼接还是
f-string）都会失败并被点名。

用 AST 而不是 grep —— 因为 f-string 正是这一条的藏身方式，而 grep 找 key 会
直接从旁边走过去。反证把 f-string、非 key 字符串、编造的 key 分别放回去，未改动
的对照保持绿。

同一次排查还发现：面板有 **50 个写 DOM 的函数，测试只真正执行了 12 个**，其余
38 个是明确登记的盲区。所以这条规则覆盖的是**扫描器产生的原因**，不是每块屏幕上
的每一句英文。**请继续看，那 38 条里可能还有。**
