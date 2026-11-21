(()=>{
  const table=document.querySelector('.mail-message-table');
  if(!table)return;
  const toggles=[...document.querySelectorAll('[data-mail-column-toggle]')];
  const defaults={status:true,subject:true,from:true,to:false,date:true,size:false,case:false};
  const storageKey='simpleoffice.mail.columns.v1.'+table.dataset.account;
  let config={...defaults};
  try{
    const saved=JSON.parse(localStorage.getItem(storageKey));
    if(saved && typeof saved==='object' && !Array.isArray(saved)){
      for(const key of Object.keys(defaults))if(typeof saved[key]==='boolean')config[key]=saved[key];
    }
  }catch(_){}
  const dateFormat = new Intl.DateTimeFormat(document.documentElement.lang || 'de', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit'
  });
  for (const cell of table.querySelectorAll('[data-mail-col="date"][title]')) {
    const stamp = Date.parse(cell.title);
    if (Number.isFinite(stamp)) cell.textContent = dateFormat.format(stamp);
  }
  const apply=()=>{
    config.subject=true; // The subject is also the link that opens the message.
    for(const toggle of toggles)toggle.checked=config[toggle.dataset.mailColumnToggle];
    for(const cell of table.querySelectorAll('[data-mail-col]')){
      const visible=config[cell.dataset.mailCol];
      cell.hidden=!visible;
      cell.style.display=visible?'':'none';
    }
  };
  for(const toggle of toggles)toggle.addEventListener('change',()=>{
    const key=toggle.dataset.mailColumnToggle;
    if(!Object.prototype.hasOwnProperty.call(defaults,key))return;
    config[key]=toggle.checked;
    apply();
    try{localStorage.setItem(storageKey,JSON.stringify(config))}catch(_){}
  });
  document.getElementById('mail-columns-reset')?.addEventListener('click',()=>{
    config={...defaults};apply();
    try{localStorage.removeItem(storageKey)}catch(_){}
  });
  apply();
})();
