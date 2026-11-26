(() => {
  'use strict';
  const root = document.querySelector('[data-shinobi]');
  if (!root) return;
  const find = (name) => root.querySelector(`[data-shinobi-${name}]`);
  const trail = find('trail'), choices = find('choices'), instruction = find('instruction');
  const start = find('start'), look = find('look'), retry = find('retry'), next = find('next');
  const title = find('title'), heading = find('round-count'), feedback = find('feedback');
  const cards = Array.from(choices.querySelectorAll('[data-shinobi-card]'));
  const ids = new Map(cards.map((card) => [card.dataset.shinobiCard, card]));
  const en = root.dataset.language === 'en';
  const text = (de, eng) => en ? eng : de;
  const rounds = [
    {title: text('Bring Licht zur Brücke', 'Bring a light to the bridge'),
      order: ['lantern', 'bridge'], deck: ['lantern', 'bridge', 'leaf']},
    {title: text('Bring ein Blatt ans Wasser', 'Bring a leaf to the water'),
      order: ['leaf', 'bridge', 'shell'], deck: ['lantern', 'bridge', 'leaf', 'shell']},
    {title: text('Zeig den Rückweg', 'Show the way back'),
      order: ['shell', 'lantern', 'leaf', 'lantern'], deck: ['lantern', 'bridge', 'leaf', 'shell']},
  ];
  let current = 0, matched = 0, mode = 'preview';

  function report(message = '') {
    // A stable live region avoids announcements on every background UI change.
    feedback.textContent = message;
  }

  function showTrail() {
    const order = rounds[current].order;
    trail.replaceChildren(...order.map((id, pos) => {
      const li = document.createElement('li');
      const slot = pos + 1;
      if (mode === 'preview' || pos < matched) {
        const card = ids.get(id);
        li.append(...Array.from(card.children, (node) => node.cloneNode(true)));
        if (pos < matched) {
          li.dataset.slot = 'done';
          const check = document.createElement('b');
          check.className = 'shinobi-mark';
          check.textContent = '✓';
          check.setAttribute('aria-label', text('richtig', 'correct'));
          li.append(check);
        }
      } else {
        li.dataset.slot = pos === matched ? 'current' : 'next';
        li.textContent = String(slot);
        li.setAttribute('aria-label', text(`Schritt ${slot}: offen`, `Step ${slot}: not filled`));
        if (pos === matched) li.setAttribute('aria-current', 'step');
      }
      return li;
    }));
  }

  function visibleCards() {
    return cards.filter((card) => !card.hidden && !card.disabled);
  }

  function render() {
    const round = rounds[current];
    heading.textContent = text(`Weg ${current + 1} von ${rounds.length}`, `Trail ${current + 1} of ${rounds.length}`);
    title.textContent = round.title;
    choices.hidden = mode !== 'play';
    start.hidden = mode !== 'preview';
    look.hidden = mode !== 'play';
    retry.hidden = mode !== 'play';
    next.hidden = mode !== 'complete';
    next.textContent = current + 1 === rounds.length ? text('↺ Nochmal spielen', '↺ Play again') :
      text('➡ Nächster Weg', '➡ Next trail');
    for (const card of cards) {
      card.hidden = !round.deck.includes(card.dataset.shinobiCard);
      card.disabled = choices.hidden || card.hidden;
      card.removeAttribute('data-wrong');
    }
    if (mode === 'preview') instruction.textContent = text('👀 Schau dir den Bilderweg an.', '👀 Look at the picture trail.');
    if (mode === 'play') instruction.textContent = text(
      `👉 Wähle Bild ${matched + 1} von ${round.order.length}.`,
      `👉 Pick picture ${matched + 1} of ${round.order.length}.`
    );
    if (mode === 'complete') instruction.textContent = current + 1 === rounds.length ?
      text('🏁 Mission geschafft!', '🏁 Mission complete!') : text('✓ Der Weg ist geschafft!', '✓ Trail complete!');
    showTrail();
  }

  function preview(reset) {
    if (reset) matched = 0;
    mode = 'preview';
    report();
    render();
    start.focus();
  }

  start.addEventListener('click', () => {
    if (mode !== 'preview' || start.disabled) return;
    mode = 'play';
    report();
    render();
    visibleCards()[0]?.focus();
  });
  look.addEventListener('click', () => { if (mode === 'play') preview(false); });
  retry.addEventListener('click', () => { if (mode === 'play') preview(true); });
  next.addEventListener('click', () => {
    if (mode !== 'complete') return;
    current = (current + 1) % rounds.length;
    preview(true);
  });
  for (const button of cards) {
    button.addEventListener('click', (event) => {
      if (mode !== 'play' || button.disabled || event.detail > 1) return;
      const order = rounds[current].order;
      if (order[matched] !== button.dataset.shinobiCard) {
        for (const card of cards) card.removeAttribute('data-wrong');
        button.dataset.wrong = 'true';
        report(text('↪ Das war noch nicht dran. Probiere ein anderes Bild oder schau nochmal.',
          '↪ Not this step. Try a different picture or look again.'));
        return;
      }
      matched += 1;
      if (matched === order.length) {
        mode = 'complete';
        render();
        report(text('✓ Alle Bilder sind in der richtigen Reihenfolge.', '✓ All pictures are in the right order.'));
        next.focus();
      } else {
        render();
        report(text(`✓ Richtig. ${matched} von ${order.length} Bildern.`,
          `✓ Correct. ${matched} of ${order.length} pictures.`));
      }
    });
  }
  choices.addEventListener('keydown', (event) => {
    if (mode !== 'play' || !cards.includes(event.target)) return;
    if (event.repeat && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      return;
    }
    const advance = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1};
    if (!Object.hasOwn(advance, event.key)) return;
    const buttons = visibleCards();
    const currentIndex = buttons.indexOf(event.target);
    if (currentIndex < 0) return;
    event.preventDefault();
    buttons[(currentIndex + advance[event.key] + buttons.length) % buttons.length]?.focus();
  });
  render();
  start.disabled = false;
})();
