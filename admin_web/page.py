"""The single-page admin dashboard (responsive, no build step)."""

PAGE = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>usertgbot · Admin</title>
<style>
  :root{--bg:#0f141a;--card:#1a222c;--card2:#222c38;--fg:#e6edf3;--muted:#8b98a5;--acc:#3b82f6;--ok:#22c55e;--bad:#ef4444;--warn:#f59e0b;--line:#2a3644}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,Segoe UI,Roboto,sans-serif}
  header{padding:14px 18px;background:var(--card);border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center;flex-wrap:wrap;position:sticky;top:0;z-index:5}
  header h1{font-size:17px;margin:0;flex:1}
  .tabs{display:flex;gap:6px}
  .tab{padding:8px 14px;border-radius:8px;background:var(--card2);cursor:pointer;user-select:none;font-size:14px}
  .tab.active{background:var(--acc)}
  main{padding:16px;max-width:1100px;margin:0 auto}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
  .stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
  .stat .n{font-size:26px;font-weight:700}
  .stat .l{color:var(--muted);font-size:13px;margin-top:4px}
  .filters{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
  select,input,button{background:var(--card2);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:14px}
  button{cursor:pointer}
  button.pri{background:var(--acc);border-color:var(--acc)}
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
  .u{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
  .u .top{display:flex;justify-content:space-between;align-items:start;gap:8px}
  .u .name{font-weight:600}
  .u .id{color:var(--muted);font-size:12px}
  .badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:var(--card2)}
  .badge.ok{background:rgba(34,197,94,.15);color:var(--ok)}
  .badge.bad{background:rgba(239,68,68,.15);color:var(--bad)}
  .badge.pro{background:rgba(59,130,246,.15);color:var(--acc)}
  .u .meta{color:var(--muted);font-size:13px;margin:8px 0}
  .u .acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
  .u .acts button{padding:6px 9px;font-size:13px}
  .u .acts button.danger{background:rgba(239,68,68,.15);color:var(--bad);border-color:rgba(239,68,68,.35)}
  .hidden{display:none}
  .muted{color:var(--muted)}
  .charts{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:16px}
  @media(max-width:760px){.charts{grid-template-columns:1fr}}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
  .panel h3{margin:0 0 12px;font-size:14px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
  .bar-row{display:flex;align-items:center;gap:10px;margin:10px 0}
  .bar-row .lbl{width:74px;font-size:13px;color:var(--muted);flex-shrink:0}
  .bar-row .track{flex:1;height:10px;background:var(--card2);border-radius:6px;overflow:hidden}
  .bar-row .fill{height:100%;border-radius:6px}
  .bar-row .val{width:28px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums}
  .top-list{list-style:none;margin:0;padding:0}
  .top-list li{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--line)}
  .top-list li:last-child{border-bottom:none}
  .top-list .rank{width:22px;height:22px;border-radius:50%;background:var(--card2);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}
  .top-list li:nth-child(1) .rank{background:#f5b301;color:#1a1400}
  .top-list li:nth-child(2) .rank{background:#c6ccd4;color:#1a1400}
  .top-list li:nth-child(3) .rank{background:#c98a4b;color:#1a1400}
  .top-list .tname{flex:1;font-size:14px}
  .top-list .tcount{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
  @media(max-width:560px){header h1{flex-basis:100%}}
</style>
</head>
<body>
<header>
  <h1>🛠 usertgbot · Admin</h1>
  <div class="tabs">
    <div class="tab active" data-tab="metrics">📊 Метрики</div>
    <div class="tab" data-tab="users">👥 Юзери</div>
    <div class="tab" data-tab="referrals">🔗 Реферали</div>
  </div>
</header>
<main>
  <section id="metrics"></section>
  <section id="users" class="hidden">
    <div class="filters">
      <select id="f-tariff"><option value="">Усі тарифи</option><option value="standard">Standard</option><option value="pro">Pro</option><option value="premium">Premium</option></select>
      <select id="f-conn"><option value="">Акаунт: усі</option><option value="1">Підключений</option><option value="0">Ні</option></select>
      <select id="f-block"><option value="">Бан: усі</option><option value="1">Забанені</option><option value="0">Активні</option></select>
      <input id="f-after" type="date" title="Приєднався від">
      <button class="pri" onclick="loadUsers()">Застосувати</button>
    </div>
    <div id="user-cards" class="cards"></div>
  </section>
  <section id="referrals" class="hidden">
    <div class="filters">
      <input id="r-code" placeholder="код (напр. youtube)">
      <input id="r-label" placeholder="назва (опційно)">
      <input id="r-discount" type="number" min="0" max="100" value="0" title="знижка %" style="width:90px">
      <button class="pri" onclick="createReferral()">Створити посилання</button>
    </div>
    <div id="referral-list"></div>
  </section>
</main>
<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const BOT_USERNAME = "__BOT_USERNAME__";
const H = {'X-Token': TOKEN};
async function api(path, opts={}){ opts.headers = Object.assign({}, H, opts.headers||{}); const r = await fetch(path, opts); if(!r.ok) throw new Error(r.status); return r.headers.get('content-type','').includes('json')?r.json():r.text(); }

document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  const tab=t.dataset.tab;
  document.getElementById('metrics').classList.toggle('hidden', tab!=='metrics');
  document.getElementById('users').classList.toggle('hidden', tab!=='users');
  document.getElementById('referrals').classList.toggle('hidden', tab!=='referrals');
  if(tab==='users') loadUsers(); else if(tab==='referrals') loadReferrals(); else loadMetrics();
});

function stat(n,l){return `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`}

function tariffBars(bt){
  const rows = [['Standard',bt.standard||0,'var(--acc)'],['Pro',bt.pro||0,'#8b5cf6'],['Premium',bt.premium||0,'var(--warn)']];
  const max = Math.max(1, ...rows.map(r=>r[1]));
  return rows.map(([label,n,color])=>`
    <div class="bar-row">
      <div class="lbl">${label}</div>
      <div class="track"><div class="fill" style="width:${(n/max*100)}%;background:${color}"></div></div>
      <div class="val">${n}</div>
    </div>`).join('');
}

function activityChart(series){
  if(!series || !series.length) return '<p class="muted">Немає даних.</p>';
  const W=520,H=140,PAD=24;
  const counts = series.map(p=>p.count);
  const max = Math.max(1, ...counts);
  const stepX = (W-PAD*2)/Math.max(1,series.length-1);
  const pts = series.map((p,i)=>{
    const x = PAD + i*stepX;
    const y = H-PAD - (p.count/max)*(H-PAD*2);
    return [x,y];
  });
  const line = pts.map((p,i)=> (i===0?'M':'L')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
  const area = line + ` L${pts[pts.length-1][0].toFixed(1)},${H-PAD} L${pts[0][0].toFixed(1)},${H-PAD} Z`;
  const dots = pts.map((p,i)=>`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="3" fill="var(--acc)"><title>${series[i].date}: ${series[i].count}</title></circle>`).join('');
  const labels = series.map((p,i)=>`<text x="${pts[i][0].toFixed(1)}" y="${H-6}" font-size="10" fill="var(--muted)" text-anchor="middle">${p.date.slice(5)}</text>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" preserveAspectRatio="xMidYMid meet">
    <path d="${area}" fill="var(--acc)" opacity="0.12"></path>
    <path d="${line}" fill="none" stroke="var(--acc)" stroke-width="2"></path>
    ${dots}${labels}
  </svg>`;
}

function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function topUsersList(top){
  if(!top || !top.length) return '<p class="muted">Сьогодні ще немає активності.</p>';
  return '<ol class="top-list" style="list-style:none;padding:0;margin:0">'+top.map((u,i)=>{
    const name = esc(u.full_name || (u.username?('@'+u.username):('id'+u.telegram_id)));
    return `<li><div class="rank">${i+1}</div><div class="tname">${name}</div><div class="tcount">${u.count} подій</div></li>`;
  }).join('')+'</ol>';
}

async function loadMetrics(){
  try{
    const m = await api('/api/metrics');
    const bt = m.by_tariff||{};
    document.getElementById('metrics').innerHTML = '<div class="grid">'+
      stat(m.total_users,'Користувачів')+
      stat(m.active_subscriptions,'Активних підписок')+
      stat(bt.standard||0,'Standard')+
      stat(bt.pro||0,'Pro')+
      stat(bt.premium||0,'Premium')+
      stat(m.connected_accounts,'Підключених акаунтів')+
      stat(m.blocked_users,'Забанених')+
      stat(m.check_used_total,'.check використано')+
      stat(m.saved_messages,'Збережених повідомлень')+
      '</div>'+
      '<div class="charts">'+
        '<div class="panel"><h3>Активність за 7 днів</h3>'+activityChart(m.activity_7d)+'</div>'+
        '<div class="panel"><h3>Топ юзерів за сьогодні</h3>'+topUsersList(m.top_users_today)+'</div>'+
      '</div>'+
      '<div class="panel" style="margin-top:14px"><h3>Розподіл по тарифах</h3>'+tariffBars(bt)+'</div>';
  }catch(e){ document.getElementById('metrics').innerHTML='<p class="muted">Помилка доступу (перевірте token).</p>'; }
}

function ucard(u){
  const sub = u.tariff ? `<span class="badge pro">${esc(u.tariff)}${u.expires_at?(' до '+esc(u.expires_at)):''}</span>` : '<span class="badge">без підписки</span>';
  const conn = u.connected ? '<span class="badge ok">акаунт ✓</span>' : '<span class="badge">акаунт ✗</span>';
  const blk = u.is_blocked ? '<span class="badge bad">бан</span>' : '';
  const ref = u.referral_code ? `<span class="badge">🔗 ${esc(u.referral_code)}</span>` : '';
  const name = esc(u.full_name||u.username||u.telegram_id);
  const uname = u.username?('@'+esc(u.username)+' · '):'';
  return `<div class="u">
    <div class="top"><div><div class="name">${name}</div><div class="id">${uname}${Number(u.telegram_id)}</div></div><div>${blk}</div></div>
    <div class="meta">Приєднався: ${esc(u.joined||'—')}</div>
    <div>${sub} ${conn} ${ref}</div>
    <div class="acts">
      <button onclick="grant(${u.telegram_id},'standard')">+Std</button>
      <button onclick="grant(${u.telegram_id},'pro')">+Pro</button>
      <button onclick="grant(${u.telegram_id},'premium')">+Premium</button>
      <button onclick="act(${u.telegram_id},'revoke')">−Підписку</button>
      ${u.is_blocked?`<button onclick="act(${u.telegram_id},'unblock')">Розбан</button>`:`<button onclick="act(${u.telegram_id},'block')">Бан</button>`}
      <button class="danger" onclick="delUser(${Number(u.telegram_id)})">🗑 Видалити</button>
    </div>
  </div>`;
}
async function loadUsers(){
  const q = new URLSearchParams();
  const t=document.getElementById('f-tariff').value; if(t) q.set('tariff',t);
  const c=document.getElementById('f-conn').value; if(c) q.set('connected',c);
  const b=document.getElementById('f-block').value; if(b) q.set('blocked',b);
  const a=document.getElementById('f-after').value; if(a) q.set('joined_after',a);
  try{
    const rows = await api('/api/users?'+q.toString());
    document.getElementById('user-cards').innerHTML = rows.length? rows.map(ucard).join('') : '<p class="muted">Нічого не знайдено.</p>';
  }catch(e){ document.getElementById('user-cards').innerHTML='<p class="muted">Помилка доступу.</p>'; }
}
async function grant(id,tariff){ await api(`/api/users/${id}/grant?tariff=${tariff}&days=30`,{method:'POST'}); loadUsers(); }
async function act(id,what){ await api(`/api/users/${id}/${what}`,{method:'POST'}); loadUsers(); }
async function delUser(id){
  if(!confirm(`Видалити користувача ${id} назавжди? Це прибере підписки, сесії, налаштування — все. Дію не можна скасувати.`)) return;
  try{
    await api(`/api/users/${id}/delete`,{method:'POST'});
    loadUsers();
  }catch(e){ alert('Не вдалося видалити: '+e.message); }
}

function rcard(r){
  const tariffs = Object.keys(r.by_tariff||{}).length
    ? Object.entries(r.by_tariff).map(([t,n])=>`${esc(t)}: ${n}`).join(', ')
    : '—';
  const link = BOT_USERNAME ? `https://t.me/${BOT_USERNAME}?start=ref_${encodeURIComponent(r.code)}` : `?start=ref_${encodeURIComponent(r.code)}`;
  const cid = 'r-' + esc(r.code).replace(/[^a-zA-Z0-9_-]/g,'_');
  return `<div class="u">
    <div class="top"><div><div class="name">${esc(r.code)}</div><div class="id">${esc(r.label||'')}</div></div></div>
    <div class="meta">Переходів: ${r.clicks} · Куплено: ${tariffs}</div>
    <div class="meta" style="display:flex;gap:6px;align-items:center">
      <code id="${cid}-link" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${esc(link)}</code>
      <button onclick="copyReferralLink('${cid}-link')">📋</button>
    </div>
    <div class="acts">
      <span class="muted">Знижка:</span>
      <input id="${cid}-disc" type="number" min="0" max="100" value="${r.discount_percent}" style="width:70px">
      <button onclick="updateDiscount('${esc(r.code)}','${cid}-disc')">Зберегти</button>
    </div>
  </div>`;
}
function copyReferralLink(id){
  const text = document.getElementById(id).textContent;
  navigator.clipboard?.writeText(text).catch(()=>{});
}
async function updateDiscount(code, inputId){
  const value = document.getElementById(inputId).value || 0;
  try{
    await api(`/api/referrals/${encodeURIComponent(code)}/discount?discount_percent=${value}`,{method:'POST'});
    loadReferrals();
  }catch(e){ alert('Не вдалося оновити знижку: '+e.message); }
}
async function loadReferrals(){
  try{
    const rows = await api('/api/referrals');
    document.getElementById('referral-list').innerHTML = rows.length
      ? '<div class="cards">'+rows.map(rcard).join('')+'</div>'
      : '<p class="muted">Ще немає реферальних посилань.</p>';
  }catch(e){ document.getElementById('referral-list').innerHTML='<p class="muted">Помилка доступу.</p>'; }
}
async function createReferral(){
  const code = document.getElementById('r-code').value.trim();
  const label = document.getElementById('r-label').value.trim();
  const discount = document.getElementById('r-discount').value || 0;
  if(!code){ alert('Вкажіть код посилання'); return; }
  const q = new URLSearchParams({code, discount_percent: discount});
  if(label) q.set('label', label);
  try{
    await api(`/api/referrals?${q.toString()}`,{method:'POST'});
    document.getElementById('r-code').value='';
    document.getElementById('r-label').value='';
    document.getElementById('r-discount').value='0';
    loadReferrals();
  }catch(e){ alert('Не вдалося створити: '+e.message); }
}

loadMetrics();
</script>
</body>
</html>"""
