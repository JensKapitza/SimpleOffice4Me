// Dump the syllable game vocabulary (single source in kids_syllables.js) as JSON
// for clip generation. Usage: node tools/dump_kids_syllables_inventory.js
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/js/kids_syllables.js'), 'utf8');
const window = {};
// No `document`: the game only exports its logic API (KidsSyllables).
vm.runInNewContext(source, {window});
const api = window.KidsSyllables;
if (!api || !api.WORDS || !api.RHYMES || !api.CLIPS || !api.slugify) {
  throw new Error('KidsSyllables export incomplete');
}
const words = {};
for (const [lang, entries] of Object.entries(api.WORDS)) {
  words[lang] = entries.map(e => ({emoji: e.emoji, word: e.word, parts: [...e.parts]}));
}
const rhymes = {};
for (const [lang, sets] of Object.entries(api.RHYMES)) {
  rhymes[lang] = sets.map(s => ({
    target: {...s.target}, rhyme: {...s.rhyme}, others: s.others.map(o => ({...o})),
  }));
}
process.stdout.write(JSON.stringify({words, rhymes, clips: {...api.CLIPS}}));
