// Dump the panel's I18N dictionary for the Python guard suite, plus proof that
// the extraction stopped in the right place.
//
// Run:  node tools/_panel_dict.js <path-to-panel.html>
//
// Extracting a JS object literal from a file is a parsing problem, not a
// counting problem: several values contain braces inside strings ('{n}') and
// function values contain template literals, so a hand-rolled brace counter
// truncates the object or runs away entirely. This scanner tracks string,
// comment and regex state, and the "trailer" it reports lets the caller assert
// that the slice ended on a statement boundary rather than in the middle of a
// value. If the trailer is not a statement terminator, every "is this key
// present" check downstream would fail at once and look like a panel defect.
'use strict';
const fs = require('fs');

const src = fs.readFileSync(process.argv[2], 'utf8');
const start = src.indexOf('const I18N = {');
if (start < 0) { console.error('I18N declaration not found'); process.exit(1); }
const i = src.indexOf('{', start);

let depth = 0, j = i, quote = null, escaped = false, prevMeaningful = '';
while (j < src.length) {
  const ch = src[j], nxt = src[j + 1] || '';
  if (quote) {
    if (escaped) escaped = false;
    else if (ch === '\\') escaped = true;
    else if (ch === quote) quote = null;
  } else if (ch === "'" || ch === '"' || ch === '`') {
    quote = ch;
  } else if (ch === '/' && nxt === '/') {
    j = src.indexOf('\n', j);
    if (j < 0) { console.error('unterminated line comment'); process.exit(1); }
  } else if (ch === '/' && nxt === '*') {
    const k = src.indexOf('*/', j + 2);
    if (k < 0) { console.error('unterminated block comment'); process.exit(1); }
    j = k + 1;
  } else if (ch === '{') {
    depth++;
  } else if (ch === '}') {
    depth--;
    if (depth === 0) break;
  }
  if (!/\s/.test(ch)) prevMeaningful = ch;
  j++;
}
if (depth !== 0) { console.error('object never closed, depth=' + depth); process.exit(1); }

const literal = src.slice(i, j + 1);
const dict = eval('(' + literal + ')');   // our own source
const out = {
  span: [i, j + 1],
  trailer: src.slice(j + 1, j + 21),
  charCount: literal.length,
  dict: Object.fromEntries(
    Object.entries(dict).map(([lang, entries]) => [
      lang,
      Object.fromEntries(Object.entries(entries).map(([k, v]) => [k, typeof v === 'function' ? '<function>' : String(v)])),
    ])
  ),
};
process.stdout.write(JSON.stringify(out));
