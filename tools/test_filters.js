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
// groupFilter has to be in scope: the predicate was lifted verbatim, and a
// free variable in it is a ReferenceError, not a failed assertion.
const filterFn = new Function('allResults', 'search', 'risk', 'cat', 'yaml', 'state', 'groupFilter',
  'const filteredResults = allResults.filter(r => {' + body + '\n  }); return filteredResults;');
const run = (data, o = {}) =>
  filterFn(data, o.search || '', o.risk || '', o.cat || '', o.yaml || '', o.state || '',
           o.group === undefined ? null : o.group);

/* ── fixtures ───────────────────────────────────────────────────────────── */
const row = (id, category, disabled, extra = {}) => Object.assign({
  entity_id: id, name: id.split('.').pop(), category,
  risk_level: 'low', reason: [], is_yaml_entity: false, disabled,
  area_id: null, area_name: null,
}, extra);

const DATA = [
  row('automation.on_1', 'automation', false, { risk_level: 'medium', area_id: 'kitchen', area_name: 'Kitchen' }),
  row('automation.off_1', 'automation', true, { area_id: 'kitchen', area_name: 'Kitchen' }),
  row('automation.off_2', 'automation', true, { area_id: 'garage', area_name: 'Garage' }),
  row('sensor.off', 'entity', true, { area_id: 'garage', area_name: 'Garage' }),
  row('sensor.on', 'entity', false, { area_id: 'garage', area_name: 'Garage' }),
  row('script.off', 'script', true, { risk_level: 'high' }),
];
const ids = (rows) => rows.map(r => r.entity_id).sort();

/* ── the group filter ────────────────────────────────────────────────────── */
console.log('\narea grouping');
const kitchen = ids(run(DATA, { group: 'kitchen' }));
check('a group keeps only its own rows',
  kitchen.length === 2 && kitchen.includes('automation.on_1') && kitchen.includes('automation.off_1'),
  kitchen.join(', '));

check('a null group is not the same as no group',
  ids(run(DATA, { group: null })).length === DATA.length,
  'null means "no group selected", so nothing is filtered out');

check('an undefined group also means no group',
  ids(run(DATA, { group: undefined })).length === DATA.length,
  'a caller that forgets the argument must not empty the table');

check('an area id that matches nothing empties the table',
  ids(run(DATA, { group: 'nowhere' })).length === 0);

check('rows with no area are reachable through the empty-id group',
  JSON.stringify(ids(run(DATA, { group: '' }))) === JSON.stringify(['script.off']),
  'the leftovers have to be selectable too, or they are unreachable');

check('the group filter composes with the others',
  ids(run(DATA, { group: 'garage', state: 'disabled' })).length === 2,
  ids(run(DATA, { group: 'garage', state: 'disabled' })).join(', '));

/* ── the state filter ───────────────────────────────────────────────────── */
console.log('\nenabled/disabled filter');
const all = ids(run(DATA));
check('no filter keeps every row', all.length === 6, all.join(', '));

const disabled = ids(run(DATA, { search: '', risk: '', cat: '', yaml: '', state: 'disabled' }));
check('disabled shows exactly the disabled rows',
  JSON.stringify(disabled) === JSON.stringify(['automation.off_1', 'automation.off_2', 'script.off', 'sensor.off']),
  disabled.join(', '));

const enabled = ids(run(DATA, { search: '', risk: '', cat: '', yaml: '', state: 'enabled' }));
check('enabled shows exactly the non-disabled rows',
  JSON.stringify(enabled) === JSON.stringify(['automation.on_1', 'sensor.on']),
  enabled.join(', '));

check('the two state filters partition the whole set',
  disabled.length + enabled.length === DATA.length && enabled.every(e => !disabled.includes(e)));

/* ── the state filter composes with the others ──────────────────────────── */
console.log('\ncomposition with the existing filters');
const disabledAutomations = ids(run(DATA, { search: '', risk: '', cat: 'automation', yaml: '', state: 'disabled' }));
check('disabled + type=automation gives only disabled automations',
  JSON.stringify(disabledAutomations) === JSON.stringify(['automation.off_1', 'automation.off_2']),
  disabledAutomations.join(', '));

const disabledHigh = ids(run(DATA, { search: '', risk: 'high', cat: '', yaml: '', state: 'disabled' }));
check('disabled + risk=high gives only the high-risk disabled row',
  JSON.stringify(disabledHigh) === JSON.stringify(['script.off']), disabledHigh.join(', '));

check('an impossible combination yields nothing',
  run(DATA, { search: '', risk: 'high', cat: 'automation', yaml: '', state: 'disabled' }).length === 0);

const yamlData = [
  row('automation.yaml_off', 'automation', true, { is_yaml_entity: true }),
  row('automation.reg_off', 'automation', true, { is_yaml_entity: false }),
];
check('disabled + source=yaml keeps only the YAML one',
  JSON.stringify(ids(run(yamlData, { yaml: 'yaml', state: 'disabled' }))) ===
  JSON.stringify(['automation.yaml_off']));
check('disabled + source=registry keeps only the registry one',
  JSON.stringify(ids(run(yamlData, { yaml: 'registry', state: 'disabled' }))) ===
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
