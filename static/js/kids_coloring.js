(() => {
  'use strict';

  const root = document.querySelector('[data-kids-coloring]');
  if (!root) return;
  const en = document.documentElement.lang.toLowerCase().startsWith('en');
  const t = (de, english) => en ? english : de;
  const svgNs = 'http://www.w3.org/2000/svg';
  const templates = [
    {id:'butterfly',emoji:'🦋',de:'Schmetterling',en:'Butterfly',zones:[
      ['left','M400 284 C340 145 178 126 145 218 C112 314 277 374 400 304 Z'],
      ['right','M400 284 C460 145 622 126 655 218 C688 314 523 374 400 304 Z'],
      ['body','M400 235 C378 268 378 326 400 360 C422 326 422 268 400 235 Z'],
    ],details:['M390 242 C374 205 350 195 328 196','M410 242 C426 205 450 195 472 196','M366 274 C335 249 302 247 277 260','M434 274 C465 249 498 247 523 260','M392 320 L408 320']},
    {id:'cat',emoji:'🐱',de:'Katze',en:'Cat',zones:[
      ['head','M252 205 L268 130 L325 166 Q400 140 475 166 L532 130 L548 205 Q585 244 562 296 Q536 358 400 360 Q264 358 238 296 Q215 244 252 205 Z'],
      ['body','M298 338 C265 375 270 480 316 506 Q400 536 484 506 C530 480 535 375 502 338 Q400 372 298 338 Z'],
      ['left-ear','M272 170 L285 145 L306 181 Z'],['right-ear','M494 181 L515 145 L528 170 Z'],
      ['left-leg','M325 468 Q360 477 382 482 L376 520 Q344 536 315 512 Z'],
      ['right-leg','M418 482 Q441 477 475 468 L485 512 Q456 536 424 520 Z'],
      ['tail','M497 389 C570 364 606 407 574 445 Q555 468 532 448 C566 434 557 414 522 427 Z'],
    ],details:['M335 248 Q349 236 363 248','M437 248 Q451 236 465 248','M400 260 L389 274 L411 274 Z','M400 274 Q387 291 372 280','M400 274 Q413 291 428 280','M350 278 L285 269','M350 292 L282 296','M450 278 L515 269','M450 292 L518 296']},
    {id:'fish',emoji:'🐠',de:'Fisch',en:'Fish',zones:[
      ['body','M216 300 C250 206 390 180 500 240 Q530 260 540 300 Q530 340 500 360 C390 420 250 394 216 300 Z'],
      ['tail','M518 239 L634 180 L620 300 L634 420 L518 361 Z'],
      ['fin-top','M337 216 Q365 143 427 207 Z'],['fin-bottom','M344 384 Q374 451 431 392 Z'],
    ],details:['M278 300 Q311 268 344 300 Q311 332 278 300 Z','M291 300 L327 300','M366 250 Q416 300 366 350','M446 250 Q480 300 446 350']},
    {id:'flower',emoji:'🌼',de:'Blume',en:'Flower',zones:[
      ['petal-top','M400 250 C350 210 350 125 400 104 C450 125 450 210 400 250 Z'],
      ['petal-left','M368 280 C300 265 269 205 298 169 C354 172 390 226 386 270 Z'],
      ['petal-right','M432 280 C500 265 531 205 502 169 C446 172 410 226 414 270 Z'],
      ['petal-bottom','M400 337 C350 380 350 456 400 477 C450 456 450 380 400 337 Z'],
      ['center','M400 290 m-45 0 a45 45 0 1 0 90 0 a45 45 0 1 0 -90 0'],
      ['stem','M382 325 L418 325 L430 520 L370 520 Z'],
      ['leaf-left','M382 404 C328 366 280 392 288 439 Q329 456 382 424 Z'],
      ['leaf-right','M418 454 C472 416 520 442 512 489 Q471 506 418 474 Z'],
    ],details:['M400 335 L400 510']},
    {id:'dinosaur',emoji:'🦕',de:'Dinosaurier',en:'Dinosaur',zones:[
      ['body','M199 337 C208 270 286 239 385 260 C453 273 487 321 487 386 Q480 445 425 459 L284 459 Q220 438 199 337 Z'],
      ['head','M379 276 C379 213 419 176 481 180 Q549 185 570 229 L601 255 L565 280 Q540 319 489 312 L437 300 Z'],
      ['tail','M218 321 L115 265 L145 369 L208 395 Z'],
      ['leg-left','M253 425 L317 425 L306 517 L236 517 Z'],['leg-right','M383 430 L447 425 L468 517 L397 517 Z'],
      ['spike-one','M353 266 L377 213 L407 270 Z'],['spike-two','M306 258 L325 206 L357 263 Z'],['spike-three','M261 271 L275 226 L307 266 Z'],
    ],details:['M524 220 Q537 214 547 224','M562 257 Q574 265 585 257','M288 327 Q316 310 342 329']},
    {id:'rocket',emoji:'🚀',de:'Rakete',en:'Rocket',zones:[
      ['body','M400 86 C332 153 324 253 335 366 L365 447 L435 447 L465 366 C476 253 468 153 400 86 Z'],
      ['window','M400 198 m-43 0 a43 43 0 1 0 86 0 a43 43 0 1 0 -86 0'],
      ['fin-left','M337 321 Q274 350 255 439 L356 403 Z'],['fin-right','M463 321 Q526 350 545 439 L444 403 Z'],
      ['flame','M365 431 Q400 457 435 431 Q437 492 400 535 Q363 492 365 431 Z'],
    ],details:['M400 119 L400 157','M369 365 L431 365']},
    {id:'house',emoji:'🏠',de:'Haus',en:'House',zones:[
      ['walls','M224 285 L400 150 L576 285 L576 520 L224 520 Z'],
      ['roof','M190 292 L400 125 L610 292 L578 330 L400 188 L222 330 Z'],
      ['door','M355 390 Q355 375 370 375 L430 375 Q445 375 445 390 L445 520 L355 520 Z'],
      ['window-left','M263 330 L333 330 L333 393 L263 393 Z'],
      ['window-right','M467 330 L537 330 L537 393 L467 393 Z'],
    ],details:['M298 330 L298 393','M263 361 L333 361','M502 330 L502 393','M467 361 L537 361','M400 375 L400 520']},
    {id:'sun',emoji:'☀️',de:'Sonne',en:'Sun',zones:[
      ['ray-top','M370 177 L400 82 L430 177 Z'],['ray-upper-right','M465 203 L535 130 L520 233 Z'],
      ['ray-right','M500 270 L605 270 L500 300 Z'],['ray-lower-right','M480 355 L550 430 L455 400 Z'],
      ['ray-bottom','M430 385 L400 490 L370 385 Z'],['ray-lower-left','M320 355 L250 430 L345 400 Z'],
      ['ray-left','M300 270 L195 270 L300 300 Z'],['ray-upper-left','M335 203 L265 130 L280 233 Z'],
      ['sun-disc','M400 195 m-95 0 a95 95 0 1 0 190 0 a95 95 0 1 0 -190 0'],
    ],details:['M355 185 Q365 175 375 185','M425 185 Q435 175 445 185','M350 225 Q400 270 450 225']},
    {id:'apple',emoji:'🍎',de:'Apfel',en:'Apple',zones:[
      ['fruit','M400 205 C340 158 250 190 245 302 C238 425 319 515 385 493 Q400 488 415 493 C481 515 562 425 555 302 C550 190 460 158 400 205 Z'],
      ['leaf','M404 176 C436 108 512 112 535 135 C512 190 458 204 404 190 Z'],
    ],details:['M400 207 Q390 164 402 135','M413 178 Q460 143 503 139','M400 240 Q400 320 400 420']},
    {id:'turtle',emoji:'🐢',de:'Schildkröte',en:'Turtle',zones:[
      ['head','M535 292 C579 268 627 285 632 320 C637 356 592 376 550 352 Z'],
      ['shell','M205 305 C221 206 307 166 400 166 C493 166 579 206 595 305 Q580 411 400 420 Q220 411 205 305 Z'],
      ['leg-front-left','M250 383 C218 399 212 440 241 460 Q270 475 296 439 L310 405 Z'],
      ['leg-front-right','M490 405 L510 439 Q536 475 565 460 C594 440 588 399 556 383 Z'],
      ['leg-back-left','M270 245 Q226 227 207 258 Q193 285 224 315 L260 302 Z'],
      ['leg-back-right','M540 302 L576 315 Q607 285 593 258 Q574 227 530 245 Z'],
      ['tail','M400 410 L367 462 L433 462 Z'],
    ],details:['M326 210 Q400 248 474 210','M250 300 Q400 350 550 300','M320 218 L300 298 L350 340','M480 218 L500 298 L450 340','M370 250 L400 230 L430 250 L430 300 L400 320 L370 300 Z','M595 316 Q603 309 611 316']},
    {id:'rainbow',emoji:'🌈',de:'Regenbogen',en:'Rainbow',zones:[
      ['band-red','M160 470 A240 240 0 0 1 640 470 L590 470 A190 190 0 0 0 210 470 Z'],
      ['band-orange','M210 470 A190 190 0 0 1 590 470 L540 470 A140 140 0 0 0 260 470 Z'],
      ['band-yellow','M260 470 A140 140 0 0 1 540 470 L490 470 A90 90 0 0 0 310 470 Z'],
      ['band-green','M310 470 A90 90 0 0 1 490 470 L440 470 A40 40 0 0 0 360 470 Z'],
      ['cloud-left','M125 480 C95 480 85 458 99 440 C110 425 129 426 143 438 C154 410 196 408 211 439 C239 443 245 480 214 487 L125 487 Z'],
      ['cloud-right','M586 480 C555 480 549 458 562 440 C573 425 592 426 606 438 C617 410 659 408 674 439 C702 443 708 480 677 487 L586 487 Z'],
    ],details:['M126 466 L208 466','M587 466 L671 466']},
    {id:'ball',emoji:'⚽',de:'Ball',en:'Ball',zones:[
      ['ball','M400 110 A220 220 0 1 0 400 550 A220 220 0 1 0 400 110 Z'],
      ['patch-center','M400 260 L448 295 L430 350 L370 350 L352 295 Z'],
      ['patch-top','M400 130 L455 150 L448 210 L400 235 L352 210 L345 150 Z'],
      ['patch-left','M190 270 L250 250 L295 290 L280 350 L220 370 L180 330 Z'],
      ['patch-right','M610 270 L550 250 L505 290 L520 350 L580 370 L620 330 Z'],
      ['patch-bottom','M400 480 L455 460 L448 400 L400 375 L352 400 L345 460 Z'],
    ],details:['M345 150 L352 210 L295 290 L280 350 L345 460','M455 150 L448 210 L505 290 L520 350 L455 460','M220 370 L280 350 L352 350 L370 350','M580 370 L520 350 L448 350 L430 350']},
    {id:'star',emoji:'⭐',de:'Stern',en:'Star',zones:[
      ['star','M400 100 L470 292 L675 300 L515 425 L570 620 L400 505 L230 620 L285 425 L125 300 L330 292 Z'],
    ],details:[]},
    {id:'heart',emoji:'❤️',de:'Herz',en:'Heart',zones:[
      ['heart','M400 520 C350 462 165 330 165 230 C165 118 300 100 400 220 C500 100 635 118 635 230 C635 330 450 462 400 520 Z'],
    ],details:[]},
    {id:'car',emoji:'🚗',de:'Auto',en:'Car',zones:[
      ['body','M135 355 L185 320 L245 215 Q260 190 295 190 L495 190 Q520 190 540 220 L605 320 L650 350 L650 450 L135 450 Z'],
      ['window-front','M270 225 L375 225 L375 310 L220 310 Z'],
      ['window-back','M395 225 L490 225 L550 310 L395 310 Z'],
      ['wheel-left','M220 410 m-58 0 a58 58 0 1 0 116 0 a58 58 0 1 0 -116 0'],
      ['wheel-right','M555 410 m-58 0 a58 58 0 1 0 116 0 a58 58 0 1 0 -116 0'],
    ],details:['M135 355 H650','M375 220 V355','M395 220 V355','M145 378 H190','M605 378 H645']},
    {id:'tree',emoji:'🌳',de:'Baum',en:'Tree',zones:[
      ['crown','M400 100 C330 100 295 150 300 205 C220 190 175 250 205 310 C135 350 175 430 250 430 L550 430 C625 430 665 350 595 310 C625 250 580 190 500 205 C505 150 470 100 400 100 Z'],
      ['trunk','M350 390 L450 390 L470 555 L330 555 Z'],
    ],details:['M400 230 V390','M400 320 L315 260','M400 340 L495 255']},
    {id:'balloon',emoji:'🎈',de:'Luftballon',en:'Balloon',zones:[
      ['balloon','M400 100 C285 100 220 190 220 295 C220 395 305 450 400 450 C495 450 580 395 580 295 C580 190 515 100 400 100 Z'],
      ['knot','M380 448 L420 448 L400 480 Z'],
    ],details:['M400 480 C360 515 440 535 400 570 C365 590 430 600 400 620']},
    {id:'kite',emoji:'🪁',de:'Drachen',en:'Kite',zones:[
      ['kite-top','M400 100 L565 300 L400 300 Z'],
      ['kite-left','M400 100 L400 300 L235 300 Z'],
      ['kite-right','M565 300 L400 500 L400 300 Z'],
      ['kite-bottom','M235 300 L400 300 L400 500 Z'],
      ['tail-bow-one','M372 535 L428 535 L400 575 Z'],['tail-bow-two','M372 595 L428 595 L400 635 Z'],
    ],details:['M400 100 V500','M235 300 H565','M400 500 C420 530 380 550 400 575 C420 590 380 610 400 635']},
    {id:'mushroom',emoji:'🍄',de:'Pilz',en:'Mushroom',zones:[
      ['cap','M160 315 C170 180 265 110 400 110 C535 110 630 180 640 315 Z'],
      ['stem','M300 315 L500 315 L470 540 L330 540 Z'],
      ['spot-one','M300 225 m-28 0 a28 28 0 1 0 56 0 a28 28 0 1 0 -56 0'],
      ['spot-two','M425 185 m-24 0 a24 24 0 1 0 48 0 a24 24 0 1 0 -48 0'],
      ['spot-three','M520 260 m-30 0 a30 30 0 1 0 60 0 a30 30 0 1 0 -60 0'],
    ],details:['M350 370 V500','M450 370 V500']},
    {id:'duck',emoji:'🦆',de:'Ente',en:'Duck',zones:[
      ['body','M230 350 C235 275 305 235 405 250 C480 260 525 300 545 350 C525 420 465 455 370 455 C285 455 230 415 230 350 Z'],
      ['head','M390 255 C385 170 430 120 495 125 C560 130 590 185 565 235 C540 280 465 290 420 270 Z'],
      ['beak','M555 185 L680 220 L565 250 Z'],
      ['wing','M300 330 C350 275 450 300 470 360 C425 405 350 410 300 370 Z'],
      ['leg-left','M330 435 L365 435 L355 520 L325 520 Z'],['leg-right','M430 435 L465 435 L475 520 L445 520 Z'],
    ],details:['M530 170 Q540 160 550 170','M330 520 L300 535 H370','M450 520 L430 535 H500']},
    {id:'blank',emoji:'🎨',de:'Leeres Blatt',en:'Blank page',zones:[],details:[]},
  ];
  const writingLetters = [...'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'Ä', 'Ö', 'Ü', 'ẞ'];
  writingLetters.forEach((glyph) => {
    const key = glyph === 'Ä' ? 'ae' : glyph === 'Ö' ? 'oe' : glyph === 'Ü' ? 'ue' : glyph === 'ẞ' ? 'eszett' : glyph.toLowerCase();
    templates.push({id:`letter-upper-${key}`,emoji:glyph,de:`Großbuchstabe ${glyph}`,en:`Capital letter ${glyph}`,kind:'uppercase',traceText:glyph,zones:[],details:[]});
    const lower = glyph.toLocaleLowerCase('de-DE');
    templates.push({id:`letter-lower-${key}`,emoji:lower,de:`Kleinbuchstabe ${lower}`,en:`Lowercase letter ${lower}`,kind:'lowercase',traceText:lower,zones:[],details:[]});
  });
  [...'0123456789'].forEach((glyph) => templates.push({
    id:`number-${glyph}`,emoji:glyph,de:`Zahl ${glyph}`,en:`Number ${glyph}`,kind:'number',traceText:glyph,zones:[],details:[],
  }));
  const labels = new Map(templates.map((item) => [item.id, t(item.de, item.en)]));
  const templateGrid = root.querySelector('[data-template-list]');
  const templateFilters = [...root.querySelectorAll('[data-template-filter]')];
  const savedGrid = root.querySelector('[data-saved-list]');
  const editor = root.querySelector('[data-editor]');
  const canvas = root.querySelector('[data-canvas]');
  const titleInput = root.querySelector('[data-title]');
  const status = root.querySelector('[data-save-status]');
  const galleryStatus = root.querySelector('[data-gallery-status]');
  const canvasHelp = root.querySelector('[data-canvas-help]');
  const undoButton = root.querySelector('[data-undo]');
  const palette = [...root.querySelectorAll('[data-color]')];
  const api = root.dataset.api;
  const csrf = root.dataset.csrf;
  let current = null;
  let drawingId = null;
  let selectedColor = '#ef4444';
  let mode = 'draw';
  let undoStack = [];
  let activeStroke = null;
  let previewPath = null;
  let saveTimer = 0;
  let saveQueue = Promise.resolve();

  // Keep category controls and the visible grid in sync, including dynamically
  // generated letter and number templates.

  const blankDrawing = (templateId) => ({templateId, fills:{}, strokes:[]});
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const makeSvg = (tag, attrs = {}) => {
    const element = document.createElementNS(svgNs, tag);
    Object.entries(attrs).forEach(([name, value]) => element.setAttribute(name, String(value)));
    return element;
  };
  const templateFor = (id) => templates.find((item) => item.id === id) || templates[0];
  const currentTemplate = () => current ? templateFor(current.templateId) : null;
  const setStatus = (message) => { status.textContent = message; };
  const pathFromPoints = (points) => points.map(([x,y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  const pushUndo = () => {
    if (!current) return;
    undoStack.push(copy(current));
    if (undoStack.length > 40) undoStack.shift();
    undoButton.disabled = false;
  };

  function renderCanvas() {
    if (!current) return;
    const template = currentTemplate();
    const fillButton = root.querySelector('[data-mode="fill"]');
    fillButton.hidden = !template.zones.length;
    if (!template.zones.length && mode === 'fill') {
      mode = 'draw';
      root.querySelectorAll('[data-mode]').forEach((item) => {
        const selected = item.dataset.mode === mode;
        item.classList.toggle('active',selected);
        item.setAttribute('aria-pressed',String(selected));
      });
    }
    canvasHelp.textContent = template.traceText
      ? t(`Fahre mit dem Finger oder der Maus die Punkte für „${template.traceText}“ nach. Du kannst auch frei darüber malen.`,`Trace the dots for “${template.traceText}” with your finger or mouse. You can also draw freely over it.`)
      : t('Freihand ist eingeschaltet. Tippe auf „Flächen färben“, um eine Form auszumalen. „Für mich speichern“ legt dein Bild privat ab.','Finger painting is on. Tap “Color areas” to fill a shape. Save for me keeps it private.');
    canvas.replaceChildren();
    canvas.append(makeSvg('rect', {x:0,y:0,width:800,height:600,fill:'#fff'}));
    if (template.traceText) {
      canvas.append(makeSvg('path', {d:'M125 500 H675 M125 135 H675',fill:'none',stroke:'#d8dee7','stroke-width':3,'stroke-dasharray':'8 9','pointer-events':'none'}));
      const guide = makeSvg('text', {
        x:400,y:475,'text-anchor':'middle','font-family':'Arial Rounded MT Bold, Trebuchet MS, sans-serif',
        'font-size':430,'font-weight':700,fill:'none',stroke:'#93a4b8','stroke-width':5,
        'stroke-dasharray':'2 12','stroke-linecap':'round','stroke-linejoin':'round','pointer-events':'none',
      });
      guide.textContent = template.traceText;
      canvas.append(guide);
    }
    template.zones.forEach(([key,d]) => {
      const zone = makeSvg('path', {d,fill:current.fills[key] || '#fff', 'data-zone':key, tabindex:0, role:'button', 'aria-label':t('Fläche färben','Color area')});
      zone.addEventListener('keydown', (event) => {
        if (mode === 'fill' && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          fillZone(key);
        }
      });
      canvas.append(zone);
    });
    template.details.forEach((d) => canvas.append(makeSvg('path', {d,fill:'none',stroke:'#243244','stroke-width':7,'stroke-linecap':'round','stroke-linejoin':'round','pointer-events':'none'})));
    current.strokes.forEach((stroke) => canvas.append(makeSvg('path', {
      d:pathFromPoints(stroke.points),fill:'none',stroke:stroke.color,'stroke-width':stroke.width,
      'stroke-linecap':'round','stroke-linejoin':'round','pointer-events':'none',
    })));
    canvas.setAttribute('aria-label', template.traceText
      ? `${t(template.de,template.en)}. ${t('Gepunktete Vorlage zum Nachspuren','Dotted tracing guide')}`
      : `${t(template.de,template.en)}. ${t('Malbild','Coloring picture')}`);
  }

  function makeTemplateCard(template) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'kids-template-card';
    button.dataset.template = template.id;
    button.dataset.templateKind = template.kind || 'picture';
    if (template.traceText) button.classList.add('kids-writing-template');
    button.setAttribute('aria-label', `${t('Vorlage auswählen: ','Choose picture: ')}${t(template.de,template.en)}`);
    const emoji = document.createElement('span');
    emoji.className = 'kids-template-emoji';
    emoji.setAttribute('aria-hidden','true');
    emoji.textContent = template.emoji;
    const name = document.createElement('strong');
    name.textContent = t(template.de,template.en);
    button.append(emoji,name);
    button.addEventListener('click', () => startTemplate(template.id));
    return button;
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      credentials:'same-origin',
      ...options,
      headers:{Accept:'application/json', ...(options.body ? {'Content-Type':'application/json','X-CSRF-Token':csrf} : {}), ...(options.headers || {})},
    });
    let result = {};
    try { result = await response.json(); } catch (_error) { /* A status message below handles non-JSON errors. */ }
    if (!response.ok) throw new Error(result.error || t('Speichern fehlgeschlagen. Bitte versuche es erneut.','Saving failed. Please try again.'));
    return result;
  }

  function renderSaved(drawings) {
    savedGrid.replaceChildren();
    if (!drawings.length) {
      const empty = document.createElement('p');
      empty.className = 'text-secondary';
      empty.textContent = t('Hier erscheinen deine gespeicherten Bilder.','Your saved pictures will appear here.');
      savedGrid.append(empty);
      return;
    }
    drawings.forEach((drawing) => {
      const card = document.createElement('article');
      card.className = 'card kids-saved-card';
      const body = document.createElement('div');
      body.className = 'card-body p-3';
      const heading = document.createElement('h3');
      heading.className = 'h6 card-title mb-1';
      heading.textContent = drawing.title;
      const info = document.createElement('p');
      info.className = 'small text-secondary mb-2';
      info.textContent = `${labels.get(drawing.template_id) || ''} · ${String(drawing.updated_at || '').slice(0,16).replace('T',' ')}`;
      const actions = document.createElement('div');
      actions.className = 'd-flex gap-2';
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'btn btn-sm btn-primary';
      open.textContent = t('Öffnen','Open');
      open.addEventListener('click', () => openDrawing(drawing.drawing_id));
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn btn-sm btn-outline-danger';
      remove.textContent = t('Löschen','Delete');
      remove.setAttribute('aria-label', `${t('Bild löschen: ','Delete picture: ')}${drawing.title}`);
      remove.addEventListener('click', () => deleteDrawing(drawing.drawing_id, drawing.title));
      actions.append(open,remove);
      body.append(heading,info,actions);
      card.append(body);
      savedGrid.append(card);
    });
  }

  async function refreshSaved() {
    try {
      const result = await requestJson(api);
      renderSaved(result.drawings || []);
      galleryStatus.textContent = '';
    } catch (error) {
      galleryStatus.textContent = error.message;
    }
  }

  async function flushCurrent() {
    if (!current) return true;
    const hasContent = current.strokes.length || Object.keys(current.fills).length;
    if (!hasContent && !saveTimer && !drawingId
        && status.textContent === t('Noch nicht gespeichert','Not saved yet')) return true;
    if (!saveTimer && status.textContent === t('Gespeichert','Saved')) return true;
    if (saveTimer) {
      window.clearTimeout(saveTimer);
      saveTimer = 0;
      saveQueue = saveQueue.then(saveNow, saveNow);
    } else if (status.textContent !== t('Speichert …','Saving …')
        && status.textContent !== t('Gespeichert','Saved')) {
      saveQueue = saveQueue.then(saveNow, saveNow);
    }
    const saved = await saveQueue;
    if (saved === true || status.textContent === t('Gespeichert','Saved')) return true;
    return saved || window.confirm(t('Das aktuelle Bild konnte nicht gespeichert werden. Trotzdem fortfahren?','The current picture could not be saved. Continue anyway?'));
  }

  async function cancelEditing(event) {
    const button = event.currentTarget;
    button.disabled = true;
    if (!(await flushCurrent())) {
      button.disabled = false;
      return;
    }
    const wasSaved = status.textContent === t('Gespeichert','Saved');
    editor.hidden = true;
    document.documentElement.classList.remove('kids-coloring-focus');
    galleryStatus.textContent = wasSaved
      ? t('Malansicht geschlossen. Dein Bild ist unter „Meine Bilder“ gespeichert.','Coloring closed. Your picture is saved under “My pictures”.')
      : t('Malansicht geschlossen.','Coloring closed.');
    const templateHeading = root.querySelector('#kids-template-heading');
    templateHeading.scrollIntoView({behavior:'smooth',block:'start'});
    templateHeading.focus();
    button.disabled = false;
  }

  async function startTemplate(templateId) {
    if (!(await flushCurrent())) return;
    current = blankDrawing(templateId);
    drawingId = null;
    mode = 'draw';
    root.querySelectorAll('[data-mode]').forEach((item) => {
      const selected = item.dataset.mode === mode;
      item.classList.toggle('active',selected);
      item.setAttribute('aria-pressed',String(selected));
    });
    undoStack = [];
    editor.hidden = false;
    titleInput.value = currentTemplate().traceText
      ? t(currentTemplate().de,currentTemplate().en)
      : `${t('Mein Kunstwerk','My artwork')} · ${new Intl.DateTimeFormat(en ? 'en' : 'de', {dateStyle:'short',timeStyle:'short'}).format(new Date())}`;
    undoButton.disabled = true;
    setStatus(t('Noch nicht gespeichert','Not saved yet'));
    renderCanvas();
    document.documentElement.classList.add('kids-coloring-focus');
    editor.scrollIntoView({behavior:'smooth',block:'start'});
  }

  async function openDrawing(id) {
    try {
      if (!(await flushCurrent())) return;
      const result = await requestJson(`${api}/${encodeURIComponent(id)}`);
      const drawing = result.drawing;
      if (!drawing || !templates.some((item) => item.id === drawing.template_id)) throw new Error(t('Dieses Bild kann nicht geöffnet werden.','This picture cannot be opened.'));
      current = {templateId:drawing.template_id, fills:drawing.drawing.fills || {}, strokes:drawing.drawing.strokes || []};
      drawingId = drawing.drawing_id;
      titleInput.value = drawing.title;
      undoStack = [];
      undoButton.disabled = true;
      editor.hidden = false;
      setStatus(t('Gespeichert','Saved'));
      renderCanvas();
      document.documentElement.classList.add('kids-coloring-focus');
      editor.scrollIntoView({behavior:'smooth',block:'start'});
    } catch (error) {
      galleryStatus.textContent = error.message;
    }
  }

  async function saveNow() {
    if (!current) return;
    const modelAtSave = current;
    const idAtSave = drawingId;
    const payload = {
      title:titleInput.value.trim() || t('Mein Bild','My picture'),
      template_id:modelAtSave.templateId,
      drawing:copy(modelAtSave),
    };
    delete payload.drawing.templateId;
    setStatus(t('Speichert …','Saving …'));
    try {
      const result = await requestJson(idAtSave ? `${api}/${encodeURIComponent(idAtSave)}` : api, {
        method:idAtSave ? 'PUT' : 'POST', body:JSON.stringify(payload),
      });
      if (!idAtSave && current === modelAtSave) drawingId = result.drawing_id;
      if (current === modelAtSave) setStatus(t('Gespeichert','Saved'));
      await refreshSaved();
      return true;
    } catch (error) {
      if (current === modelAtSave) setStatus(error.message);
      return false;
    }
  }

  function markChanged() {
    setStatus(t('Änderungen noch nicht gespeichert','Changes not saved yet'));
    if (saveTimer) window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      saveTimer = 0;
      saveQueue = saveQueue.then(saveNow, saveNow);
    }, 650);
  }

  function fillZone(key) {
    if (!current || mode !== 'fill') return;
    pushUndo();
    current.fills[key] = selectedColor;
    renderCanvas();
    markChanged();
  }

  function pointFromEvent(event) {
    const matrix = canvas.getScreenCTM();
    if (!matrix) return [0,0];
    const point = canvas.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const local = point.matrixTransform(matrix.inverse());
    return [Math.max(0,Math.min(800,local.x)),Math.max(0,Math.min(600,local.y))];
  }

  function finishStroke() {
    if (!activeStroke) return;
    if (activeStroke.points.length === 1) activeStroke.points.push([...activeStroke.points[0]]);
    if (previewPath) previewPath.remove();
    current.strokes.push(activeStroke);
    activeStroke = null;
    previewPath = null;
    renderCanvas();
    markChanged();
  }

  canvas.addEventListener('pointerdown', (event) => {
    if (!current || mode !== 'draw' || event.button !== 0) return;
    event.preventDefault();
    pushUndo();
    activeStroke = {color:selectedColor,width:30,points:[pointFromEvent(event)]};
    previewPath = makeSvg('path', {fill:'none',stroke:activeStroke.color,'stroke-width':activeStroke.width,'stroke-linecap':'round','stroke-linejoin':'round','pointer-events':'none'});
    canvas.append(previewPath);
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!activeStroke) return;
    const point = pointFromEvent(event);
    const previous = activeStroke.points[activeStroke.points.length - 1];
    if (Math.hypot(point[0]-previous[0],point[1]-previous[1]) < 1.2) return;
    if (activeStroke.points.length < 1200) activeStroke.points.push(point);
    if (previewPath) previewPath.setAttribute('d',pathFromPoints(activeStroke.points));
  });
  ['pointerup','pointercancel','lostpointercapture'].forEach((eventName) => canvas.addEventListener(eventName, finishStroke));
  canvas.addEventListener('click', (event) => {
    if (mode !== 'fill') return;
    const zone = event.target.closest('[data-zone]');
    if (zone) fillZone(zone.dataset.zone);
  });

  palette.forEach((button) => button.addEventListener('click', () => {
    selectedColor = button.dataset.color;
    palette.forEach((item) => {
      const selected = item === button;
      item.classList.toggle('is-selected',selected);
      item.setAttribute('aria-pressed',String(selected));
    });
  }));

  root.querySelectorAll('[data-mode]').forEach((button) => button.addEventListener('click', () => {
    mode = button.dataset.mode;
    root.querySelectorAll('[data-mode]').forEach((item) => {
      const selected = item === button;
      item.classList.toggle('active',selected);
      item.setAttribute('aria-pressed',String(selected));
    });
    canvas.style.cursor = mode === 'draw' ? 'crosshair' : 'pointer';
  }));

  undoButton.addEventListener('click', () => {
    if (!undoStack.length) return;
    current = undoStack.pop();
    undoButton.disabled = !undoStack.length;
    renderCanvas();
    markChanged();
  });
  root.querySelector('[data-finish]').addEventListener('click', async (event) => {
    if (!current) return;
    const button = event.currentTarget;
    button.disabled = true;
    if (saveTimer) { window.clearTimeout(saveTimer); saveTimer = 0; }
    setStatus(t('Speichert …','Saving …'));
    saveQueue = saveQueue.then(saveNow, saveNow);
    const saved = await saveQueue;
    if (saved) {
      document.documentElement.classList.remove('kids-coloring-focus');
      editor.hidden = true;
      galleryStatus.textContent = t('Privat in „Meine Bilder“ gespeichert. Nur du kannst es sehen.','Saved privately in “My pictures”. Only you can see it.');
      root.querySelector('#kids-saved-heading').scrollIntoView({behavior:'smooth',block:'start'});
    }
    button.disabled = false;
  });
  root.querySelector('[data-cancel]').addEventListener('click', cancelEditing);
  titleInput.addEventListener('input', () => { if (current) markChanged(); });
  root.querySelector('[data-new-drawing]').addEventListener('click', () => startTemplate('blank'));

  async function deleteDrawing(id, title) {
    if (!window.confirm(`${t('Bild wirklich löschen? ','Delete this picture? ')}${title}`)) return;
    try {
      await requestJson(`${api}/${encodeURIComponent(id)}`,{method:'DELETE',headers:{'X-CSRF-Token':csrf}});
      if (drawingId === id) {
        drawingId = null;
        current = null;
        editor.hidden = true;
      }
      await refreshSaved();
    } catch (error) {
      galleryStatus.textContent = error.message;
    }
  }

  templates.forEach((template) => templateGrid.append(makeTemplateCard(template)));
  function applyTemplateFilter(kind) {
    templateGrid.dataset.filter = kind;
    templateGrid.querySelectorAll('[data-template]').forEach((card) => {
      const cardKind = card.dataset.templateKind;
      const visible = kind === 'all' || cardKind === kind
        || (kind === 'writing' && ['uppercase','lowercase','number'].includes(cardKind));
      card.hidden = !visible;
    });
    templateFilters.forEach((button) => {
      const selected = button.dataset.templateFilter === kind;
      button.classList.toggle('active',selected);
      button.setAttribute('aria-pressed',String(selected));
    });
  }
  templateFilters.forEach((button) => button.addEventListener('click', () => applyTemplateFilter(button.dataset.templateFilter)));
  applyTemplateFilter(templateGrid.dataset.filter || 'picture');
  refreshSaved();
})();
