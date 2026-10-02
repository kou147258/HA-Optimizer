/* The browser tool cannot drive a <select>'s inline onchange reliably - the
 * pre-existing risk filter does not respond to it either - so a rendered click
 * test is not evidence about the filter. This one is: it lifts the ACTUAL
 * predicate out of panel.html and runs it, so it tests the shipped source
 * rather than a re-implementation of it.
 *
 * usage: node tools/test_filters.js
 */
const fs = require('fs');
const path = require('path');

const PANEL = path.join(__dirname, '..', 'custom_components', 'ha_optimizer', 'panel.html');
const src = fs.readFileSync(PANEL, 'utf8');

const FAILURES = [];
function check(name, cond, detail = '') {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (cond || !detail ? '' : `   [${detail}]`));
  if (!cond) FAILURES.push(name);
}

/* ── lift the real predicate ────────────────────────────────────────────── */
const m = src.match(/filteredResults = allResults\.filter\(r => \{([\s\S]*?)\n  \}\);/);
check('the filter predicate could be located in panel.html', !!m);
if (!m) process.exit(1);

const body = m[1];
const filterFn = new Function('allResults', 'search', 'risk', 'cat', 'yaml', 'state',
  'const filteredResults = allResults.filter(r => {' + body + '\n  }); return filteredResults;');

/* ── fixtures ───────────────────────────────────────────────────────────── */
const row = (id, category, disabled, extra = {}) => Object.assign({
  entity_id: id, name: id.split('.').pop(), category,
  risk_level: 'low', reason: [], is_yaml_entity: false, disabled,
}, extra);

const DATA = [
  row('automation.on_1', 'automation', false, { risk_level: 'medium' }),
  row('automation.off_1', 'automation', true),
  row('automation.off_2', 'automation', true),
  row('sensor.off', 'entity', true),
  row('sensor.on', 'entity', false),
  row('script.off', 'script', true, { risk_level: 'high' }),
];
const ids = (rows) => rows.map(r => r.entity_id).sort();

/* ── the state filter ───────────────────────────────────────────────────── */
console.log('\nenabled/disabled filter');
const all = ids(filterFn(DATA, '', '', '', '', ''));
check('no filter keeps every row', all.length === 6, all.join(', '));

const disabled = ids(filterFn(DATA, '', '', '', '', 'disabled'));
check('disabled shows exactly the disabled rows',
  JSON.stringify(disabled) === JSON.stringify(['automation.off_1', 'automation.off_2', 'script.off', 'sensor.off']),
  disabled.join(', '));

const enabled = ids(filterFn(DATA, '', '', '', '', 'enabled'));
check('enabled shows exactly the non-disabled rows',
  JSON.stringify(enabled) === JSON.stringify(['automation.on_1', 'sensor.on']),
  enabled.join(', '));

check('the two state filters partition the whole set',
  disabled.length + enabled.length === DATA.length && enabled.every(e => !disabled.includes(e)));

/* ── the state filter composes with the others ──────────────────────────── */
console.log('\ncomposition with the existing filters');
const disabledAutomations = ids(filterFn(DATA, '', '', 'automation', '', 'disabled'));
check('disabled + type=automation gives only disabled automations',
  JSON.stringify(disabledAutomations) === JSON.stringify(['automation.off_1', 'automation.off_2']),
  disabledAutomations.join(', '));

const disabledHigh = ids(filterFn(DATA, '', 'high', '', '', 'disabled'));
check('disabled + risk=high gives only the high-risk disabled row',
  JSON.stringify(disabledHigh) === JSON.stringify(['script.off']), disabledHigh.join(', '));

check('an impossible combination yields nothing',
  filterFn(DATA, '', 'high', 'automation', '', 'disabled').length === 0);

const yamlData = [
  row('automation.yaml_off', 'automation', true, { is_yaml_entity: true }),
  row('automation.reg_off', 'automation', true, { is_yaml_entity: false }),
];
check('disabled + source=yaml keeps only the YAML one',
  JSON.stringify(ids(filterFn(yamlData, '', '', '', 'yaml', 'disabled'))) ===
  JSON.stringify(['automation.yaml_off']));
check('disabled + source=registry keeps only the registry one',
  JSON.stringify(ids(filterFn(yamlData, '', '', '', 'registry', 'disabled'))) ===
  JSON.stringify(['automation.reg_off']));

/* ── a row with the field absent must not be called disabled ────────────── */
console.log('\nrows from an older build');
const legacy = [
  { entity_id: 'automation.legacy', name: 'legacy', category: 'automation', risk_level: 'low' },
];
check('a row with no disabled field counts as enabled, not as disabled',
  filterFn(legacy, '', '', '', '', 'disabled').length === 0 &&
  filterFn(legacy, '', '', '', '', 'enabled').length === 1);

console.log();
if (FAILURES.length) {
  console.log(`${FAILURES.length} check(s) FAILED: ${FAILURES.join(', ')}`);
  process.exit(1);
}
console.log('all filter checks passed');
