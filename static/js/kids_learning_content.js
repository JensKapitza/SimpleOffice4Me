(() => {
  window.KidsLearningContent = (text, english, promptSpeechLanguage, splitQuotedSpeech) => {
  const levels = {
    preschool: text('Kita & Vorschule', 'Preschool'),
    1: text('Klasse 1', 'Grade 1'), 2: text('Klasse 2', 'Grade 2'),
    3: text('Klasse 3', 'Grade 3'), 4: text('Klasse 4', 'Grade 4'), 5: text('Klasse 5', 'Grade 5')
  };
  const subjects = {
    math: text('Mathe', 'Math'), german: text('Deutsch', 'German'),
    english: text('Englisch', 'English'), world: text('Natur & Welt', 'Nature & world'),
    search: text('Suchbild & Buchstaben', 'Search & letters'), mixed: text('Gemischte Runde', 'Mixed round')
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
  const visualEarlyGames = {
    math: [
      {q:text('Wie viele Äpfel siehst du?','How many apples can you see?'), visual:'🍎🍎🍎', a:'3', c:['2','3','4'], audioPrompt:text('Wie viele Äpfel siehst du?','How many apples can you see?'), speechLang:english?'en-US':'de-DE'},
      {q:text('Wie viele Bälle siehst du?','How many balls can you see?'), visual:'⚽⚽⚽⚽', a:'4', c:['3','4','5'], audioPrompt:text('Wie viele Bälle siehst du?','How many balls can you see?'), speechLang:english?'en-US':'de-DE'},
      {q:text('Wie viele Sterne siehst du?','How many stars can you see?'), visual:'⭐ ⭐ ⭐ ⭐ ⭐', a:'5', c:['4','5','6'], audioPrompt:text('Wie viele Sterne siehst du?','How many stars can you see?'), speechLang:english?'en-US':'de-DE'}
    ],
    german: [
      {q:text('Welcher Laut beginnt Apfel?','Which sound starts Apfel (apple)?'), visual:'🍎', a:'A', c:['A','B','M'], audioPrompt:text('Welcher Laut beginnt Apfel?','Which sound starts Apfel (apple)?'), speak:'Apfel', speechLang:'de-DE'},
      {q:text('Welcher Laut beginnt Ball?','Which sound starts Ball (ball)?'), visual:'⚽', a:'B', c:['B','A','S'], audioPrompt:text('Welcher Laut beginnt Ball?','Which sound starts Ball (ball)?'), speak:'Ball', speechLang:'de-DE'},
      {q:text('Welcher Laut beginnt Maus?','Which sound starts Maus (mouse)?'), visual:'🐭', a:'M', c:['M','H','K'], audioPrompt:text('Welcher Laut beginnt Maus?','Which sound starts Maus (mouse)?'), speak:'Maus', speechLang:'de-DE'}
    ],
    english: [
      {q:text('Finde die Katze.','Find the cat.'), visual:'🔊', a:'Katze', c:['Katze','Hund','Ente'], choiceImages:{Katze:'🐱',Hund:'🐶',Ente:'🦆'}, audioPrompt:'Find the cat.', speak:'cat', speechLang:'en-US'},
      {q:text('Finde den Ball.','Find the ball.'), visual:'🔊', a:'Ball', c:['Ball','Sonne','Fisch'], choiceImages:{Ball:'⚽',Sonne:'☀️',Fisch:'🐟'}, audioPrompt:'Find the ball.', speak:'ball', speechLang:'en-US'},
      {q:text('Finde den Apfel.','Find the apple.'), visual:'🔊', a:'Apfel', c:['Apfel','Haus','Blume'], choiceImages:{Apfel:'🍎',Haus:'🏠',Blume:'🌼'}, audioPrompt:'Find the apple.', speak:'apple', speechLang:'en-US'}
    ],
    world: [
      {q:text('Welches Tier lebt im Wasser?','Which animal lives in water?'), visual:'🌊', a:'Fisch', c:['Fisch','Hund','Vogel'], choiceImages:{Fisch:'🐟',Hund:'🐶',Vogel:'🐦'}, audioPrompt:text('Welches Tier lebt im Wasser?','Which animal lives in water?'), speechLang:english?'en-US':'de-DE'},
      {q:text('Was wächst auf einer Wiese?','What grows in a meadow?'), visual:'🌱', a:'Blume', c:['Blume','Auto','Schuh'], choiceImages:{Blume:'🌼',Auto:'🚗',Schuh:'👟'}, audioPrompt:text('Was wächst auf einer Wiese?','What grows in a meadow?'), speechLang:english?'en-US':'de-DE'},
      {q:text('Was tragen wir auf dem Kopf?','What do we wear on our head?'), visual:'🧢', a:'Mütze', c:['Mütze','Socke','Tasse'], choiceImages:{Mütze:'🧢',Socke:'🧦',Tasse:'☕'}, audioPrompt:text('Was tragen wir auf dem Kopf?','What do we wear on our head?'), speechLang:english?'en-US':'de-DE'}
    ]
  };
  ['preschool','1','2'].forEach(level => { Object.entries(visualEarlyGames).forEach(([subject, items]) => { bank[level][subject] = items; }); });
  Object.values(bank).forEach(levelBank => {
    levelBank.german.forEach(item => {
      item.audioPrompt ||= item.q;
      item.speechLang = 'de-DE';
      item.promptLang = promptSpeechLanguage;
      item.audioParts = item.visual && item.speak
        ? [
          {text:text('Welcher Laut beginnt das Wort?','Which sound starts the German word?'), language:promptSpeechLanguage},
          {text:item.speak, language:'de-DE'}
        ]
        : splitQuotedSpeech(item.audioPrompt, item.promptLang, 'de-DE');
      item.speak ||= item.a;
    });
    levelBank.english.forEach(item => {
      item.audioPrompt ||= item.q;
      item.speechLang = 'en-US';
      item.promptLang = promptSpeechLanguage;
      item.audioParts = splitQuotedSpeech(item.audioPrompt, item.promptLang, 'en-US');
    });
  });
  const safetyCards = [
    {prompt:text('Die Ampel ist rot. Was machst du?','The light is red. What do you do?'), image:'🚦🔴', choices:[['🛑','Warten',false],['🏃','Loslaufen',true]]},
    {prompt:text('Du fährst Fahrrad. Was schützt deinen Kopf?','You ride a bicycle. What protects your head?'), image:'🚲', choices:[['🪖','Helm',false],['🎩','Hut',true]]},
    {prompt:text('Du gehst über die Straße. Was ist sicher?','You cross the road. What is safe?'), image:'🚸', choices:[['👀','Schauen und warten',false],['📱','Auf das Handy schauen',true]]}
  ];
  const arrowCards = [['⬆️','oben','up'],['➡️','rechts','right'],['⬇️','unten','down'],['⬅️','links','left']].map(([emoji,de,en])=>({emoji,name:text(de,en)}));
  const mazeFriends = [
    {character:'🐶',goal:'🦴',de:'Knochen',en:'bone',lettersDe:'HUND',lettersEn:'DOG'},
    {character:'🐝',goal:'🍯',de:'Bienenstock',en:'beehive',lettersDe:'BIENE',lettersEn:'BEE'},
    {character:'🐱',goal:'🛏️',de:'Bett',en:'bed',lettersDe:'KATZE',lettersEn:'CAT'},
    {character:'🐰',goal:'🥕',de:'Möhre',en:'carrot',lettersDe:'HASE',lettersEn:'BUNNY'},
    {character:'🐟',goal:'🫧',de:'Blase',en:'bubble',lettersDe:'FISCH',lettersEn:'FISH'},
    {character:'🦆',goal:'🏠',de:'Teich',en:'pond',lettersDe:'ENTE',lettersEn:'DUCK'},
    {character:'🐭',goal:'🧀',de:'Käse',en:'cheese',lettersDe:'MAUS',lettersEn:'MOUSE'},
    {character:'🐻',goal:'🍯',de:'Honig',en:'honey',lettersDe:'BAER',lettersEn:'BEAR'}
  ];
  function generateMaze(friend) {
    const word = (english ? friend.lettersEn : friend.lettersDe).toUpperCase();
    const edgeCount = word.length + 1 + (word.length % 2 === 0 ? 1 : 0);
    const start = {col:0,row:1};
    const goal = {col:3,row:2};
    const shuffle = values => values.sort(() => Math.random() - .5);
    const directionFor = (from, to) => to.col > from.col ? 'right' : to.col < from.col ? 'left' : to.row > from.row ? 'down' : 'up';
    let path = null;
    const findPath = (node, route, occupied) => {
      if (path) return;
      const remaining = edgeCount - (route.length - 1);
      const distance = Math.abs(node.col - goal.col) + Math.abs(node.row - goal.row);
      if (distance > remaining || ((remaining - distance) % 2)) return;
      if (node.col === goal.col && node.row === goal.row) {
        if (remaining === 0) path = route;
        return;
      }
      if (remaining <= 0) return;
      const neighbors = shuffle([
        {col:node.col + 1,row:node.row},{col:node.col - 1,row:node.row},
        {col:node.col,row:node.row + 1},{col:node.col,row:node.row - 1},
      ].filter(next => next.col >= 0 && next.col < 4 && next.row >= 0 && next.row < 4
        && !occupied.has(`${next.col},${next.row}`)));
      neighbors.forEach(next => {
        if (!path) {
          const nextOccupied = new Set(occupied);
          nextOccupied.add(`${next.col},${next.row}`);
          findPath(next, [...route, next], nextOccupied);
        }
      });
    };
    findPath(start, [start], new Set([`${start.col},${start.row}`]));
    if (!path) path = edgeCount === 6
      ? [start,{col:0,row:0},{col:1,row:0},{col:2,row:0},{col:3,row:0},{col:3,row:1},goal]
      : [start,{col:1,row:1},{col:1,row:2},{col:2,row:2},goal];
    const nodes = {};
    path.forEach((point,index) => {
      const id = index === 0 ? 'start' : index === path.length - 1 ? 'goal' : `letter-${index}`;
      nodes[id] = {...point, exits:{}, letter:index > 0 && index <= word.length ? word[index - 1] : '', letterIndex:index > 0 && index <= word.length ? index - 1 : -1};
    });
    const connect = (fromId, toId) => {
      const from = nodes[fromId];
      const to = nodes[toId];
      from.exits[directionFor(from,to)] = toId;
      to.exits[directionFor(to,from)] = fromId;
    };
    for (let index = 0; index < path.length - 1; index += 1) {
      connect(index === 0 ? 'start' : `letter-${index}`, index + 1 === path.length - 1 ? 'goal' : `letter-${index + 1}`);
    }
    const occupied = new Set(path.map(point => `${point.col},${point.row}`));
    let branchCount = 0;
    path.slice(1, -1).forEach((point,index) => {
      if (Math.random() > .62 || branchCount >= 5) return;
      const options = shuffle([
        {col:point.col + 1,row:point.row},{col:point.col - 1,row:point.row},
        {col:point.col,row:point.row + 1},{col:point.col,row:point.row - 1},
      ].filter(next => next.col >= 0 && next.col < 4 && next.row >= 0 && next.row < 4
        && !occupied.has(`${next.col},${next.row}`)));
      const branch = options[0];
      if (!branch) return;
      const parentId = `letter-${index + 1}`;
      const branchId = `branch-${branchCount++}`;
      nodes[branchId] = {...branch, exits:{}, letter:'', letterIndex:-1};
      occupied.add(`${branch.col},${branch.row}`);
      connect(parentId,branchId);
    });
    return {nodes,word};
  }
  const searchObjects = {
    preschool: [
      ['Apfel','Apple','🍎'],['Ball','Ball','⚽'],['Katze','Cat','🐱'],['Sonne','Sun','☀️'],['Fisch','Fish','🐟'],['Haus','House','🏠'],
      ['Buch','Book','📘'],['Blume','Flower','🌼'],['Auto','Car','🚗'],['Banane','Banana','🍌'],['Hund','Dog','🐶'],['Stern','Star','⭐']
    ],
    1: [
      ['Apfel','Apple','🍎'],['Ball','Ball','⚽'],['Katze','Cat','🐱'],['Sonne','Sun','☀️'],['Fisch','Fish','🐟'],['Haus','House','🏠'],
      ['Buch','Book','📘'],['Blume','Flower','🌼'],['Auto','Car','🚗'],['Banane','Banana','🍌'],['Hund','Dog','🐶'],['Stern','Star','⭐']
    ],
    2: [
      ['Schule','School','🏫'],['Regen','Rain','🌧️'],['Pferd','Horse','🐴'],['Brille','Glasses','👓'],['Tasse','Cup','☕'],['Mond','Moon','🌙'],
      ['Schiff','Ship','🚢'],['Baum','Tree','🌳'],['Kuchen','Cake','🍰'],['Zug','Train','🚂'],['Ente','Duck','🦆'],['Uhr','Clock','🕒']
    ],
    3: [
      ['Regenbogen','Rainbow','🌈'],['Dinosaurier','Dinosaur','🦕'],['Schmetterling','Butterfly','🦋'],['Rucksack','Backpack','🎒'],['Fußball','Football','⚽'],['Erdbeere','Strawberry','🍓'],
      ['Gitarre','Guitar','🎸'],['Leuchtturm','Lighthouse','🗼'],['Kamera','Camera','📷'],['Schildkröte','Turtle','🐢'],['Zahnbürste','Toothbrush','🪥'],['Wolke','Cloud','☁️']
    ],
    4: [
      ['Fernglas','Binoculars','🔭'],['Ananas','Pineapple','🍍'],['Feuerwehr','Fire engine','🚒'],['Schneemann','Snowman','⛄'],['Pflanze','Plant','🪴'],['Koffer','Suitcase','🧳'],
      ['Mikrofon','Microphone','🎤'],['Leiter','Ladder','🪜'],['Schlüssel','Key','🔑'],['Pinguin','Penguin','🐧'],['Kaktus','Cactus','🌵'],['Kronleuchter','Chandelier','💡']
    ],
    5: [
      ['Kompass','Compass','🧭'],['Teleskop','Telescope','🔭'],['Fahrrad','Bicycle','🚲'],['Berg','Mountain','⛰️'],['Papagei','Parrot','🦜'],['Muschel','Shell','🐚'],
      ['Schlagzeug','Drum kit','🥁'],['Schloss','Castle','🏰'],['Fallschirm','Parachute','🪂'],['Rakete','Rocket','🚀'],['Kopfhörer','Headphones','🎧'],['Regenschirm','Umbrella','☂️']
    ]
  };
  const wordCards = {
    preschool: [
      {word:'Katze',image:'🐱',missing:1}, {word:'Ball',image:'⚽',missing:0}, {word:'Hund',image:'🐶',missing:1}
    ],
    1: [
      {word:'Apfel',image:'🍎',missing:1}, {word:'Sonne',image:'☀️',missing:1}, {word:'Blume',image:'🌼',missing:2}
    ],
    2: [
      {word:'Schule',image:'🏫',missing:3}, {word:'Pferd',image:'🐴',missing:2}, {word:'Regen',image:'🌧️',missing:1}
    ],
    3: [
      {word:'Regenbogen',image:'🌈',missing:5}, {word:'Schildkröte',image:'🐢',missing:4}, {word:'Rucksack',image:'🎒',missing:3}
    ],
    4: [
      {word:'Fernglas',image:'🔭',missing:4}, {word:'Schneemann',image:'⛄',missing:5}, {word:'Erdbeere',image:'🍓',missing:3}
    ],
    5: [
      {word:'Kompass',image:'🧭',missing:3}, {word:'Teleskop',image:'🔭',missing:5}, {word:'Fahrrad',image:'🚲',missing:4}
    ]
  };
    return {levels, subjects, bank, safetyCards, arrowCards, mazeFriends, generateMaze, searchObjects, wordCards};
  };
})();
