import {logic, gateSignal, gateStep, appleSignal, appleStep, matchingIds, SetChallenge} from './kids_logic_rules.mjs';

const de = {
  circuit: {
    single: 'Schließt A den Weg zur Batterie, kann die Lampe leuchten.',
    and: 'A UND B müssen geschlossen sein. Beide liegen auf demselben Weg.',
    or: 'A ODER B reicht. Sind beide geschlossen, leuchtet die Lampe auch.',
    not: 'Der Logikbaustein dreht nur das Signal um: aus wird an und an wird aus. Er ist kein offener Schalter im Batteriekreis.',
    lightOn: '💡 Lampe an.', lightOff: '○ Lampe aus.',
  },
  gate: {
    positions: ['Tor unten.', 'Tor in der Mitte.', 'Tor oben.'], conflict: 'Stopp: Hoch und Runter sind zugleich an. Schalte eine Richtung aus.',
    top: 'Oben angekommen. Der Endschalter stoppt Hoch.',
    bottom: 'Unten angekommen. Der Endschalter stoppt Runter.',
    up: 'Bereit für einen Schritt hoch.', down: 'Bereit für einen Schritt runter.',
    idle: 'Tor wartet: Wähle Hoch oder Runter.',
  },
  apple: {
    move: 'Das Fließband ist bereit: Beim nächsten Schritt fährt der Apfel zur Presse.',
    process: 'Die Presse ist bereit: Beim nächsten Schritt wird aus dem Apfel Mus.',
    fill: 'Mus und ein leeres Glas sind da, der Abfüller ist an. Der nächste Schritt füllt das Glas.',
    done: '🫙 Fertig! Rechts steht ein Glas Apfelmus.',
    needs_belt: 'Der Apfel wartet links. Schalte das Fließband ein.',
    needs_processing: 'Der Apfel ist angekommen. Schalte die Presse ein.',
    needs_glass: 'Das Mus ist bereit. Stell ein leeres Glas hin.',
    needs_filling: 'Mus und Glas sind da. Schalte den Abfüller ein.',
    needs_both: 'Das Mus ist bereit. Es fehlen ein leeres Glas UND der eingeschaltete Abfüller.',
  },
  sets: {
    and: 'Finde Karten, die rot UND rund sind.',
    or: 'Finde Karten, die rot ODER rund sind. Eine Karte, die beides ist, gehört auch dazu.',
    not: 'Finde unter allen Karten die, die NICHT rot sind.',
    difference: 'Finde Karten, die rot UND NICHT rund sind.',
    right: '🌼😊 Genau! Du hast alle passenden Karten gefunden.', answer: 'Das ist die Lösung. Passende Karten sind mit ✓ markiert.',
    wrong: (missing, outside, attempt) => `Noch nicht ganz! ${missing} passende Karte(n) fehlen, ${outside} gewählte passen nicht. Versuch ${attempt} von 5.`,
    limit: 'Fünf Versuche sind vorbei. Du kannst dir jetzt die Lösung ansehen.',
    attempts: (count) => `Fehlversuche: ${count} von 5`,
    labels: {right:'✓ Passt', missing:'＋ Fehlt', outside:'× Passt nicht'},
  },
  story: {progress: (index, count) => `Bild ${index} von ${count}`},
};
const en = {
  circuit: {
    single: 'When A closes the loop to the battery, the lamp can light.',
    and: 'A AND B must be closed. Both are on the same path.',
    or: 'A OR B is enough. Closing both also lights the lamp.',
    not: 'This logic block flips the signal: off becomes on, and on becomes off. It is not an open switch in the battery loop.',
    lightOn: '💡 Lamp on.', lightOff: '○ Lamp off.',
  },
  gate: {
    positions: ['Gate at the bottom.', 'Gate halfway up.', 'Gate at the top.'], conflict: 'Stop: Up and Down are both on. Turn one direction off.',
    top: 'The upper limit switch stops the gate going farther up.',
    bottom: 'The lower limit switch stops the gate going farther down.',
    up: 'Ready to move one step up.', down: 'Ready to move one step down.',
    idle: 'The gate is waiting. Choose Up or Down.',
  },
  apple: {
    move: 'The conveyor is ready. Next step: carry the apple to the press.',
    process: 'The press is ready. Next step: turn the apple into sauce.',
    fill: 'The applesauce, empty jar and filler are ready. The next step fills the jar.',
    done: '🫙 Done! Your jar of applesauce is on the right.',
    needs_belt: 'The apple is waiting on the left. Turn on the conveyor.',
    needs_processing: 'The apple has arrived. Turn on the press.',
    needs_glass: 'The sauce is ready. Put out an empty jar.',
    needs_filling: 'The sauce and jar are ready. Turn on the filler.',
    needs_both: 'The sauce is ready. It needs an empty jar AND the filler switched on.',
  },
  sets: {
    and: 'Find tiles that are red AND round.',
    or: 'Find tiles that are red OR round. A tile that is both counts too.',
    not: 'From all the tiles, find ones that are NOT red.',
    difference: 'Find tiles that are red AND NOT round.',
    right: '🌼😊 Exactly right! You found all matching tiles.', answer: 'Here is the answer. Tiles that fit have a ✓.',
    wrong: (missing, outside, attempt) => `Not quite! ${missing} matching tile(s) are missing, and ${outside} selected tile(s) do not fit. Try ${attempt} of 5.`,
    limit: 'That was five tries. You can reveal the answer now.',
    attempts: (count) => `Wrong tries: ${count} of 5`,
    labels: {right:'✓ Fits', missing:'＋ Missing', outside:'× Does not fit'},
  },
  story: {progress: (index, count) => `Picture ${index} of ${count}`},
};
const query = (root, selector) => root.querySelector(selector);
const all = (root, selector) => [...root.querySelectorAll(selector)];
function say(node, text) { if (node.textContent !== text) node.textContent = text; }
function button(root, selector, action) {
  const control = query(root, selector);
  control.disabled = false;
  control.addEventListener('click', action);
  return control;
}
function switches(root, onChange) {
  const nodes = all(root, '[data-switch]');
  const state = Object.fromEntries(nodes.map((node) => [node.dataset.switch, false]));
  for (const node of nodes) {
    node.disabled = false;
    node.addEventListener('click', () => {
      const key = node.dataset.switch;
      state[key] = !state[key];
      node.setAttribute('aria-pressed', String(state[key]));
      onChange();
    });
  }
  return {state, nodes, clear() {
    for (const node of nodes) {
      state[node.dataset.switch] = false;
      node.setAttribute('aria-pressed', 'false');
    }
  }};
}
function wireCircuit(root, text) {
  const mode = query(root, '[data-circuit-mode]');
  const display = query(root, '[data-circuit-display]');
  const rule = query(root, '[data-circuit-rule]');
  const status = query(root, '[data-logic-status]');
  const control = switches(root, render);
  const diagrams = all(display, '[data-circuit]');
  function render() {
    const {a, b} = control.state;
    const lit = logic(mode.value, a, b);
    display.dataset.a = a ? 'on' : 'off';
    display.dataset.b = b ? 'on' : 'off';
    display.dataset.lit = lit ? 'on' : 'off';
    say(rule, text[mode.value]);
    say(status, lit ? text.lightOn : text.lightOff);
    control.nodes[1].hidden = control.nodes[1].disabled = !['and', 'or'].includes(mode.value);
    diagrams.forEach((plot) => plot.toggleAttribute('hidden', plot.dataset.circuit !== mode.value));
  }
  mode.disabled = false;
  mode.addEventListener('change', () => { control.clear(); render(); });
  render();
}
function wireGate(root, text) {
  let position = 0;
  const display = query(root, '[data-position]');
  const indicator = query(root, '[data-gate-position]');
  const status = query(root, '[data-logic-status]');
  const {state} = switches(root, render);
  function render() {
    display.dataset.position = String(position);
    say(indicator, text.positions[position]);
    say(status, text[gateSignal(position, state.up, state.down)]);
  }
  button(root, '[data-step]', () => { position = gateStep(position, state.up, state.down).position; render(); });
  render();
}
function wireApple(root, text) {
  let stage = 'left';
  const status = query(root, '[data-logic-status]');
  const stations = all(root, '[data-station]');
  const control = switches(root, render);
  function render() {
    for (const node of stations) {
      if (node.dataset.station === stage) node.setAttribute('aria-current', 'step');
      else node.removeAttribute('aria-current');
    }
    say(status, text[appleSignal(stage, control.state)]);
  }
  button(root, '[data-step]', () => { stage = appleStep(stage, control.state).stage; render(); });
  button(root, '[data-reset]', () => { stage = 'left'; control.clear(); render(); });
  render();
}
function wireSets(root, text) {
  const mode = query(root, '[data-set-operation]');
  const prompt = query(root, '[data-set-rule]');
  const result = query(root, '[data-set-result]');
  const attempts = query(root, '[data-set-attempts]');
  const next = query(root, '[data-set-next]');
  const grid = query(root, '[data-token-grid]');
  const cards = all(root, '[data-token]');
  const objects = cards.map(({dataset}) => ({id: dataset.token, shape: dataset.shape, color: dataset.color}));
  const challenge = new SetChallenge(objects, mode.value);
  const shuffle = (items) => {
    const shuffled = [...items];
    for (let i = shuffled.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
  };
  const clearFades = () => cards.forEach((node) => {
    delete node.dataset.mistakes;
    node.style.removeProperty('opacity');
  });
  const shuffleCards = () => grid.replaceChildren(...shuffle(cards));
  const freshChallenge = () => {
    clearFades();
    shuffleCards();
    render();
  };
  function render(marks = new Map(), feedback = '') {
    say(prompt, text[mode.value]);
    for (const node of cards) {
      const id = node.dataset.token;
      const mark = marks.get(id);
      node.setAttribute('aria-pressed', String(challenge.selected.has(id)));
      node.disabled = challenge.solved || challenge.attempts >= challenge.maxAttempts;
      if (mark) node.dataset.result = mark;
      else delete node.dataset.result;
      say(query(node, '[data-token-hint]'), mark ? text.labels[mark] : '');
      node.setAttribute('aria-label', mark ? `${node.dataset.tokenLabel}. ${text.labels[mark]}` : node.dataset.tokenLabel);
    }
    say(attempts, text.attempts(challenge.attempts));
    query(root, '[data-set-check]').disabled = challenge.solved || challenge.attempts >= challenge.maxAttempts;
    query(root, '[data-set-reveal]').disabled = !challenge.canReveal;
    next.hidden = !challenge.solved;
    say(result, feedback);
  }
  for (const node of cards) {
    node.disabled = false;
    node.addEventListener('click', () => {
      challenge.toggle(node.dataset.token);
      render();
    });
  }
  const clear = () => { challenge.reset(); freshChallenge(); };
  mode.disabled = false;
  mode.addEventListener('change', () => { challenge.setOperation(mode.value); freshChallenge(); });
  button(root, '[data-set-reset]', clear);
  button(root, '[data-set-next]', () => {
    challenge.next();
    mode.value = challenge.operation;
    freshChallenge();
    cards[0].focus();
  });
  button(root, '[data-set-reveal]', () => {
    if (!challenge.reveal()) return;
    const marks = new Map(matchingIds(objects, mode.value).map((id) => [id, 'right']));
    challenge.selected.forEach((id) => { if (!marks.has(id)) marks.set(id, 'outside'); });
    render(marks, text.answer);
    next.focus();
  });
  button(root, '[data-set-check]', () => {
    const checked = challenge.check();
    if (checked.result === 'correct') {
      render(new Map([...challenge.selected].map((id) => [id, 'right'])), text.right);
      next.focus();
      return;
    }
    if (!['wrong', 'limit'].includes(checked.result)) return;
    const marks = new Map([...challenge.selected].map((id) => [id, checked.outside.includes(id) ? 'outside' : 'right']));
    checked.outside.forEach((id) => {
      const node = cards.find((card) => card.dataset.token === id);
      const mistakes = Number(node.dataset.mistakes || 0) + 1;
      node.dataset.mistakes = String(mistakes);
      node.style.opacity = String(Math.max(.12, 1 - mistakes * .17));
    });
    const feedback = checked.result === 'limit'
      ? text.limit
      : text.wrong(checked.missing.length, checked.outside.length, checked.attempts);
    render(marks, feedback);
  });
  shuffleCards();
  render();
}
function wireStory(root, text) {
  const scenes = all(root, '[data-story-scene]');
  const progress = query(root, '[data-story-progress]');
  let index = 0;
  function render() {
    scenes.forEach((scene, i) => { scene.hidden = i !== index; });
    back.disabled = index === 0;
    next.disabled = index === scenes.length - 1;
    say(progress, text.progress(index + 1, scenes.length));
  }
  const back = button(root, '[data-story-back]', () => {
    if (index > 0) index -= 1;
    render();
    if (back.disabled) next.focus();
  });
  const next = button(root, '[data-story-next]', () => {
    if (index < scenes.length - 1) index += 1;
    render();
    if (next.disabled) replay.focus();
  });
  const replay = button(root, '[data-story-replay]', () => { index = 0; render(); next.focus(); });
  render();
}

export function startWorkshop(root) {
  const text = root.dataset.language === 'en' ? en : de;
  Object.entries({circuit: wireCircuit, apple: wireApple, gate: wireGate, sets: wireSets, story: wireStory})
    .forEach(([unit, wire]) => wire(query(root, `[data-logic-unit="${unit}"]`), text[unit]));
}
if (typeof document !== 'undefined') {
  const root = document.querySelector('[data-kids-logic]');
  if (root) startWorkshop(root);
}
