One line, and it is a reversal of a decision that was made deliberately and
argued for in a comment — which is the interesting part.

On every upgrade the integration notices that the panel copy under `www/` is
from a previous version, replaces it with the installed one, and says so. It
logged that at WARNING, on the reasoning that "the stale copy is worth knowing
about", balanced against "a message that only reports a problem trains people
to dismiss the banner".

The balance was decided backwards, and a real user answered it: the message was
pasted to me as a failure — while its own text said *Nothing is wrong now -
reload the panel page to see the current version.*

Home Assistant renders a WARNING as a red card under "errors from custom
integrations", and it cannot tell that this particular warning already fixed
itself. A notice whose entire content is "the replacement succeeded, reload the
page" has no business wearing the costume of something that needs fixing. It is
now INFO. Nothing is lost — it still fires on every upgrade and still reports
both byte counts — it just stops arriving as an alarm.

If you are upgrading from 1.7.42 and see no such message, that is the change
working.

---

一行，而且这是一个**被刻意论证过的决定的反转** —— 有意思的地方正在这里。

每次升级时，集成会发现 `www/` 下的面板副本来自上一个版本，把它换成已安装的那份，
并把这件事记下来。它记在 **WARNING** 级别，理由是"陈旧副本值得知道"，与"只报问题
不给动作的消息会训练人忽略横幅"相权衡。

**这个权衡判反了，而且被一个真实用户回答了**：那条消息被当成故障发给了我 —— 而
它自己的文字写的是 *Nothing is wrong now - reload the panel page to see the
current version.*

Home Assistant 把 WARNING 渲染成"此错误来自自定义集成"下的红色卡片，**它无法分辨
这一条已经自己修好了**。一条全部内容都是"替换已成功，请刷新页面"的通知，没有理
由打扮成需要处理故障的样子。现在它是 INFO。信息没有丢 —— 它每次升级仍然会记录、仍
然会报两个字节数 —— 只是不再以警报的形式送到你面前。

如果你从 1.7.42 升上来之后**看不到**那条消息了，那就是这个改动在生效。
