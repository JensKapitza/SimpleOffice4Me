const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

const script = readFileSync(join(__dirname, '..', 'static', 'js', 'i18n.js'), 'utf8');

test('literal UI translations leave explicitly marked user data unchanged', () => {
  const element = (flag, name = 'SPAN') => {
    const attributes = new Map([['title', 'First place']]);
    return {
      tagName: name,
      closest: () => flag === null ? null : { getAttribute: () => flag },
      hasAttribute: key => attributes.has(key),
      getAttribute: key => attributes.get(key),
      setAttribute: (key, value) => attributes.set(key, value),
      matches: () => false,
    };
  };
  const untouched = element('no'), ui = element(null), override = element('yes');
  const nodes = [untouched, ui, override].map(parentElement => ({parentElement, nodeValue: 'First place'}));
  const NodeFilter = {SHOW_TEXT: 4, FILTER_ACCEPT: 1, FILTER_REJECT: 2};
  const document = {
    documentElement: {lang: 'de'},
    body: {},
    createTreeWalker(_root, _type, filter) {
      const allowed = nodes.filter(node => filter.acceptNode(node) === NodeFilter.FILTER_ACCEPT);
      return { nextNode() { this.currentNode = allowed.shift(); return !!this.currentNode; } };
    },
    querySelectorAll: () => [untouched, ui, override],
  };
  const window = {SimpleOfficeTranslations: {First: 'Vorname'}};
  runInNewContext(script, {document, window, NodeFilter});
  assert.deepEqual(nodes.map(node => node.nodeValue), ['First place', 'Vorname place', 'Vorname place']);
  assert.deepEqual([untouched, ui, override].map(el => el.getAttribute('title')), [
    'First place', 'Vorname place', 'Vorname place',
  ]);
});
