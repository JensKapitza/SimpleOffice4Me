# Kids Logikwerkstatt / Kids logic lab

Open **Kinder / Kids** and pick one of the three local activity cards. No
timer, camera, microphone, audio requirement, internet service, or game data
storage. Controls use ordinary keyboard and touch semantics. German and
English are available. The game never controls equipment or shows real wiring.

- **Schalter & Maschinen:** First close one virtual battery loop. Then test two
  switches in series (both must be closed) and in parallel (either or both
  closed). NICHT / NOT reverses a logical signal; it is not a broken circuit
  that lights up. Keep moving the gate until it reaches its simulated upper or
  lower stop. Asking for both directions blocks movement. In the apple line,
  press one step at a time: conveyor to press, press to applesauce, filler AND
  empty jar. The line always shows which item is ready or what is missing.
  This is learning about control flow, not an applesauce recipe.
- **Formen & Mengen:** The displayed shapes are the universe. Red items are
  set A and circles are set B. Pick/check the intersection, inclusive union,
  complement of A, or A minus B. Tile shapes, words, and individual result
  markers supplement color.
- **Sicher bei Strom:** A separate picture-led story with back, next and
  replay. On the Kids dashboard, the person tile opens the picture story; the
  book tile is the separate reading course on plugs and cables with questions.
  The original vector illustrations show only safe behavior: leave the loose
  plug alone, step back and get a grown-up, leave a dropped device alone, bring a sibling
  along from a safe distance without touching, and ordinary use of a working
  light switch. The page's existing safety lesson link is optional; listening
  or reading is not required to turn the pictures. For grown-ups, see
  [BIÖG: Sicherheit zu Hause](https://www.kindergesundheit-info.de/themen/sicher-aufwachsen/sicherheit-im-alltag/zu-hause/).

## Reproduce

Use a repository-supported Python version and Node. From the project root:

```sh
python -m pip install -e '.[test,web-export]'
node --test tests/kids_logic_workshop.test.mjs
PYTHONPATH=.:tests python -m unittest -v test_kids_logic_workshop test_kids_mini_games test_kids_mode test_template_accessibility
python -m playwright install --with-deps chromium
SIMPLEOFFICE_LOGIC_BROWSER_REQUIRED=1 SIMPLEOFFICE_LOGIC_SCREENSHOTS=./logic-test-pictures PYTHONPATH=.:tests \
  python -m unittest -v test_kids_logic_workshop_browser
```

The dedicated CI workflow uses a disposable signed-in test user. It checks
keyboard circuit changes, the conflicting gate directions and both end
stops, a deliberately stalled and then finished applesauce flow, sets with
portrait touch, and the four story scenes including replay and control
focus. It retains the portrait story renders as a seven-day artifact when
they are produced. It rejects remote or mutating requests in the walk-through.
The code does not claim testing on physical devices.
