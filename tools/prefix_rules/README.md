# prefix_rules

Frozen copies of three checks AS THEY WERE BEFORE their holes were closed.
They are not tools and are never run by the harness; nothing imports them.

They exist for one reason. Each counter-proof next to a fixed check measures
itself twice: the current rule must catch an injected defect, and the
pre-fix rule must NOT. Without the second half the counter-proof cannot tell
"the new rule caught this" from "the file is simply broken", so a syntax
error or a crash would satisfy it.

The obvious way to get the pre-fix rule is `git show HEAD:tools/<check>`,
and that works until the fix is committed - at which point HEAD holds the new
rule and the comparison has nothing left to make. These files do not rot.

**Do not update them to match the current checks.** Each counter-proof still
asserts that its frozen copy differs from the rule it is testing; making them
identical makes it fail loudly, which is the intended response.
