(() => {
  'use strict';

  const root = document.querySelector('[data-electrical-learning]');
  if (!root) return;

  const t = root.dataset.language === 'en';
  root.querySelectorAll('[data-check]').forEach((check) => {
    const feedback = check.querySelector('.electrical-check-feedback');
    const choices = [...check.querySelectorAll('button[data-correct]')];
    for (let i = choices.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [choices[i], choices[j]] = [choices[j], choices[i]];
    }
    choices.forEach((button) => check.append(button));

    choices.forEach((button) => {
      button.addEventListener('click', () => {
        const correct = button.dataset.correct === 'true';
        if (correct) {
          button.classList.add('is-correct');
          feedback.textContent = t ? 'Correct. Good safe choice!' : 'Richtig. Gute und sichere Entscheidung!';
          check.classList.add('is-complete');
          choices.forEach((choice) => { choice.disabled = true; });
          return;
        }

        button.disabled = true;
        button.classList.add('is-wrong');
        const answer = choices.find((choice) => choice.dataset.correct === 'true');
        feedback.textContent = t
          ? `That is not the safe choice. ${answer.textContent.trim()}`
          : `Das ist nicht sicher. ${answer.textContent.trim()}`;
      });
    });
  });
})();
