// Estrazione delle transazioni da WashTec Plus (Report > Dati transazione).
// Gira DENTRO plus.washtec.com, gia' loggato: la usano sia il preferito
// "WashTec -> Gestionale" (importa_washtec.html) sia l'import automatico
// sul server (comando importa_washtec_auto, via Playwright).
// Il preferito la serializza con toString e Chrome puo' togliere gli
// a-capo: deve restare autosufficiente e con i commenti solo su righe
// intere (mai codice seguito da un commento sulla stessa riga).
// dal, al: 'gg/mm/aa'. avanzamento(testo): facoltativo.
// Ritorna le righe grezze [numero, orario, programma, metodo, pagato,
// manutenzione, eseguito]; in caso di problemi lancia una stringa.
async function estraiWashtec(dal, al, avanzamento) {
  const scrivi = avanzamento || (() => {});
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const fire = el => ['mousedown', 'mouseup', 'click'].forEach(t =>
    el.dispatchEvent(new MouseEvent(t, { bubbles: true, view: window })));
  const until = async (f, ms) => {
    const t0 = Date.now();
    while (Date.now() - t0 < (ms || 20000)) { const v = f(); if (v) return v; await sleep(250); }
    return null;
  };
  const foglia = re => [...document.querySelectorAll('div,td,span,a')]
    .find(e => e.children.length === 0 && e.offsetParent && re.test(e.textContent.trim()));
  // Paginatore: "1-25 di 96" oppure, su periodi lunghi, "976-1.000 di oltre 1.000"
  // (punto delle migliaia; totale non ancora noto: diventa esatto solo alla fine).
  const pag = () => foglia(/^[\d.]+-[\d.]+ di (oltre )?[\d.]+$/);
  // Durante il cambio pagina WashTec ridisegna il paginatore: per un
  // attimo non esiste, quindi si legge sempre in modo tollerante.
  const testoPag = () => { const p = pag(); return p ? p.textContent.trim() : ''; };
  const infoPag = () => {
    const m = testoPag().replace(/\./g, '').match(/^(\d+)-(\d+) di (oltre )?(\d+)$/);
    return m ? { a: +m[2], totale: +m[4], esatto: !m[3] } : null;
  };
  const imposta = (el, v) => {
    el.focus(); el.value = v;
    ['input', 'change', 'keyup', 'blur'].forEach(t => el.dispatchEvent(new Event(t, { bubbles: true })));
  };

  if (!/plus\.washtec\.com/.test(location.host)) throw 'apri prima plus.washtec.com e fai il login';
  if (location.hash !== '#reporting') location.hash = '#reporting';
  const scheda = await until(() => foglia(/^Dati transazione$/));
  if (!scheda) throw 'sezione Report non trovata: hai fatto il login?';
  fire(scheda);
  const date = await until(() => {
    const a = [...document.querySelectorAll('input')].filter(i => i.offsetParent && /^\d\d\/\d\d\/\d\d$/.test(i.value));
    return a.length >= 2 ? a : null;
  });
  if (!date) throw 'campi data non trovati';
  scrivi('Estrazione dal ' + dal + ' al ' + al + '&hellip;');
  imposta(date[0], dal);
  imposta(date[1], al);

  const prima = testoPag();
  const lente = [...document.querySelectorAll('button[title=Cerca]')].find(e => e.offsetParent);
  if (!lente) throw 'bottone di ricerca non trovato';
  fire(lente);
  await until(() => testoPag() && testoPag() !== prima, 60000);
  await sleep(800);
  if (!testoPag()) throw 'nessun risultato per il periodo';

  const righe = {};
  const raccogli = () => [...document.querySelectorAll('tr')].filter(tr => tr.offsetParent).forEach(tr => {
    const c = [...tr.cells].map(x => x.innerText.trim());
    if (c.length >= 10 && /^\d+$/.test(c[1]) && /^\d\d\/\d\d\/\d\d \d\d:\d\d:\d\d$/.test(c[2]))
      righe[c[1] + '|' + c[2]] = [c[1], c[2], c[3], c[5], c[6], c[8], c[9]];
  });
  const frecce = () => pag().closest('table').querySelectorAll('img');
  // Cambio pagina: attende che il paginatore mostri un testo nuovo
  // e stabile, poi lascia renderizzare le righe.
  const primaRiga = () => {
    const tr = [...document.querySelectorAll('tr')].find(tr => tr.offsetParent &&
      tr.cells.length >= 10 && /^\d+$/.test(tr.cells[1].innerText.trim()));
    return tr ? tr.cells[1].innerText.trim() + '|' + tr.cells[2].innerText.trim() : '';
  };
  const vaiA = async indiceFreccia => {
    const t = testoPag(), r = primaRiga();
    fire(frecce()[indiceFreccia]);
    const ok = await until(() => testoPag() && testoPag() !== t &&
                                 primaRiga() && primaRiga() !== r, 15000);
    await sleep(400);
    return ok;
  };
  // dalla prima pagina, poi avanti finche' non le ho tutte
  if (!/^1-/.test(testoPag())) await vaiA(0);
  let info = infoPag();
  for (let giro = 0; giro < 1000; giro++) {
    raccogli();
    info = infoPag() || info;
    scrivi('Lette ' + Object.keys(righe).length + ' righe su ' +
           (info.esatto ? '' : 'oltre ') + info.totale + '&hellip;');
    if (info.esatto && info.a >= info.totale) break;
    if (!await vaiA(2)) throw 'la tabella non avanza di pagina';
  }
  const dati = Object.values(righe);
  if (!info.esatto || dati.length !== info.totale) throw 'lette ' + dati.length + ' righe su ' + info.totale;
  return dati;
}
