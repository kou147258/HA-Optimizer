// Counter-proof harness: inject each defect the UI audit found, run the guard
// suite against the broken copy, and require it to FAIL.
//
// A guard suite that has never been seen red is indistinguishable from a suite
// that checks nothing. Each mutation below is the literal defect as it existed
// before the fix.
//
//   node tools/counterproof_ui_labels.js
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const SUITE = path.join(ROOT, 'tools', 'test_ui_labels.py');
const PANEL = path.join(ROOT, 'custom_components', 'ha_optimizer', 'panel.html');
const original = fs.readFileSync(PANEL, 'utf8');
const tmp = path.join(os.tmpdir(), 'haopt-counterproof-panel.html');

const MUTATIONS = [
  {
    name: 'the doubled emoji on btnSelectAll (the exact original defect)',
    apply: (s) => s.replace(
      '<button class="btn btn-ghost" onclick="selectAll()" id="btnSelectAll" disabled>',
      '<button class="btn btn-ghost" onclick="selectAll()" id="btnSelectAll" disabled>\u2611\ufe0f ',
    ),
  },
  {
    name: 'a hardcoded English tooltip on a control',
    apply: (s) => s.replace(
      'data-i18n-title="entityRegistryTitle" title="Entity Registry"',
      'title="Entity Registry"',
    ),
  },
  {
    name: 'a dictionary key referenced in code but never defined',
    apply: (s) => s.replace("t('modalHardDeleteDesc', ids.length)", "t('modalHardDeleteTypo', ids.length)"),
  },
  {
    name: 'a toast message that bypasses the dictionary',
    apply: (s) => s.replace(".then(()=>toast('success', t('copiedYaml')))",
      ".then(()=>toast('success', '\u2705 Copied!'))"),
  },
  {
    name: 'the backup warning stated twice in the top strip',
    apply: (s) => s.replace(
      "    ${t('tickerSupport')}",
      "    ${t('tickerBackup')}\r\n    &nbsp;&nbsp;&nbsp;&nbsp;\u2605&nbsp;&nbsp;&nbsp;&nbsp;\r\n    ${t('tickerSupport')}",
    ),
  },
  {
    name: 'the trash can back on the reversible Disable control',
    // \u takes exactly four hex digits in JavaScript; "\U0001F5D1" is not an
    // escape at all and would insert the literal text "U0001F5D1" instead of
    // the glyph, which quietly made this counter-proof pass for a week.
    apply: (s) => s.replace(
      "btnDisable: '\u23f8\ufe0f Disable'", "btnDisable: '\u{1F5D1}\ufe0f Disable'",
    ),
  },
  {
    name: 'a key present in en but missing from zh',
    apply: (s) => s.replace("    thEntityId: '\u5b9e\u4f53 ID',\r\n", ''),
  },
  {
    name: 'a static glyph in front of a dictionary call (the ticker shape)',
    apply: (s) => s.replace("    ${t('tickerBackup')}", "    \u26a0\ufe0f&nbsp;&nbsp;${t('tickerBackup')}"),
  },
  {
    name: 'a duplicate copy of the strip stranded outside its element',
    apply: (s) => s.replace(
      '  </span>\r\n</div>',
      '    \u2605&nbsp;&nbsp;&nbsp;&nbsp;\r\n  </span>\r\n    &nbsp;&nbsp;&nbsp;&nbsp;\u2605&nbsp;&nbsp;&nbsp;&nbsp;\r\n    \u26a0\ufe0f&nbsp;&nbsp;<strong>Backup</strong> your HA before Scan &amp; Purge \u2014 hard deletes cannot be undone!\r\n    &nbsp;&nbsp;&nbsp;&nbsp;\u2605&nbsp;&nbsp;&nbsp;&nbsp;\r\n  </span>\r\n</div>',
    ),
  },
];

let bad = 0;
console.log('unmutated panel must PASS:');
fs.writeFileSync(tmp, original, 'utf8');
try {
  execFileSync('python', [SUITE, tmp], { stdio: 'pipe' });
  console.log('  PASS  suite is green on the real panel');
} catch (e) {
  console.log('  FAIL  suite is RED on the unmutated panel - fix that first');
  console.log(String(e.stdout || ''));
  process.exit(1);
}

console.log('\neach injected defect must FAIL:');
for (const [n, m] of MUTATIONS.entries()) {
  const mutated = m.apply(original);
  if (mutated === original) {
    console.log(`  SKIP  #${n + 1} ${m.name} - the mutation did not change the file (pattern moved)`);
    bad++;
    continue;
  }
  fs.writeFileSync(tmp, mutated, 'utf8');
  let failed = false, detail = '';
  try {
    execFileSync('python', [SUITE, tmp], { stdio: 'pipe' });
  } catch (e) {
    failed = true;
    detail = String(e.stdout || '');
  }
  if (!failed) {
    // The suite went green on a deliberately broken panel. Print what it said,
    // otherwise this reads as a mysterious harness bug instead of a guard hole.
    let seen = '';
    try { seen = String(execFileSync('python', [SUITE, tmp], { stdio: 'pipe' })); } catch (e) { seen = String(e.stdout || ''); }
    detail = seen;
  }
  const names = [...detail.matchAll(/^ {2}- (.+)$/gm)].map((x) => x[1]);
  console.log(`  ${failed ? 'PASS' : 'FAIL'}  #${n + 1} ${m.name}`);
  if (failed) names.forEach((x) => console.log('            caught by: ' + x));
  else {
    console.log('            suite stayed green; last lines:');
    detail.trim().split('\n').slice(-4).forEach((l) => console.log('              ' + l));
    bad++;
  }
}

fs.unlinkSync(tmp);
console.log();
if (bad) { console.log(`${bad} counter-proof(s) did not hold`); process.exit(1); }
console.log(`all ${MUTATIONS.length} counter-proofs held: every injected defect was caught`);
// The canonical verdict line. audit.py requires it, because an exit code alone
// is not a verdict - it is what a tool that failed three assertions and
// `sys.exit(0)` also produces.
console.log("PASSED");
