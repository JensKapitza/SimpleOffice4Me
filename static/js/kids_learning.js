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
    english: text('Englisch', 'English'), world: text('Natur & Welt', 'Nature & world'),
    search: text('Suchbild & Buchstaben', 'Search & letters')
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
      item.promptLang = english ? 'en-US' : 'de-DE';
      item.speak ||= item.a;
    });
  });
  const safetyCards = [
    {prompt:text('Die Ampel ist rot. Was machst du?','The light is red. What do you do?'), image:'🚦🔴', choices:[['🛑','Warten',false],['🏃','Loslaufen',true]]},
    {prompt:text('Du fährst Fahrrad. Was schützt deinen Kopf?','You ride a bicycle. What protects your head?'), image:'🚲', choices:[['🪖','Helm',false],['🎩','Hut',true]]},
    {prompt:text('Du gehst über die Straße. Was ist sicher?','You cross the road. What is safe?'), image:'🚸', choices:[['👀','Schauen und warten',false],['📱','Auf das Handy schauen',true]]}
  ];
  const arrowCards = [['⬆️','oben','up'],['➡️','rechts','right'],['⬇️','unten','down'],['⬅️','links','left']].map(([emoji,de,en])=>({emoji,name:text(de,en)}));
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

  const setup = root.querySelector('[data-setup]');
  const game = root.querySelector('[data-game]');
  const searchModes = root.querySelector('[data-search-modes]');
  const searchGame = root.querySelector('[data-search-game]');
  const searchScene = root.querySelector('[data-search-scene]');
  const searchPrompt = root.querySelector('[data-search-prompt]');
  const searchNext = root.querySelector('[data-search-next]');
  const letterPlay = root.querySelector('[data-letter-play]');
  const letterChoices = root.querySelector('[data-letter-choices]');
  const result = root.querySelector('[data-result]');
  const questionNode = root.querySelector('[data-question]');
  const choicesNode = root.querySelector('[data-choices]');
  const progress = root.querySelector('[data-progress]');
  const speakButton = root.querySelector('[data-speak]');
  let selectedLevel = 'preschool';
  let selectedSubject = 'math';
  let selectedSearchMode = 'objects';
  let questions = [];
  let index = 0;
  let score = 0;
  let searchTargets = [];
  let searchIndex = 0;
  let searchObjectsInScene = [];
  let activeWordCard = null;
  let searchReadyForNext = false;
  let wrongAttempts = 0;
  const maxWrongAttempts = 5;

  function setQuizFeedback(kind, message) {
    root.querySelector('[data-feedback-status-icon]').textContent = kind === 'wrong' ? '😟 ' : kind === 'right' ? '🌼😊 ' : '';
    root.querySelector('[data-feedback-status-text]').textContent = message;
    const icon = root.querySelector('[data-feedback-icon]');
    icon.textContent = kind === 'wrong' ? '😟' : kind === 'right' ? '🌼😊' : '';
    icon.hidden = !kind;
  }
  function setSearchFeedback(kind, message) {
    root.querySelector('[data-search-status-icon]').textContent = kind === 'wrong' ? '😟 ' : kind === 'right' ? '🌼😊 ' : '';
    root.querySelector('[data-search-status-text]').textContent = message;
    const icon = root.querySelector('[data-search-icon]');
    icon.textContent = kind === 'wrong' ? '😟' : kind === 'right' ? '🌼😊' : '';
    icon.hidden = !kind;
  }
  function fadeWrongChoice(button) {
    button.classList.add('is-wrong');
    const mistakes = Number(button.dataset.mistakes || 0) + 1;
    button.dataset.mistakes = String(mistakes);
    button.style.opacity = String(Math.max(.12, 1 - mistakes * .17));
  }
  function revealQuizAnswer(item) {
    choicesNode.querySelectorAll('button').forEach(option => {
      option.disabled = true;
      if (option.dataset.answer === String(item.a)) option.classList.add('is-correct');
      else option.hidden = true;
    });
    const answer = item.choiceImages?.[item.a] || item.a;
    setQuizFeedback('wrong', text(`Die Lösung ist: ${answer}`, `The answer is: ${answer}`));
    root.querySelector('[data-next]').hidden = false;
    root.querySelector('[data-next]').focus();
  }

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
    searchModes.hidden = true;
    searchGame.hidden = true;
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
  function showSearchModes() {
    selectedSubject = 'search';
    setup.hidden = true;
    game.hidden = true;
    result.hidden = true;
    searchGame.hidden = true;
    searchModes.hidden = false;
    root.querySelector('[data-search-mode="objects"]').focus();
  }
  function startSearch(mode) {
    selectedSubject = 'search';
    selectedSearchMode = mode;
    searchIndex = 0;
    score = 0;
    searchReadyForNext = false;
    setup.hidden = true;
    searchModes.hidden = true;
    game.hidden = true;
    result.hidden = true;
    searchGame.hidden = false;
    root.querySelector('[data-search-level]').textContent = levels[selectedLevel];
    if (mode === 'objects') {
      const targets = shuffle(searchObjects[selectedLevel].map(([de, en, emoji]) => ({name:text(de, en), emoji})));
      searchTargets = targets.slice(0, 3);
      const targetEmojis = new Set(searchTargets.map(item => item.emoji));
      const distractors = Object.values(searchObjects).flat()
        .filter(([, , emoji]) => !targetEmojis.has(emoji))
        .map(([de, en, emoji]) => ({name:text(de, en), emoji}));
      searchObjectsInScene = shuffle([...searchTargets, ...shuffle(distractors).slice(0, 9)]);
    } else if (mode === 'letters') {
      searchTargets = shuffle(wordCards[selectedLevel]).slice(0, 3);
    } else if (mode === 'directions') {
      searchTargets = shuffle(arrowCards).slice(0, 3);
    } else {
      searchTargets = safetyCards;
    }
    showSearchRound();
  }
  function showSearchRound() {
    searchReadyForNext = false;
    searchNext.hidden = true;
    wrongAttempts = 0;
    setSearchFeedback('', '');
    root.querySelector('[data-search-speak]').setAttribute('aria-label', text('Aufgabe anhören', 'Listen to the question'));
    root.querySelector('[data-search-progress]').textContent = text(`Runde ${searchIndex + 1} von 3`, `Round ${searchIndex + 1} of 3`);
    if (selectedSearchMode === 'objects' || selectedSearchMode === 'directions') {
      const target = searchTargets[searchIndex];
      const spokenPrompt = selectedSearchMode === 'directions'
        ? text(`Finde den Pfeil, der nach ${target.name} zeigt.`, `Find the arrow pointing ${target.name}.`)
        : text(`Finde ${target.name}.`, `Find ${target.name}.`);
      const early = ['preschool','1','2'].includes(selectedLevel);
      searchPrompt.textContent = early || selectedSearchMode === 'directions' ? '' : text(`Finde: ${target.name}`, `Find: ${target.name}`);
      searchPrompt.setAttribute('aria-label', spokenPrompt);
      root.querySelector('[data-search-speak]').hidden = false;
      root.querySelector('[data-search-speak]').onclick = () => speak(spokenPrompt, english ? 'en-US' : 'de-DE');
      if (early || selectedSearchMode === 'directions') speak(spokenPrompt, english ? 'en-US' : 'de-DE');
      searchPrompt.focus();
      const targetImage = root.querySelector('[data-search-target-image]');
      targetImage.textContent = target.emoji;
      targetImage.hidden = false;
      letterPlay.hidden = true;
      searchScene.hidden = false;
      searchScene.replaceChildren();
      const sceneItems = selectedSearchMode === 'directions'
        ? shuffle([target, ...shuffle(arrowCards.filter(card => card.emoji !== target.emoji)).slice(0, 11)])
        : searchObjectsInScene;
      sceneItems.forEach((item, itemIndex) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'kids-search-object';
        button.dataset.objectIndex = String(itemIndex);
        button.setAttribute('aria-label', item.name);
        const icon = document.createElement('span');
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = item.emoji;
        button.append(icon);
        button.addEventListener('click', () => findObject(button, item, target, sceneItems));
        searchScene.append(button);
      });
    } else if (selectedSearchMode === 'letters') {
      const card = searchTargets[searchIndex];
      activeWordCard = card;
      const prompt = text('Welcher Buchstabe fehlt in dem Wort?', 'Which letter is missing from the German word?');
      searchPrompt.textContent = ['preschool','1','2'].includes(selectedLevel) ? '' : prompt;
      searchPrompt.setAttribute('aria-label', prompt);
      root.querySelector('[data-search-speak]').hidden = false;
      root.querySelector('[data-search-speak]').onclick = () => speak(prompt, english ? 'en-US' : 'de-DE');
      if (['preschool','1','2'].includes(selectedLevel)) speak(prompt, english ? 'en-US' : 'de-DE');
      searchPrompt.focus();
      root.querySelector('[data-search-target-image]').hidden = true;
      searchScene.hidden = true;
      letterPlay.hidden = false;
      root.querySelector('[data-letter-image]').textContent = card.image;
      root.querySelector('[data-letter-word]').textContent = card.word.toLocaleUpperCase().split('').map((letter, position) => position === card.missing ? '＿' : letter).join(' ');
      letterChoices.replaceChildren();
      const answer = card.word[card.missing].toLocaleLowerCase();
      const choices = shuffle([answer, ...shuffle('aeiourstlmnp'.split('').filter(letter => letter !== answer)).slice(0, 3)]);
      choices.forEach(letter => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'kids-learning-choice';
        button.textContent = letter.toLocaleUpperCase();
        button.addEventListener('click', () => answerLetter(letter, button));
        letterChoices.append(button);
      });
    } else {
      const card = searchTargets[searchIndex];
      const targetImage = root.querySelector('[data-search-target-image]');
      const prompt = card.prompt;
      searchPrompt.textContent = ['preschool','1','2'].includes(selectedLevel) ? '' : prompt;
      searchPrompt.setAttribute('aria-label', prompt);
      targetImage.textContent = card.image;
      targetImage.hidden = false;
      letterPlay.hidden = true;
      searchScene.hidden = false;
      searchScene.replaceChildren();
      root.querySelector('[data-search-speak]').hidden = false;
      root.querySelector('[data-search-speak]').onclick = () => speak(prompt, english ? 'en-US' : 'de-DE');
      speak(prompt, english ? 'en-US' : 'de-DE');
      card.choices.forEach(([emoji, label, unsafe]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'kids-search-object kids-safety-choice';
        button.dataset.unsafe = String(unsafe);
        button.setAttribute('aria-label', label);
        button.textContent = emoji;
        button.addEventListener('click', () => answerSafety(button));
        searchScene.append(button);
      });
    }
  }
  function findObject(button, item, target, items = searchObjectsInScene) {
    if (searchReadyForNext || button.disabled) return;
    if (item.emoji === target.emoji) {
      button.classList.add('is-found');
      button.disabled = true;
      score += 1;
      setSearchFeedback('right', text('Gefunden! Gut gemacht!', 'Found it! Well done!'));
      searchReadyForNext = true;
      searchNext.hidden = false;
      searchNext.focus();
    } else {
      button.classList.add('is-missed');
      wrongAttempts += 1;
      button.dataset.mistakes = String(Number(button.dataset.mistakes || 0) + 1);
      button.style.opacity = String(Math.max(.12, 1 - Number(button.dataset.mistakes) * .2));
      setSearchFeedback('wrong', wrongAttempts >= maxWrongAttempts
        ? text('Schau auf das Bild: Das ist die Lösung.', 'Look at the picture: this is the answer.')
        : text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}. Suche noch einmal.`, `Try ${wrongAttempts} of ${maxWrongAttempts}. Look again.`));
      if (wrongAttempts >= maxWrongAttempts) {
        searchScene.querySelectorAll('button').forEach((option, position) => {
          option.disabled = true;
          if (items[position].emoji !== target.emoji) option.hidden = true;
          else option.classList.add('is-found');
        });
        searchReadyForNext = true;
        searchNext.hidden = false;
        searchNext.focus();
      } else window.setTimeout(() => button.classList.remove('is-missed'), 500);
    }
  }
  function answerSafety(button) {
    if (searchReadyForNext) return;
    if (button.dataset.unsafe === 'false') {
      score += 1;
      button.classList.add('is-found');
      searchScene.querySelectorAll('button').forEach(option => { option.disabled = true; });
      setSearchFeedback('right', text('Richtig und sicher!', 'That is safe!'));
      searchReadyForNext = true;
      searchNext.hidden = false;
      searchNext.focus();
      return;
    }
    wrongAttempts += 1;
    fadeWrongChoice(button);
    setSearchFeedback('wrong', wrongAttempts >= maxWrongAttempts
      ? text('Schau: Das ist die sichere Wahl.', 'Look: this is the safe choice.')
      : text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}.`, `Try ${wrongAttempts} of ${maxWrongAttempts}.`));
    if (wrongAttempts >= maxWrongAttempts) {
      searchScene.querySelectorAll('button').forEach(option => {
        option.disabled = true;
        if (option.dataset.unsafe === 'false') option.classList.add('is-found');
        else option.hidden = true;
      });
      searchReadyForNext = true;
      searchNext.hidden = false;
      searchNext.focus();
    }
  }
  function answerLetter(letter, selectedButton = null) {
    if (!activeWordCard || searchReadyForNext) return false;
    const normalizedLetter = letter.toLocaleLowerCase();
    const button = selectedButton || [...letterChoices.querySelectorAll('button')]
      .find(option => option.textContent.toLocaleLowerCase() === normalizedLetter);
    if (!button || button.disabled) return false;
    const correct = normalizedLetter === activeWordCard.word[activeWordCard.missing].toLocaleLowerCase();
    if (correct) {
      score += 1;
      const displayed = activeWordCard.word.split('');
      displayed.forEach((letter, position) => { displayed[position] = letter.toLocaleUpperCase(); });
      root.querySelector('[data-letter-word]').textContent = displayed.join(' ');
      letterChoices.querySelectorAll('button').forEach(button => {
        button.disabled = true;
        if (button.textContent.toLocaleLowerCase() === normalizedLetter) button.classList.add('is-correct');
      });
      setSearchFeedback('right', text('Richtig! Das Wort heißt: ', 'That’s right! The word is: ') + activeWordCard.word.toLocaleUpperCase());
      root.querySelector('[data-search-speak]').setAttribute('aria-label', text('Wort noch einmal anhören', 'Listen to the word again'));
      speak(activeWordCard.word, 'de-DE');
      searchReadyForNext = true;
      searchNext.hidden = false;
      searchNext.focus();
    } else {
      wrongAttempts += 1;
      fadeWrongChoice(button);
      setSearchFeedback('wrong', wrongAttempts >= maxWrongAttempts
        ? text(`Die Lösung ist ${activeWordCard.word[activeWordCard.missing].toLocaleUpperCase()}.`, `The missing letter is ${activeWordCard.word[activeWordCard.missing].toLocaleUpperCase()}.`)
        : text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}. Probier es noch einmal.`, `Try ${wrongAttempts} of ${maxWrongAttempts}. Try again.`));
      letterChoices.querySelector('button:not(:disabled):not(.is-correct)')?.focus();
      if (wrongAttempts >= maxWrongAttempts) {
        const revealed = activeWordCard.word.toLocaleUpperCase();
        root.querySelector('[data-letter-word]').textContent = revealed.split('').join(' ');
        letterChoices.querySelectorAll('button').forEach(option => {
          option.disabled = true;
          if (option.textContent.toLocaleLowerCase() === activeWordCard.word[activeWordCard.missing].toLocaleLowerCase()) option.classList.add('is-correct');
          else option.hidden = true;
        });
        root.querySelector('[data-search-speak]').onclick = () => speak(activeWordCard.word, 'de-DE');
        setSearchFeedback('wrong', text(`Die Lösung ist ${revealed}.`, `The answer is ${revealed}.`));
        searchReadyForNext = true;
        searchNext.hidden = false;
        searchNext.focus();
      }
    }
    return true;
  }
  function finishSearch() {
    searchGame.hidden = true;
    result.hidden = false;
    root.querySelector('[data-result-title]').textContent = text('Toll gesucht! 🎉', 'Great searching! 🎉');
    root.querySelector('[data-result-copy]').textContent = text(`Du hast ${score} von 3 Aufgaben geschafft.`, `You completed ${score} of 3 tasks.`);
    root.querySelector('[data-result-icon]').textContent = score === 3 ? '🌼😊' : '😟';
    root.querySelector('[data-result-title]').focus();
  }
  function showQuestion() {
    const item = questions[index];
    wrongAttempts = 0;
    progress.textContent = text(`Frage ${index + 1} von ${questions.length}`, `Question ${index + 1} of ${questions.length}`);
    questionNode.textContent = item.visual || item.q;
    if (item.visual) questionNode.setAttribute('aria-label', item.q);
    else questionNode.removeAttribute('aria-label');
    questionNode.focus();
    setQuizFeedback('', '');
    root.querySelector('[data-next]').hidden = true;
    speakButton.hidden = !item.speak && !item.audioPrompt;
    speakButton.onclick = () => speak(item.audioPrompt || item.speak, item.promptLang || item.speechLang || 'en-US');
    if (item.audioPrompt) speak(item.audioPrompt, item.promptLang || item.speechLang || 'en-US');
    choicesNode.replaceChildren();
    shuffle(item.c).forEach(choice => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'kids-learning-choice';
      button.dataset.answer = String(choice);
      button.textContent = item.choiceImages?.[choice] || choice;
      if (item.choiceImages?.[choice]) button.setAttribute('aria-label', choice);
      button.addEventListener('click', () => answer(button, choice, item));
      choicesNode.append(button);
    });
  }
  function answer(button, choice, item) {
    if (choicesNode.querySelector('button.is-correct')) return;
    const correct = choice === item.a;
    if (correct) {
      score += 1;
      choicesNode.querySelectorAll('button').forEach(option => {
        option.disabled = true;
        if (option.dataset.answer === String(item.a)) option.classList.add('is-correct');
      });
      setQuizFeedback('right', text('Richtig! Super gemacht!', 'That’s right! Well done!'));
      root.querySelector('[data-next]').hidden = false;
      if (item.speak) {
        speakButton.onclick = () => speak(item.speak, item.speechLang || 'en-US');
        speak(item.speak, item.speechLang || 'en-US');
      }
    } else {
      wrongAttempts += 1;
      fadeWrongChoice(button);
      setQuizFeedback('wrong', wrongAttempts >= maxWrongAttempts
        ? text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}. Jetzt siehst du die Lösung.`, `Try ${wrongAttempts} of ${maxWrongAttempts}. Here is the answer.`)
        : text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}. Probier es noch einmal.`, `Try ${wrongAttempts} of ${maxWrongAttempts}. Try again.`));
      if (wrongAttempts >= maxWrongAttempts) revealQuizAnswer(item);
    }
  }
  function speak(word, language = 'en-US') {
    if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
      const message = text('Die Vorlesestimme ist auf diesem Gerät nicht verfügbar.', 'A read-aloud voice is not available on this device.');
      if (searchGame.hidden) setQuizFeedback('', message);
      else setSearchFeedback('', message);
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(word);
    utterance.lang = language;
    window.speechSynthesis.speak(utterance);
  }
  function showSetup() {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    game.hidden = true;
    searchGame.hidden = true;
    searchModes.hidden = true;
    result.hidden = true;
    setup.hidden = false;
    root.querySelector('#kids-learning-level-heading').focus();
  }

  root.querySelectorAll('[data-level]').forEach(button => button.addEventListener('click', () => {
    selectedLevel = button.dataset.level;
    root.querySelectorAll('[data-level]').forEach(level => level.setAttribute('aria-pressed', String(level === button)));
  }));
  root.querySelectorAll('[data-subject]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.subject === 'search') showSearchModes();
    else start(button.dataset.subject);
  }));
  root.querySelectorAll('[data-search-mode]').forEach(button => button.addEventListener('click', () => startSearch(button.dataset.searchMode)));
  root.querySelector('[data-search-modes-back]').addEventListener('click', showSetup);
  root.querySelector('[data-search-back]').addEventListener('click', showSetup);
  searchNext.addEventListener('click', () => {
    if (!searchReadyForNext) return;
    searchIndex += 1;
    if (searchIndex < 3) showSearchRound();
    else finishSearch();
  });
  searchScene.addEventListener('keydown', event => {
    const movement = {ArrowRight:1, ArrowLeft:-1, ArrowDown:4, ArrowUp:-4}[event.key];
    if (!movement) return;
    const buttons = [...searchScene.querySelectorAll('button')];
    const current = buttons.indexOf(document.activeElement);
    if (current < 0) return;
    event.preventDefault();
    buttons[Math.max(0, Math.min(buttons.length - 1, current + movement))].focus();
  });
  root.addEventListener('keydown', event => {
    const letterGameHasFocus = document.activeElement === searchPrompt || letterPlay.contains(document.activeElement);
    if (searchGame.hidden || selectedSearchMode !== 'letters' || !letterGameHasFocus
        || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.isComposing
        || !/^[a-z]$/i.test(event.key)) return;
    if (answerLetter(event.key)) event.preventDefault();
  });
  root.querySelector('[data-next]').addEventListener('click', () => {
    index += 1;
    if (index < questions.length) showQuestion();
    else {
      game.hidden = true;
      result.hidden = false;
      root.querySelector('[data-result-title]').textContent = text('Toll gespielt! 🎉', 'Great playing! 🎉');
      root.querySelector('[data-result-copy]').textContent = text(`Du hast ${score} von ${questions.length} Fragen richtig beantwortet.`, `You got ${score} out of ${questions.length} questions right.`);
      root.querySelector('[data-result-icon]').textContent = score === questions.length ? '🌼😊' : '😟';
      root.querySelector('[data-result-title]').focus();
    }
  });
  root.querySelector('[data-back]').addEventListener('click', showSetup);
  root.querySelector('[data-result-back]').addEventListener('click', showSetup);
  root.querySelector('[data-replay]').addEventListener('click', () => {
    if (selectedSubject === 'search') startSearch(selectedSearchMode);
    else start(selectedSubject);
  });
})();
