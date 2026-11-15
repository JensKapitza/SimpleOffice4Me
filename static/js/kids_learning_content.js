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
  const pictureChoiceCard = (de, en, image, correct, incorrect) => ({
    prompt:text(de,en), image,
    choices:[
      [correct[0],text(correct[1],correct[2]),false],
      [incorrect[0],text(incorrect[1],incorrect[2]),true],
    ],
  });
  const safetyCards = [
    pictureChoiceCard('Die Ampel ist rot. Was machst du?','The traffic light is red. What do you do?','🚦🔴',['🛑','Warten','Wait'],['🏃','Loslaufen','Run across']),
    pictureChoiceCard('Die Ampel ist grün. Was machst du vor dem Losgehen?','The traffic light is green. What do you do before crossing?','🚦🟢',['👀','Trotzdem schauen','Look both ways'],['🏃','Einfach rennen','Run without looking']),
    pictureChoiceCard('Am Zebrastreifen hält ein Auto noch nicht. Was machst du?','A car has not stopped at the zebra crossing. What do you do?','🚸🚗',['✋','Warten','Wait'],['🚶','Vor dem Auto gehen','Walk in front of it']),
    pictureChoiceCard('Du siehst ein rotes STOPP-Schild. Was machst du?','You see a red STOP sign. What do you do?','🛑🚲',['✋','Anhalten und schauen','Stop and look'],['➡️','Weiterfahren','Ride on']),
    pictureChoiceCard('Das Schild zeigt Tempo 30. Wie fährt das Auto?','The sign shows a 30 speed limit. How should the car go?','🔴30',['🐢','Langsam','Slowly'],['🏎️','Schnell','Fast']),
    pictureChoiceCard('Du siehst das Dreieck mit Kindern. Was bedeutet es?','You see the triangle sign with children. What does it mean?','⚠️👧🧒',['🐢','Auf Kinder achten','Watch for children'],['🏎️','Schnell vorbeifahren','Drive past quickly']),
    pictureChoiceCard('Ein rundes Schild zeigt ein Fahrrad mit rotem Rand. Was bedeutet es?','A round sign shows a bicycle with a red border. What does it mean?','🚳',['🚶','Hier nicht Rad fahren','Do not ride here'],['🚲','Hier Rad fahren','Ride here']),
    pictureChoiceCard('Du siehst das rote Einfahrt-verboten-Schild. Was machst du?','You see a no-entry sign. What do you do?','⛔',['↩️','Draußen bleiben','Stay out'],['➡️','Hineinfahren','Go in']),
    pictureChoiceCard('Du fährst mit dem Rad. Was gehört auf deinen Kopf?','You are riding a bicycle. What goes on your head?','🚲',['🪖','Fahrradhelm','Bicycle helmet'],['🎩','Partyhut','Party hat']),
    pictureChoiceCard('Du sitzt im Auto. Was hält dich sicher im Sitz?','You are sitting in a car. What keeps you safely in your seat?','🚗',['🔒','Gurt anlegen','Fasten your seat belt'],['🪟','Auf die Fensterbank klettern','Climb onto the window']),
    pictureChoiceCard('Du gehst an einer Straße entlang. Wo gehst du?','You are walking beside a road. Where do you walk?','🚶🛣️',['🚶','Auf dem Gehweg','On the sidewalk'],['🚗','Auf der Fahrbahn','On the road']),
    pictureChoiceCard('Ein Ball rollt auf die Straße. Was machst du?','A ball rolls into the road. What do you do?','⚽🛣️',['🧑‍🤝‍🧑','Bei einem Erwachsenen bleiben','Stay with an adult'],['🏃','Dem Ball nachlaufen','Run after the ball']),
    pictureChoiceCard('Du möchtest die Straße überqueren. Was suchst du?','You want to cross the road. What do you look for?','🚶🛣️',['🚸','Zebrastreifen oder Ampel','Zebra crossing or traffic light'],['🚙','Lücke zwischen Autos','A gap between cars']),
    pictureChoiceCard('An der Baustelle stehen rot-weiße Baken. Wo gehst du?','Red-and-white barriers are at roadworks. Where do you walk?','🚧',['🚶','Außen herum','Around the outside'],['🚧','Durch die Baustelle','Through the roadworks']),
    pictureChoiceCard('Die Bahnschranke geht herunter. Was machst du?','The railway barrier is coming down. What do you do?','🚂🚧',['✋','Warten hinter der Schranke','Wait behind the barrier'],['🚶','Darunter durchgehen','Walk under it']),
    pictureChoiceCard('Du wartest an der Bushaltestelle. Wo wartest du?','You are waiting at a bus stop. Where do you wait?','🚏🚌',['🧍','Mit Abstand am Gehweg','On the sidewalk, well back'],['🛣️','Auf der Straße','In the road']),
    pictureChoiceCard('Ein Einsatzwagen kommt mit Blaulicht. Was machen die Autos?','An emergency vehicle is coming with flashing lights. What do cars do?','🚑🚨',['↔️','Platz machen','Make room'],['🚗','Im Weg stehen bleiben','Stay in its way']),
    pictureChoiceCard('Ein Auto fährt rückwärts aus einer Einfahrt. Was machst du?','A car is reversing out of a driveway. What do you do?','🚗⬅️',['✋','Warten und Blickkontakt suchen','Wait and make eye contact'],['🏃','Schnell dahinter laufen','Run behind it']),
    pictureChoiceCard('Es ist dunkel. Welche Kleidung hilft, dich zu sehen?','It is dark. Which clothing helps drivers see you?','🌙🚶',['✨','Helle Kleidung und Reflektoren','Bright clothes and reflectors'],['⚫','Dunkle Kleidung','Dark clothes']),
    pictureChoiceCard('Zwischen parkenden Autos möchtest du auf die Straße. Was ist sicher?','You want to cross between parked cars. What is safe?','🚗🚗',['👀','Zu einer freien Stelle gehen und schauen','Go to a clear spot and look'],['➡️','Zwischen den Autos hervorspringen','Step out between the cars']),
    pictureChoiceCard('Du steigst aus dem Auto. Auf welcher Seite steigst du sicher aus?','You are getting out of a car. Which side is safer?','🚗🚪',['🏠','Zur Gehwegseite','The sidewalk side'],['🛣️','Zur Straßenseite','The road side']),
    pictureChoiceCard('Du fährst Rad auf einem Radweg. Was beachtest du?','You are cycling on a bike path. What should you do?','🚲🛤️',['👀','Auf Fußgänger achten','Watch for pedestrians'],['🔔','Alle erschrecken und durchfahren','Startle everyone and speed through']),
    pictureChoiceCard('Du hörst am Bahnübergang ein Warnsignal. Was machst du?','You hear a warning signal at a railway crossing. What do you do?','🚉🔔',['✋','Stehen bleiben und warten','Stop and wait'],['🚲','Noch schnell rüberfahren','Hurry across']),
    pictureChoiceCard('Du gehst mit einem kleinen Kind zur Schule. Was hilft?','You are walking to school with a small child. What helps?','🏫🚶',['🤝','An der Hand gehen','Hold hands'],['📱','Beim Gehen aufs Handy sehen','Look at a phone while walking']),
    pictureChoiceCard('Ein Fahrrad-Schild zeigt einen Radweg. Wer fährt dort?','A bicycle sign marks a bike path. Who rides there?','🚲🟦',['🚲','Fahrräder','Bicycles'],['🚗','Autos','Cars']),
    pictureChoiceCard('An der Kreuzung ist ein Polizist. Wem folgst du?','A police officer is directing traffic at a junction. Who do you follow?','👮🚦',['👮','Den Zeichen des Polizisten','The officer’s signals'],['📱','Dem Handybildschirm','A phone screen']),
    pictureChoiceCard('Du kommst zu einem Kreisverkehr. Was machst du vor dem Überqueren?','You reach a roundabout. What do you do before crossing?','🔄🚗',['👀','Warten und nach Autos schauen','Wait and look for cars'],['🏃','Zwischen den Autos durchlaufen','Run between cars']),
    pictureChoiceCard('Das Schild zeigt einen Fußweg. Was machst du mit dem Fahrrad?','The sign marks a footpath. What do you do with your bicycle?','🚶🟦',['🚲','Absteigen und schieben','Get off and walk it'],['🚴','Schnell durchfahren','Ride through quickly']),
    pictureChoiceCard('Du kommst an einer Ausfahrt vorbei. Was kann dort passieren?','You are passing a driveway. What might happen there?','🏠🚗',['👀','Ein Auto kann herausfahren','A car may pull out'],['🛝','Dort ist immer ein Spielplatz','It is always a playground']),
    pictureChoiceCard('Das Schild zeigt Vorfahrt gewähren. Was macht ein Auto?','A give-way sign is ahead. What should a car do?','🔻🚗',['✋','Warten und Vorfahrt lassen','Wait and give way'],['➡️','Ohne Schauen fahren','Go without looking']),
    pictureChoiceCard('Du siehst ein rundes blaues Schild mit einem weißen Fahrrad.','You see a round blue sign with a white bicycle.','🔵🚲',['🚲','Den Radweg benutzen','Use the bike path'],['🚗','Mit dem Auto darauf fahren','Drive a car on it']),
    pictureChoiceCard('Das Schild zeigt einen Fußgänger. Wer benutzt diesen Weg?','The sign shows a pedestrian. Who uses this path?','🚶🟦',['🚶','Menschen zu Fuß','People walking'],['🏎️','Rennwagen','Race cars']),
    pictureChoiceCard('Du siehst ein Halteverbotsschild am Straßenrand. Was machst du?','You see a no-stopping sign at the roadside. What do you do?','🚫🅿️',['🚗','Nicht dort anhalten','Do not stop there'],['🅿️','Dort das Auto abstellen','Park there']),
    pictureChoiceCard('Ein gelbes Schild zeigt eine Umleitung. Wohin gehst du?','A yellow sign shows a detour. Where do you go?','↪️🟨',['➡️','Den Pfeilen folgen','Follow the arrows'],['🚧','Durch die Absperrung gehen','Go through the barrier']),
    pictureChoiceCard('Das Schild warnt vor einer Baustelle. Was ist richtig?','A sign warns about roadworks. What is right?','⚠️🚧',['🐢','Langsam und vorsichtig sein','Go slowly and carefully'],['🏎️','Schneller fahren','Drive faster']),
    pictureChoiceCard('Du siehst ein Schild mit einer Kuh. Was könnte dahinter sein?','You see a sign with a cow. What might be ahead?','⚠️🐄',['🐄','Tiere auf der Straße','Animals on the road'],['🏁','Eine Rennstrecke','A race track']),
    pictureChoiceCard('Das Schild zeigt einen Zug am Bahnübergang. Was tust du?','The sign shows a train at a railway crossing. What do you do?','⚠️🚂',['👀','Auf Zug und Schranke achten','Watch for trains and barriers'],['🎧','Musik lauter machen','Turn music up']),
    pictureChoiceCard('Das runde Schild hat einen roten Rand und zeigt 50.','The round sign has a red border and shows 50.','🔴50',['🐢','Höchstens 50 fahren','Drive no faster than 50'],['🏎️','So schnell wie möglich fahren','Drive as fast as possible']),
    pictureChoiceCard('Das Schild zeigt ein H für Haltestelle. Was findest du dort?','A sign shows H for a bus stop. What do you find there?','🅷🚌',['🚌','Busse halten dort','Buses stop there'],['🚂','Züge halten dort','Trains stop there']),
    pictureChoiceCard('Ein Auto blinkt rechts. Was kann es gleich tun?','A car is signalling right. What might it do next?','🚗➡️',['👀','Abbiegen','Turn'],['🏃','Plötzlich rückwärts fliegen','Fly backwards suddenly']),
    pictureChoiceCard('Du hörst ein Fahrrad klingeln. Was machst du auf dem Weg?','You hear a bicycle bell. What do you do on the path?','🚲🔔',['↔️','Platz machen und aufmerksam sein','Make room and pay attention'],['🙉','Ohren zuhalten und stehen bleiben','Cover your ears and block the way']),
    pictureChoiceCard('Ein Lieferwagen versperrt die Sicht an der Ecke. Wo schaust du?','A van blocks the view at a corner. Where do you look?','🚐🏠',['👀','An einer freien Stelle','From a clear spot'],['➡️','Blind um die Ecke laufen','Run around the corner without looking']),
    pictureChoiceCard('Du möchtest aus dem Bus aussteigen. Wann gehst du los?','You want to get off the bus. When do you move?','🚌🚪',['🛑','Wenn der Bus hält und die Tür offen ist','When the bus stops and the door opens'],['🏃','Während der Bus noch fährt','While the bus is moving']),
    pictureChoiceCard('Du bist im Bus. Wo bleibst du während der Fahrt?','You are on a bus. Where do you stay while it is moving?','🚌',['🪑','Sitzen oder gut festhalten','Sit or hold on securely'],['🚪','An der Tür herumturnen','Play by the door']),
    pictureChoiceCard('Ein Zebrastreifen hat eine Verkehrsinsel in der Mitte. Was tust du?','A zebra crossing has a refuge island in the middle. What do you do?','🚸🏝️',['⏸️','In der Mitte warten und neu schauen','Wait in the middle and look again'],['🏃','Ohne Schauen weiterlaufen','Keep going without looking']),
    pictureChoiceCard('Das Schild zeigt eine Sackgasse. Was bedeutet es?','The sign shows a dead end. What does it mean?','🚫➡️',['↩️','Die Straße endet dort','The road ends there'],['🚀','Die Straße führt ins Weltall','The road goes into space']),
    pictureChoiceCard('Du siehst ein Schild mit einem Fahrrad und Auto. Was beachtest du?','You see a sign with a bicycle and a car. What should you remember?','🚲🚗',['↔️','Auf beide achten','Watch for both'],['🎮','Das ist ein Spielplatz','It is a playground']),
    pictureChoiceCard('Ein Lkw biegt ab. Wo hältst du dich als Fußgänger auf?','A truck is turning. Where should you stay as a pedestrian?','🚚↩️',['↔️','Mit Abstand außerhalb des Wegs','Back and clear of its path'],['🚚','Ganz nah neben dem Lkw','Very close beside the truck']),
    pictureChoiceCard('Die Straße ist nass und rutschig. Wie fährst du mit dem Rad?','The road is wet and slippery. How do you ride your bike?','🌧️🚲',['🐢','Langsam und vorsichtig','Slowly and carefully'],['⚡','Schnell und mit scharfem Bremsen','Fast and braking sharply']),
    pictureChoiceCard('Du fährst Roller. Wo fährst du langsam und vorsichtig?','You are riding a scooter. Where do you go slowly and carefully?','🛴🚶',['👀','Bei Fußgängern','Near pedestrians'],['💨','Dicht an ihnen vorbei','Very close past them']),
    pictureChoiceCard('An der Ampel blinkt das grüne Licht. Was ist sicher?','The green traffic light is flashing. What is safe?','🚦🟢',['✋','Warten, wenn du noch nicht losgegangen bist','Wait if you have not started crossing'],['🏃','Schnell noch vor Autos rennen','Race in front of cars']),
    pictureChoiceCard('Du hörst ein Martinshorn hinter dir. Was machst du auf dem Gehweg?','You hear a siren behind you. What do you do on the sidewalk?','🚓🚨',['↔️','Ruhig bleiben und Platz machen','Stay calm and make room'],['🛣️','Auf die Straße springen','Jump into the road']),
    pictureChoiceCard('Das Schild zeigt einen Pfeil in eine Richtung. Was machst du?','The sign shows an arrow for one-way traffic. What do you do?','➡️🛣️',['➡️','Nur in die erlaubte Richtung fahren','Go only in the permitted direction'],['⬅️','Gegen den Pfeil fahren','Go against the arrow']),
    pictureChoiceCard('Du wartest vor einer roten Fußgängerampel. Was tust du?','You are waiting at a red pedestrian light. What do you do?','🚶🚦🔴',['✋','Am Gehweg warten','Wait on the sidewalk'],['🚶','Auf die Straße gehen','Walk into the road']),
    pictureChoiceCard('Vor der Schule steht ein Schülerlotse. Wie gehst du?','A school crossing patrol is outside the school. What do you do?','🏫🦺',['👮','Auf die Zeichen warten','Wait for the signal'],['🏃','Zwischen Autos loslaufen','Run between cars']),
    pictureChoiceCard('Ein Auto steht am Fußgängerüberweg. Wo gehst du vorbei?','A car is stopped at a pedestrian crossing. Where do you pass?','🚗🚸',['↔️','Mit Abstand und gut sichtbar','At a distance where you can be seen'],['🚗','Direkt hinter dem Auto hervorspringen','Dart out from behind the car']),
    pictureChoiceCard('Das Schild zeigt eine Spielstraße. Wie fährt ein Auto dort?','The sign shows a play street. How should a car drive there?','🏘️🚗',['🐢','Sehr langsam und aufmerksam','Very slowly and carefully'],['🏎️','Wie auf einer Rennstrecke','Like on a race track']),
    pictureChoiceCard('An einer Kreuzung gibt es keinen Zebrastreifen. Was machst du?','There is no zebra crossing at a junction. What do you do?','➕🛣️',['👀','Eine sichere Stelle suchen und gut schauen','Find a safe place and look carefully'],['🏃','Zwischen fahrenden Autos durchlaufen','Run between moving cars']),
  ];
  const logicCards = [
    pictureChoiceCard('Das Muster ist rot, blau, rot, blau. Was kommt als Nächstes?','The pattern is red, blue, red, blue. What comes next?','🔴🔵🔴🔵❓',['🔴','Rot','Red'],['🟢','Grün','Green']),
    pictureChoiceCard('Das Muster ist Hund, Katze, Hund, Katze. Was kommt weiter?','The pattern is dog, cat, dog, cat. What comes next?','🐶🐱🐶🐱❓',['🐶','Hund','Dog'],['🐰','Hase','Rabbit']),
    pictureChoiceCard('Die Zahlenfolge ist 1, 2, 3. Welche Zahl fehlt danach?','The number pattern is 1, 2, 3. Which number comes next?','1️⃣2️⃣3️⃣❓',['4️⃣','Vier','Four'],['6️⃣','Sechs','Six']),
    pictureChoiceCard('Die Pfeile drehen sich: oben, rechts, unten. Wohin zeigt der nächste?','The arrows turn: up, right, down. Which way is next?','⬆️➡️⬇️❓',['⬅️','Nach links','Left'],['⬆️','Nach oben','Up']),
    pictureChoiceCard('Der Roboter fährt ein Feld nach rechts und dann nach oben.','The robot moves one square right, then one square up.','🤖➡️⬆️',['↗️','Schräg rechts oben','Up and right'],['↙️','Schräg links unten','Down and left']),
    pictureChoiceCard('Welche Reihe hat nur Fahrzeuge?','Which row contains only vehicles?','🚗🚲🚌🍎',['🚗🚲🚌','Auto, Rad, Bus','Car, bike, bus'],['🚗🍎🚌','Auto, Apfel, Bus','Car, apple, bus']),
    pictureChoiceCard('Zuerst ziehst du Schuhe an, dann den Helm, dann fährst du los.','First put on shoes, then your helmet, then ride.','👟➡️🪖➡️❓',['🚲','Losfahren','Ride'],['🛏️','Schlafen','Sleep']),
    pictureChoiceCard('Der Code lautet: vor, vor, rechts. Welche Richtung kommt zuletzt?','The code says: forward, forward, right. Which direction is last?','⬆️⬆️❓',['➡️','Rechts','Right'],['⬅️','Links','Left']),
    pictureChoiceCard('Im Muster fehlt ein Bild: Sonne, Sonne, Mond, Sonne, Sonne.','One picture is missing: sun, sun, moon, sun, sun.','☀️☀️🌙☀️☀️❓',['🌙','Mond','Moon'],['⭐','Stern','Star']),
    pictureChoiceCard('Der Roboter schaut nach oben. Der Befehl ist „drehe rechts“.','The robot faces up. The command is “turn right.”','🤖⬆️',['➡️','Nach rechts schauen','Face right'],['⬅️','Nach links schauen','Face left']),
    pictureChoiceCard('Welche Form passt weiter: Kreis, Quadrat, Kreis, Quadrat?','Which shape comes next: circle, square, circle, square?','⚪🟦⚪🟦❓',['⚪','Kreis','Circle'],['🔺','Dreieck','Triangle']),
    pictureChoiceCard('Ein Code wiederholt sich: klatschen, stampfen, klatschen.','A code repeats: clap, stomp, clap.','👏👣👏❓',['👣','Stampfen','Stomp'],['👏','Klatschen','Clap']),
    pictureChoiceCard('Du gehst zwei Schritte vor und einen zurück. Wo bist du?','You take two steps forward and one step back. Where are you?','⬆️⬆️⬇️',['⬆️','Ein Feld vor dem Start','One square past the start'],['🏁','Am Ziel','At the finish']),
    pictureChoiceCard('Welche Zahl fehlt: 2, 4, 6, …?','Which number is missing: 2, 4, 6, …?','2️⃣4️⃣6️⃣❓',['8️⃣','Acht','Eight'],['7️⃣','Sieben','Seven']),
    pictureChoiceCard('Ein Muster wechselt: groß, klein, groß, klein.','A pattern alternates: big, small, big, small.','🐘🐭🐘🐭❓',['🐘','Groß','Big'],['🐭','Klein','Small']),
    pictureChoiceCard('Der Code ist links, rechts, links, rechts. Was kommt als Nächstes?','The code is left, right, left, right. What comes next?','⬅️➡️⬅️➡️❓',['⬅️','Links','Left'],['⬆️','Oben','Up']),
    pictureChoiceCard('Welche Dinge gehören zum Wetter?','Which things belong to the weather?','☀️🌧️❄️🚗',['☀️🌧️❄️','Sonne, Regen, Schnee','Sun, rain, snow'],['☀️🚗🍎','Sonne, Auto, Apfel','Sun, car, apple']),
    pictureChoiceCard('Der Roboter soll zum Stern. Welcher Code führt nach rechts?','The robot must reach the star. Which code goes right?','🤖⭐',['➡️➡️','Zweimal rechts','Right twice'],['⬅️⬅️','Zweimal links','Left twice']),
    pictureChoiceCard('Sortiere vom kleinsten zum größten: Maus, Hund, Elefant.','Order from smallest to biggest: mouse, dog, elephant.','🐭🐶🐘',['🐭🐶🐘','Maus, Hund, Elefant','Mouse, dog, elephant'],['🐘🐭🐶','Elefant, Maus, Hund','Elephant, mouse, dog']),
    pictureChoiceCard('Ein Code wiederholt sich: hüpfen, drehen, hüpfen, drehen. Was kommt als Nächstes?','A code repeats: jump, turn, jump, turn. What comes next?','🐸↪️🐸↪️❓',['🐸','Hüpfen','Jump'],['↪️','Drehen','Turn']),
    pictureChoiceCard('Welche Ampelfolge ist richtig: rot, gelb, grün, …?','Which traffic-light sequence is right: red, yellow, green, …?','🔴🟡🟢❓',['🔴','Rot','Red'],['🟢','Grün','Green']),
    pictureChoiceCard('Im Raster gehst du ein Feld nach unten und dann nach rechts.','On the grid, move one square down, then right.','⬇️➡️',['↘️','Schräg rechts unten','Down and right'],['↖️','Schräg links oben','Up and left']),
    pictureChoiceCard('Welche Gruppe ist nach Farben sortiert?','Which group is sorted by color?','🟥🟥🟥🟦',['🟥🟥🟥','Drei rote Dinge','Three red things'],['🟥🟦🟥','Rot, blau, rot','Red, blue, red']),
    pictureChoiceCard('Der Roboter wiederholt den Befehl vorwärts dreimal.','The robot repeats the forward command three times.','⬆️⬆️⬆️',['🏁','Drei Felder vorwärts','Three squares forward'],['↩️','Ein Feld rückwärts','One square backward']),
    pictureChoiceCard('Welche Reihe wiederholt sich immer gleich?','Which row repeats the same way?','🍎🍌🍎🍌',['🍎🍌🍎🍌','Apfel, Banane, Apfel, Banane','Apple, banana, apple, banana'],['🍎🍎🍌🍌','Apfel, Apfel, Banane, Banane','Apple, apple, banana, banana']),
    pictureChoiceCard('Du hast einen Schritt nach links und einen nach rechts gemacht.','You took one step left and one step right.','⬅️➡️',['🏠','Du bist wieder am Start','You are back at the start'],['➡️','Du bist zwei Felder rechts','You are two squares right']),
    pictureChoiceCard('Welche Karte beendet die Folge: Frühling, Sommer, Herbst, …?','Which season completes the sequence: spring, summer, autumn, …?','🌱☀️🍂❓',['❄️','Winter','Winter'],['🌸','Frühling','Spring']),
    pictureChoiceCard('Ein Code lautet: klatschen, klatschen, stampfen. Was wiederholt sich zuerst?','A code says: clap, clap, stomp. What repeats first?','👏👏👣',['👏','Klatschen','Clap'],['👣','Stampfen','Stomp']),
    pictureChoiceCard('Der Roboter fährt vorwärts, dreht links und fährt weiter.','The robot moves forward, turns left and moves on.','⬆️⬅️⬆️',['↖️','Der Weg knickt links ab','The path turns left'],['➡️','Der Weg bleibt gerade','The path stays straight']),
    pictureChoiceCard('Welche Form fehlt: Dreieck, Kreis, Quadrat, Dreieck, …?','Which shape is missing: triangle, circle, square, triangle, …?','🔺⚪🟦🔺❓',['⚪','Kreis','Circle'],['🟦','Quadrat','Square']),
    pictureChoiceCard('Der Roboter soll am Haus starten und zum Baum fahren.','The robot starts at the house and must reach the tree.','🏠⬆️🌳',['⬆️','Vorwärts fahren','Move forward'],['⬇️','Zurück fahren','Move backward']),
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
    return {levels, subjects, bank, safetyCards, logicCards, arrowCards, mazeFriends, generateMaze, searchObjects, wordCards};
  };
})();
