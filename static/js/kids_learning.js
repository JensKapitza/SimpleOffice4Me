(() => {
  const root = document.querySelector('[data-kids-learning]');
  if (!root) return;

  const english = root.dataset.language === 'en';
  const text = (de, en) => english ? en : de;
  const levels = {
    preschool: text('Kita & Vorschule', 'Preschool'),
    1: text('Klasse 1', 'Grade 1'), 2: text('Klasse 2', 'Grade 2'),
    3: text('Klasse 3', 'Grade 3'), 4: text('Klasse 4', 'Grade 4'), 5: text('Klasse 5', 'Grade 5')
  };
  const subjects = {
    math: text('Mathe', 'Math'), german: text('Deutsch', 'German'),
    english: text('Englisch', 'English'), world: text('Natur & Welt', 'Nature & world')
  };
  const bank = {
    preschool: {
      german: [
        { q: text('Welcher Buchstabe beginnt „Ball“?', 'Which letter starts “Ball” in German?'), a: 'B', c: ['A', 'B', 'D'] },
        { q: text('Welches Wort reimt sich auf „Haus“?', 'Which German word rhymes with “Haus”?'), a: 'Maus', c: ['Maus', 'Ball', 'Bett'] },
        { q: text('Wie viele Silben hat „Mama“?', 'How many syllables does “Mama” have?'), a: '2', c: ['1', '2', '3'] }
      ],
      english: [
        { q: text('Was heißt „cat“ auf Deutsch?', 'What does “cat” mean in German?'), a: 'Katze', c: ['Hund', 'Katze', 'Maus'], speak: 'cat' },
        { q: text('Was heißt „red“ auf Deutsch?', 'What does “red” mean in German?'), a: 'rot', c: ['blau', 'grün', 'rot'], speak: 'red' },
        { q: text('Was heißt „sun“ auf Deutsch?', 'What does “sun” mean in German?'), a: 'Sonne', c: ['Mond', 'Sonne', 'Stern'], speak: 'sun' }
      ],
      world: [
        { q: text('Welches Tier sagt „miau“?', 'Which animal says “meow”?'), a: text('Katze', 'Cat'), c: [text('Katze', 'Cat'), text('Kuh', 'Cow'), text('Ente', 'Duck')] },
        { q: text('Was brauchen Pflanzen zum Wachsen?', 'What do plants need to grow?'), a: text('Wasser und Licht', 'Water and light'), c: [text('Schuhe', 'Shoes'), text('Wasser und Licht', 'Water and light'), text('Spielzeug', 'Toys')] },
        { q: text('Welches Tier kann fliegen?', 'Which animal can fly?'), a: text('Vogel', 'Bird'), c: [text('Fisch', 'Fish'), text('Vogel', 'Bird'), text('Hund', 'Dog')] }
      ]
    }
  };
  const gradeQuestions = {
    german: [
      [
        { q: text('Welches Wort ist ein Nomen?', 'Which word is a noun?'), a: 'Hund', c: ['laufen', 'Hund', 'schnell'] },
        { q: text('Welcher Buchstabe fehlt: K _ tze?', 'Which letter is missing: K _ tze?'), a: 'a', c: ['e', 'a', 'i'] },
        { q: text('Welches Wort beginnt mit „Sch“?', 'Which word starts with “Sch”?'), a: 'Schule', c: ['Tisch', 'Schule', 'Apfel'] }
      ],
      [
        { q: text('Was ist die Mehrzahl von „Kind“?', 'What is the plural of “Kind” (child)?'), a: 'Kinder', c: ['Kinde', 'Kindern', 'Kinder'] },
        { q: text('Welches Wort ist ein Verb?', 'Which word is a verb?'), a: 'springen', c: ['Baum', 'springen', 'gelb'] },
        { q: text('Welches Satzzeichen kommt ans Ende einer Frage?', 'Which punctuation mark ends a question?'), a: '?', c: ['.', '?', '!'] }
      ],
      [
        { q: text('Was ist das Gegenteil von „laut“?', 'What is the opposite of “loud”?'), a: 'leise', c: ['hell', 'leise', 'schnell'] },
        { q: text('Welches Wort ist richtig geschrieben?', 'Which word is spelled correctly?'), a: 'Fahrrad', c: ['Farad', 'Fahrrad', 'Farrad'] },
        { q: text('Welches Wort ist ein Adjektiv?', 'Which word is an adjective?'), a: 'fröhlich', c: ['singen', 'fröhlich', 'Tasche'] }
      ],
      [
        { q: text('Welche Zeitform beschreibt „ich ging“?', 'Which tense is “ich ging” (I went)?'), a: 'Vergangenheit', c: ['Gegenwart', 'Vergangenheit', 'Zukunft'] },
        { q: text('Welches Wort ist ein Pronomen?', 'Which word is a pronoun?'), a: 'wir', c: ['Tisch', 'wir', 'lesen'] },
        { q: text('Was ist die Mehrzahl von „Maus“?', 'What is the plural of “Maus” (mouse)?'), a: 'Mäuse', c: ['Mause', 'Mäuse', 'Mausen'] }
      ],
      [
        { q: text('Welches Wort ist ein Nomen?', 'Which word is a noun?'), a: 'Abenteuer', c: ['erleben', 'Abenteuer', 'spannend'] },
        { q: text('Was ist die Grundform von „lief“?', 'What is the infinitive of “lief” (ran)?'), a: 'laufen', c: ['laufen', 'laufenes', 'gelaufen'] },
        { q: text('Welches Wort ist ein Adverb?', 'Which word is an adverb?'), a: 'gestern', c: ['gestern', 'Freund', 'malen'] }
      ]
    ],
    english: [
      [
        { q: text('Was heißt „dog“ auf Deutsch?', 'What does “dog” mean in German?'), a: 'Hund', c: ['Hund', 'Katze', 'Vogel'], speak: 'dog' },
        { q: text('Was heißt „blue“ auf Deutsch?', 'What does “blue” mean in German?'), a: 'blau', c: ['rot', 'blau', 'gelb'], speak: 'blue' },
        { q: text('Was heißt „book“ auf Deutsch?', 'What does “book” mean in German?'), a: 'Buch', c: ['Buch', 'Haus', 'Baum'], speak: 'book' }
      ],
      [
        { q: text('Was heißt „apple“ auf Deutsch?', 'What does “apple” mean in German?'), a: 'Apfel', c: ['Apfel', 'Birne', 'Brot'], speak: 'apple' },
        { q: text('Was heißt „happy“ auf Deutsch?', 'What does “happy” mean in German?'), a: 'glücklich', c: ['traurig', 'glücklich', 'müde'], speak: 'happy' },
        { q: text('Was heißt „school“ auf Deutsch?', 'What does “school” mean in German?'), a: 'Schule', c: ['Schule', 'Straße', 'Stadt'], speak: 'school' }
      ],
      [
        { q: text('Was heißt „to swim“ auf Deutsch?', 'What does “to swim” mean in German?'), a: 'schwimmen', c: ['schwimmen', 'springen', 'schlafen'], speak: 'to swim' },
        { q: text('Was heißt „green“ auf Deutsch?', 'What does “green” mean in German?'), a: 'grün', c: ['grün', 'grau', 'groß'], speak: 'green' },
        { q: text('Was heißt „friend“ auf Deutsch?', 'What does “friend” mean in German?'), a: 'Freund', c: ['Freund', 'Familie', 'Lehrer'], speak: 'friend' }
      ],
      [
        { q: text('Was heißt „yesterday“ auf Deutsch?', 'What does “yesterday” mean in German?'), a: 'gestern', c: ['morgen', 'gestern', 'heute'], speak: 'yesterday' },
        { q: text('Was heißt „beautiful“ auf Deutsch?', 'What does “beautiful” mean in German?'), a: 'wunderschön', c: ['wunderschön', 'winzig', 'langsam'], speak: 'beautiful' },
        { q: text('Was heißt „because“ auf Deutsch?', 'What does “because” mean in German?'), a: 'weil', c: ['aber', 'weil', 'oder'], speak: 'because' }
      ],
      [
        { q: text('Was heißt „to borrow“ auf Deutsch?', 'What does “to borrow” mean in German?'), a: 'ausleihen', c: ['ausleihen', 'verkaufen', 'verlieren'], speak: 'to borrow' },
        { q: text('Was heißt „carefully“ auf Deutsch?', 'What does “carefully” mean in German?'), a: 'vorsichtig', c: ['vorsichtig', 'plötzlich', 'gemeinsam'], speak: 'carefully' },
        { q: text('Was heißt „journey“ auf Deutsch?', 'What does “journey” mean in German?'), a: 'Reise', c: ['Frage', 'Reise', 'Antwort'], speak: 'journey' }
      ]
    ],
    world: [
      [
        { q: text('Wo lebt ein Fisch?', 'Where does a fish live?'), a: text('Im Wasser', 'In water'), c: [text('Im Wasser', 'In water'), text('Im Baum', 'In a tree'), text('In der Wüste', 'In the desert')] },
        { q: text('Welche Jahreszeit kommt nach dem Winter?', 'Which season comes after winter?'), a: text('Frühling', 'Spring'), c: [text('Herbst', 'Autumn'), text('Frühling', 'Spring'), text('Winter', 'Winter')] },
        { q: text('Womit hören wir?', 'Which body part do we hear with?'), a: text('Ohren', 'Ears'), c: [text('Ohren', 'Ears'), text('Nase', 'Nose'), text('Knie', 'Knees')] }
      ],
      [
        { q: text('Was wird aus einer Raupe?', 'What does a caterpillar become?'), a: text('Schmetterling', 'Butterfly'), c: [text('Frosch', 'Frog'), text('Schmetterling', 'Butterfly'), text('Spinne', 'Spider')] },
        { q: text('Welches Tier ist ein Säugetier?', 'Which animal is a mammal?'), a: text('Delfin', 'Dolphin'), c: [text('Delfin', 'Dolphin'), text('Forelle', 'Trout'), text('Biene', 'Bee')] },
        { q: text('Was zeigt ein Thermometer?', 'What does a thermometer show?'), a: text('Temperatur', 'Temperature'), c: [text('Temperatur', 'Temperature'), text('Uhrzeit', 'Time'), text('Entfernung', 'Distance')] }
      ],
      [
        { q: text('Welcher Planet ist unser Zuhause?', 'Which planet is our home?'), a: text('Erde', 'Earth'), c: ['Mars', text('Erde', 'Earth'), 'Venus'] },
        { q: text('Was brauchen Menschen zum Atmen?', 'What do people need to breathe?'), a: text('Luft', 'Air'), c: [text('Luft', 'Air'), text('Sand', 'Sand'), text('Holz', 'Wood')] },
        { q: text('Welche Himmelsrichtung liegt gegenüber von Norden?', 'Which direction is opposite north?'), a: text('Süden', 'South'), c: [text('Osten', 'East'), text('Westen', 'West'), text('Süden', 'South')] }
      ],
      [
        { q: text('Wie heißt der Stern, der uns am Tag wärmt?', 'What is the star that warms us during the day?'), a: text('Sonne', 'Sun'), c: [text('Sonne', 'Sun'), text('Mond', 'Moon'), text('Polarstern', 'North Star')] },
        { q: text('Welches Organ pumpt Blut durch den Körper?', 'Which organ pumps blood around the body?'), a: text('Herz', 'Heart'), c: [text('Herz', 'Heart'), text('Lunge', 'Lung'), text('Magen', 'Stomach')] },
        { q: text('Was entsteht, wenn Wasser gefriert?', 'What forms when water freezes?'), a: text('Eis', 'Ice'), c: [text('Dampf', 'Steam'), text('Eis', 'Ice'), text('Salz', 'Salt')] }
      ],
      [
        { q: text('Wie nennt man Tiere, die nur Pflanzen fressen?', 'What do you call animals that eat only plants?'), a: text('Pflanzenfresser', 'Herbivores'), c: [text('Raubtiere', 'Predators'), text('Pflanzenfresser', 'Herbivores'), text('Allesfresser', 'Omnivores')] },
        { q: text('Welches Gas nehmen Pflanzen aus der Luft auf?', 'Which gas do plants take in from the air?'), a: text('Kohlenstoffdioxid', 'Carbon dioxide'), c: [text('Sauerstoff', 'Oxygen'), text('Kohlenstoffdioxid', 'Carbon dioxide'), text('Helium', 'Helium')] },
        { q: text('Wie viele Beine hat ein Insekt?', 'How many legs does an insect have?'), a: '6', c: ['4', '6', '8'] }
      ]
    ]
  };
  const extras = [
    [
      { q: text('Wie viele Ecken hat ein Dreieck?', 'How many corners does a triangle have?'), a: '3', c: ['2', '3', '4'] },
      { q: text('Was ist die Hälfte von 10?', 'What is half of 10?'), a: '5', c: ['2', '5', '8'] },
      { q: text('Wie viele Tage hat eine Woche?', 'How many days are in a week?'), a: '7', c: ['5', '6', '7'] }
    ],
    [
      { q: text('Was ist 8 + 7?', 'What is 8 + 7?'), a: '15', c: ['14', '15', '16'] },
      { q: text('Was ist 20 − 6?', 'What is 20 − 6?'), a: '14', c: ['12', '14', '16'] },
      { q: text('Welche Zahl ist gerade?', 'Which number is even?'), a: '18', c: ['13', '17', '18'] }
    ],
    [
      { q: text('Was ist 6 × 4?', 'What is 6 × 4?'), a: '24', c: ['20', '24', '28'] },
      { q: text('Was ist 45 ÷ 5?', 'What is 45 ÷ 5?'), a: '9', c: ['8', '9', '10'] },
      { q: text('Welche Zahl ist ein Vielfaches von 7?', 'Which number is a multiple of 7?'), a: '35', c: ['32', '35', '37'] }
    ],
    [
      { q: text('Was ist 125 + 75?', 'What is 125 + 75?'), a: '200', c: ['190', '200', '210'] },
      { q: text('Was ist 9 × 8?', 'What is 9 × 8?'), a: '72', c: ['63', '72', '81'] },
      { q: text('Wie viele Minuten hat eine Stunde?', 'How many minutes are in an hour?'), a: '60', c: ['30', '60', '100'] }
    ],
      [
        { q: text('Was ist ¾ als Dezimalzahl?', 'What is ¾ as a decimal?'), a: text('0,75', '0.75'), c: [text('0,25', '0.25'), text('0,5', '0.5'), text('0,75', '0.75')] },
      { q: text('Was ist 12 × 12?', 'What is 12 × 12?'), a: '144', c: ['124', '134', '144'] },
      { q: text('Wie viel sind 25 % von 80?', 'What is 25% of 80?'), a: '20', c: ['15', '20', '25'] }
    ]
  ];
  bank.preschool.math = [
    { q: text('Wie viele Äpfel sind hier? 🍎🍎🍎', 'How many apples are here? 🍎🍎🍎'), a: '3', c: ['2', '3', '4'] },
    { q: text('Welche Zahl kommt nach 4?', 'Which number comes after 4?'), a: '5', c: ['3', '5', '7'] },
    { q: text('Was ist 2 + 1?', 'What is 2 + 1?'), a: '3', c: ['2', '3', '4'] }
  ];
  ['1','2','3','4','5'].forEach((grade, index) => {
    bank[grade] = {
      german: gradeQuestions.german[index], english: gradeQuestions.english[index],
      world: gradeQuestions.world[index], math: extras[index]
    };
  });

  const setup = root.querySelector('[data-setup]');
  const game = root.querySelector('[data-game]');
  const result = root.querySelector('[data-result]');
  const questionNode = root.querySelector('[data-question]');
  const choicesNode = root.querySelector('[data-choices]');
  const feedback = root.querySelector('[data-feedback]');
  const progress = root.querySelector('[data-progress]');
  const speakButton = root.querySelector('[data-speak]');
  let selectedLevel = 'preschool';
  let selectedSubject = 'math';
  let questions = [];
  let index = 0;
  let score = 0;

  const shuffle = items => {
    const shuffled = [...items];
    for (let i = shuffled.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
  };
  function start(subject) {
    selectedSubject = subject;
    questions = shuffle(bank[selectedLevel][subject]).slice(0, 3);
    index = 0;
    score = 0;
    setup.hidden = true;
    result.hidden = true;
    game.hidden = false;
    root.querySelector('[data-game-level]').textContent = levels[selectedLevel];
    root.querySelector('[data-game-subject]').textContent = subjects[subject];
    showQuestion();
  }
  function showQuestion() {
    const item = questions[index];
    progress.textContent = text(`Frage ${index + 1} von ${questions.length}`, `Question ${index + 1} of ${questions.length}`);
    questionNode.textContent = item.q;
    questionNode.focus();
    feedback.textContent = '';
    root.querySelector('[data-next]').hidden = true;
    speakButton.hidden = !item.speak;
    choicesNode.replaceChildren();
    shuffle(item.c).forEach(choice => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'kids-learning-choice';
      button.textContent = choice;
      button.addEventListener('click', () => answer(button, choice, item));
      choicesNode.append(button);
    });
  }
  function answer(button, choice, item) {
    if (choicesNode.querySelector('button:disabled')) return;
    const correct = choice === item.a;
    if (correct) score += 1;
    choicesNode.querySelectorAll('button').forEach(option => {
      option.disabled = true;
      if (option.textContent === item.a) option.classList.add('is-correct');
    });
    if (!correct) button.classList.add('is-wrong');
    feedback.textContent = correct
      ? text('Richtig! Super gemacht! 🌟', 'That’s right! Well done! 🌟')
      : text(`Gute Idee! Richtig ist: ${item.a}`, `Good try! The answer is: ${item.a}`);
    root.querySelector('[data-next]').hidden = false;
    if (item.speak) speak(item.speak);
  }
  function speak(word) {
    if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
      feedback.textContent = text('Die Vorlesestimme ist auf diesem Gerät nicht verfügbar.', 'A read-aloud voice is not available on this device.');
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(word);
    utterance.lang = 'en-US';
    window.speechSynthesis.speak(utterance);
  }
  function showSetup() {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    game.hidden = true;
    result.hidden = true;
    setup.hidden = false;
    root.querySelector('#kids-learning-level-heading').focus();
  }

  root.querySelectorAll('[data-level]').forEach(button => button.addEventListener('click', () => {
    selectedLevel = button.dataset.level;
    root.querySelectorAll('[data-level]').forEach(level => level.setAttribute('aria-pressed', String(level === button)));
  }));
  root.querySelectorAll('[data-subject]').forEach(button => button.addEventListener('click', () => start(button.dataset.subject)));
  root.querySelector('[data-next]').addEventListener('click', () => {
    index += 1;
    if (index < questions.length) showQuestion();
    else {
      game.hidden = true;
      result.hidden = false;
      root.querySelector('[data-result-title]').textContent = text('Toll gespielt! 🎉', 'Great playing! 🎉');
      root.querySelector('[data-result-copy]').textContent = text(`Du hast ${score} von ${questions.length} Fragen richtig beantwortet.`, `You got ${score} out of ${questions.length} questions right.`);
      root.querySelector('[data-result-title]').focus();
    }
  });
  root.querySelector('[data-speak]').addEventListener('click', () => {
    const item = questions[index];
    if (item && item.speak) speak(item.speak);
  });
  root.querySelector('[data-back]').addEventListener('click', showSetup);
  root.querySelector('[data-result-back]').addEventListener('click', showSetup);
  root.querySelector('[data-replay]').addEventListener('click', () => start(selectedSubject));
})();
