const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/js/mobile_workspace.js'), 'utf8');

class Element {
  constructor(dataset = {}, attrs = {}) {
    this.dataset = dataset;
    this.attributes = new Map(Object.entries(attrs));
    this.hidden = false;
  }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener() {}
}

const defaults = ['/documents/quick-search', '/documents/calendar', '/documents/', '/tasks/', '/chat/'];

function render(pathname, rendered = defaults) {
  const links = rendered.map(mobileLink => new Element({mobileLink}, {'aria-current': 'page'}));
  const bar = {querySelectorAll: () => links};
  const actions = ['event', 'capture', 'task', 'resubmit', 'copy', 'share'].map(
    mobileAction => new Element({mobileAction}),
  );
  const order = [];
  const menu = {querySelectorAll: () => actions, querySelector: () => null,
    append: node => order.push(node.dataset.mobileAction)};
  const toggle = new Element();
  const fab = {querySelector: selector => selector === '[data-mobile-fab-menu]' ? menu : toggle};
  vm.runInNewContext(script, {
    window: {location: {pathname}},
    document: {querySelector: selector => selector === '[data-mobile-bar]' ? bar : fab,
      addEventListener() {}},
  });
  return {
    current: links.filter(link => link.attributes.get('aria-current') === 'page').map(link => link.dataset.mobileLink),
    order,
  };
}

test('search and calendar take precedence over Files regardless of link order', () => {
  for (const links of [defaults, defaults.slice().reverse()]) {
    for (const pathname of ['/documents/calendar', '/documents/calendar/', '/documents/calendar/reminders.json']) {
      const view = render(pathname, links);
      assert.deepEqual(view.current, ['/documents/calendar']);
      assert.equal(view.order[0], 'event');
    }
    const search = render('/documents/quick-search', links);
    assert.deepEqual(search.current, ['/documents/quick-search']);
  }
});

test('unrelated lookalike routes do not inherit their neighboring section', () => {
  for (const pathname of ['/documents/calendar-export', '/documents/quick-search-old', '/documents/detail/item']) {
    const view = render(pathname);
    assert.deepEqual(view.current, ['/documents/']);
    assert.equal(view.order[0], 'capture');
  }
  const outside = render('/tasks-settings');
  assert.deepEqual(outside.current, []);
  assert.equal(outside.order[0], 'event');
});

test('section root, trailing slashes and genuine descendants remain highlighted', () => {
  for (const pathname of ['/documents', '/documents/']) {
    assert.deepEqual(render(pathname).current, ['/documents/']);
  }
  for (const pathname of ['/tasks', '/tasks/', '/tasks/export/']) {
    const tasks = render(pathname);
    assert.deepEqual(tasks.current, ['/tasks/']);
    assert.equal(tasks.order[0], 'resubmit');
  }
  assert.deepEqual(render('/chat/thread').current, ['/chat/']);
});

test('only actual rendered links can be active and ties still mark exactly one', () => {
  assert.deepEqual(render('/documents/calendar', defaults.filter(link => !link.includes('calendar'))).current,
    ['/documents/']);
  assert.deepEqual(render('/documents/calendar', ['/documents/calendar/', '/documents/calendar']).current,
    ['/documents/calendar/']);
  assert.deepEqual(render('/settings', [...defaults, '/', '']).current, []);
  assert.deepEqual(render('/', [...defaults, '/', '']).current, []);
});
