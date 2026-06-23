import os
import re
from datetime import datetime
from flask import Flask, request, Response, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import OpenAI

app = Flask(__name__)

# ── env vars (all optional at startup, checked at call time) ──────────────────
def get_twilio():
    sid   = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        raise ValueError("Twilio credentials missing in environment variables")
    return Client(sid, token)

def get_openai():
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY missing in environment variables")
    return OpenAI(api_key=key)

def TWILIO_NUM():  return os.environ.get("TWILIO_PHONE_NUMBER", "")
def SERVER():      return os.environ.get("SERVER_URL", "").rstrip("/")
def AGENT_NAME():  return os.environ.get("YOUR_NAME", "Rahul")
def COMPANY():     return os.environ.get("YOUR_COMPANY", "WebPro Solutions")

# ── in-memory state ───────────────────────────────────────────────────────────
call_states = {}
call_log    = []

# ── AI ────────────────────────────────────────────────────────────────────────
def build_system_prompt():
    return f"""You are {AGENT_NAME()}, a friendly website and SEO consultant from {COMPANY()}.
You call Indian business owners to offer website creation and SEO services.

GOAL:
1. Greet them and ask if they have a website
2. No website -> pitch website (3000-8000 rupees, ready in 2-3 days)
3. Has website -> pitch SEO (rank higher on Google, more customers call them)
4. Handle objections politely
5. Try to book a callback

STRICT RULES:
- Keep every reply under 35 words
- Be friendly, never pushy
- If not interested, say thank you and goodbye
- If busy, ask for best time and say goodbye
- Never invent prices outside the ranges above"""

def get_ai_reply(call_sid, user_text, biz):
    state = call_states.setdefault(call_sid, {"history": [], "biz": biz})
    history = state["history"]
    messages = [{"role": "system", "content": build_system_prompt()}]
    messages += history
    messages.append({"role": "user", "content": user_text})
    try:
        res = get_openai().chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )
        reply = res.choices[0].message.content.strip()
    except Exception as e:
        reply = "I'm sorry, I'm having a technical issue. I'll call you back later. Thank you!"
    history.append({"role": "user",      "content": user_text})
    history.append({"role": "assistant", "content": reply})
    state["history"] = history[-16:]
    return reply

# ── TwiML helpers ─────────────────────────────────────────────────────────────
def speak_and_listen(text, next_url):
    r = VoiceResponse()
    g = Gather(input="speech", action=next_url, method="POST",
               timeout=6, speech_timeout="auto", language="en-IN")
    g.say(text, voice="Polly.Raveena", language="en-IN")
    r.append(g)
    r.say("I did not catch that, goodbye for now.", voice="Polly.Raveena")
    r.hangup()
    return r

def is_farewell(text):
    words = ["goodbye","thank you for your time","have a great day",
             "not interested","do not call","don't call","remove me"]
    return any(w in text.lower() for w in words)

def caller_wants_to_end(text):
    words = ["bye","goodbye","not interested","busy","call later",
             "no thank you","no thanks","remove","don't call","do not call"]
    return any(w in text.lower() for w in words)

# ── HTML dashboard (embedded) ─────────────────────────────────────────────────
DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Cold Call Agent</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',sans-serif;background:#0f0f1a;color:#e0e0e0;min-height:100vh}
header{background:linear-gradient(135deg,#1a1a2e,#16213e);padding:20px 28px;border-bottom:1px solid #2a2a4a;display:flex;align-items:center;gap:12px}
header h1{font-size:20px;font-weight:700;color:#fff}
header p{font-size:12px;color:#888}
.stats{display:flex;gap:14px;padding:20px 28px;flex-wrap:wrap}
.sc{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:16px 22px;flex:1;min-width:130px}
.sc .l{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px}
.sc .v{font-size:30px;font-weight:700;color:#7c6fff;margin-top:4px}
.main{display:flex;gap:18px;padding:0 28px 28px;flex-wrap:wrap}
.panel{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:14px;padding:22px}
.panel h2{font-size:15px;font-weight:600;color:#fff;margin-bottom:16px}
.lp{flex:0 0 320px;display:flex;flex-direction:column;gap:18px}
.rp{flex:1;min-width:280px}
label{display:block;font-size:11px;color:#aaa;margin-bottom:5px;margin-top:12px}
label:first-of-type{margin-top:0}
input,textarea{width:100%;background:#0f0f1a;border:1px solid #333;border-radius:8px;padding:9px 13px;color:#e0e0e0;font-size:13px;outline:none}
input:focus,textarea:focus{border-color:#7c6fff}
textarea{resize:vertical;min-height:90px;font-family:monospace;font-size:11px}
.btn{display:inline-flex;align-items:center;gap:7px;padding:10px 20px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;transition:opacity .2s;width:100%;justify-content:center;margin-top:12px}
.btn:hover{opacity:.85}
.bp{background:#7c6fff;color:#fff}
.bb{background:#1e7e34;color:#fff}
.br{background:#2a2a4a;color:#ccc;font-size:11px;padding:6px 13px;width:auto;margin-top:0;float:right}
.hint{font-size:10px;color:#555;margin-top:5px}
.tw{overflow-x:auto;margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:9px 11px;color:#777;font-weight:500;font-size:10px;text-transform:uppercase;border-bottom:1px solid #2a2a4a}
td{padding:10px 11px;border-bottom:1px solid #1e1e30;vertical-align:top}
tr:last-child td{border-bottom:none}
tr:hover td{background:#1e1e30}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:600}
.bi{background:#2a2a4a;color:#aaa}
.bp2{background:#1a3a5c;color:#5bc0eb}
.bc{background:#1a3a1a;color:#5cb85c}
.bn{background:#3a1a1a;color:#e07070}
.tbtn{background:#2a2a4a;border:none;color:#aaa;padding:3px 9px;border-radius:5px;cursor:pointer;font-size:10px}
.tbtn:hover{background:#3a3a5a;color:#fff}
.mbg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);align-items:center;justify-content:center;z-index:100}
.mbg.open{display:flex}
.modal{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:14px;padding:26px;width:90%;max-width:500px;max-height:78vh;overflow-y:auto}
.modal h3{font-size:15px;color:#fff;margin-bottom:14px}
.mc{float:right;background:none;border:none;color:#888;font-size:20px;cursor:pointer}
.turn{margin-bottom:12px}
.turn .role{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px}
.turn.agent .role{color:#7c6fff}
.turn.caller .role{color:#5cb85c}
.turn .text{font-size:12px;color:#ccc;line-height:1.5}
.toast{position:fixed;bottom:20px;right:20px;background:#7c6fff;color:#fff;padding:10px 20px;border-radius:9px;font-size:13px;font-weight:600;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
.empty{text-align:center;padding:40px 0;color:#444;font-size:13px}
</style>
</head>
<body>
<header>
  <div>
    <h1>📞 AI Cold Call Agent</h1>
    <p>Website &amp; SEO Sales — Automated</p>
  </div>
</header>
<div class="stats">
  <div class="sc"><div class="l">Total</div><div class="v" id="s0">0</div></div>
  <div class="sc"><div class="l">Answered</div><div class="v" id="s1">0</div></div>
  <div class="sc"><div class="l">Completed</div><div class="v" id="s2">0</div></div>
  <div class="sc"><div class="l">No Answer</div><div class="v" id="s3">0</div></div>
</div>
<div class="main">
  <div class="lp">
    <div class="panel">
      <h2>📲 Single Call</h2>
      <label>Phone Number (+91...)</label>
      <input id="ph" placeholder="+919876543210">
      <label>Business Name</label>
      <input id="bn" placeholder="e.g. Sunshine Gym">
      <button class="btn bp" onclick="singleCall()">▶ Start Call</button>
    </div>
    <div class="panel">
      <h2>🚀 Bulk Calls</h2>
      <label>One per line: +91XXXXXXXXXX,Business Name</label>
      <textarea id="bulk" placeholder="+919876543210,Sunshine Gym&#10;+919123456789,Star Preschool&#10;+919000111222,ABC Real Estate"></textarea>
      <p class="hint">Max 50 numbers per batch</p>
      <button class="btn bb" onclick="bulkCall()">🚀 Start Bulk Calls</button>
    </div>
  </div>
  <div class="rp panel">
    <h2>📋 Call Log <button class="btn br" onclick="loadCalls()">↻ Refresh</button></h2>
    <div class="tw">
      <table>
        <thead><tr><th>Business</th><th>Phone</th><th>Status</th><th>Outcome</th><th>Time</th><th>Log</th></tr></thead>
        <tbody id="tb"><tr><td colspan="6" class="empty">No calls yet — start one!</td></tr></tbody>
      </table>
    </div>
  </div>
</div>
<div class="mbg" id="mbg" onclick="closeMod(event)">
  <div class="modal">
    <button class="mc" onclick="closeMod()">×</button>
    <h3 id="mtitle">Transcript</h3>
    <div id="mbody"></div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
let data=[];
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),3000)}
async function singleCall(){
  const ph=document.getElementById('ph').value.trim();
  const bn=document.getElementById('bn').value.trim();
  if(!ph){toast('Enter phone number');return}
  const r=await fetch('/api/make-call',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:ph,business_name:bn||'your business'})});
  const d=await r.json();
  if(d.success){toast('✅ Call started!');loadCalls()}else toast('❌ '+(d.error||'Error'))
}
async function bulkCall(){
  const raw=document.getElementById('bulk').value.trim();
  if(!raw){toast('Paste numbers first');return}
  const nums=raw.split('\\n').map(l=>{const[p,...rest]=l.split(',');return{phone:p.trim(),business_name:rest.join(',').trim()||'your business'}}).filter(n=>n.phone);
  if(!nums.length){toast('No valid numbers');return}
  toast('📞 Starting '+nums.length+' calls...');
  const r=await fetch('/api/bulk-call',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({numbers:nums})});
  const d=await r.json();
  const ok=d.results.filter(x=>x.success).length;
  toast('✅ '+ok+'/'+nums.length+' calls initiated');
  loadCalls();
}
async function loadCalls(){
  const r=await fetch('/api/calls');
  data=await r.json();
  document.getElementById('s0').textContent=data.length;
  document.getElementById('s1').textContent=data.filter(c=>['in-progress','completed'].includes(c.status)).length;
  document.getElementById('s2').textContent=data.filter(c=>c.outcome==='Completed').length;
  document.getElementById('s3').textContent=data.filter(c=>c.status==='no-answer').length;
  const tb=document.getElementById('tb');
  if(!data.length){tb.innerHTML='<tr><td colspan="6" class="empty">No calls yet.</td></tr>';return}
  tb.innerHTML=[...data].reverse().map((c,i)=>`<tr>
    <td><strong>${c.business_name}</strong></td>
    <td style="font-family:monospace">${c.phone}</td>
    <td><span class="badge ${c.status==='initiated'?'bi':c.status==='in-progress'?'bp2':c.status==='completed'?'bc':'bn'}">${c.status}</span></td>
    <td>${c.outcome}</td><td>${c.started_at}</td>
    <td>${c.transcript&&c.transcript.length?`<button class="tbtn" onclick="showT(${data.length-1-i})">View</button>`:'—'}</td>
  </tr>`).join('');
}
function showT(i){
  const c=data[i];
  document.getElementById('mtitle').textContent='Transcript — '+c.business_name;
  document.getElementById('mbody').innerHTML=c.transcript.map(t=>`<div class="turn ${t.role}"><div class="role">${t.role==='agent'?'🤖 Agent':'👤 Caller'}</div><div class="text">${t.text}</div></div>`).join('')||'<p style="color:#555">Empty</p>';
  document.getElementById('mbg').classList.add('open');
}
function closeMod(e){if(!e||e.target===document.getElementById('mbg'))document.getElementById('mbg').classList.remove('open')}
loadCalls();
setInterval(loadCalls,8000);
</script>
</body>
</html>"""

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return Response(DASHBOARD, mimetype="text/html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/api/calls")
def api_calls():
    return jsonify(call_log)

@app.route("/api/make-call", methods=["POST"])
def api_make_call():
    data  = request.get_json(force=True) or {}
    phone = data.get("phone", "").strip()
    biz   = data.get("business_name", "your business").strip()
    if not phone:
        return jsonify({"error": "phone required"}), 400
    try:
        call = get_twilio().calls.create(
            to=phone,
            from_=TWILIO_NUM(),
            url=f"{SERVER()}/voice/start?biz={biz.replace(' ','+')}",
            method="POST",
            status_callback=f"{SERVER()}/voice/status",
            status_callback_method="POST",
        )
        call_log.append({"call_sid": call.sid, "phone": phone, "business_name": biz,
                         "status": "initiated", "started_at": datetime.now().strftime("%H:%M:%S"),
                         "outcome": "—", "transcript": []})
        return jsonify({"success": True, "call_sid": call.sid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/bulk-call", methods=["POST"])
def api_bulk_call():
    data    = request.get_json(force=True) or {}
    numbers = data.get("numbers", [])[:50]
    results = []
    for item in numbers:
        phone = item.get("phone", "").strip()
        biz   = item.get("business_name", "your business").strip()
        if not phone:
            continue
        try:
            call = get_twilio().calls.create(
                to=phone,
                from_=TWILIO_NUM(),
                url=f"{SERVER()}/voice/start?biz={biz.replace(' ','+')}",
                method="POST",
                status_callback=f"{SERVER()}/voice/status",
                status_callback_method="POST",
            )
            call_log.append({"call_sid": call.sid, "phone": phone, "business_name": biz,
                             "status": "initiated", "started_at": datetime.now().strftime("%H:%M:%S"),
                             "outcome": "—", "transcript": []})
            results.append({"phone": phone, "call_sid": call.sid, "success": True})
        except Exception as e:
            results.append({"phone": phone, "error": str(e), "success": False})
    return jsonify({"results": results})

@app.route("/voice/start", methods=["POST"])
def voice_start():
    sid = request.form.get("CallSid", "")
    biz = request.args.get("biz", "your business").replace("+", " ")
    call_states[sid] = {"history": [], "biz": biz}
    opening = (f"Hello! Am I speaking with someone from {biz}? "
               f"This is {AGENT_NAME()} from {COMPANY()}. "
               "I am calling about a quick opportunity to grow your business online. "
               "Do you have 30 seconds?")
    r = speak_and_listen(opening, f"{SERVER()}/voice/respond")
    return Response(str(r), mimetype="text/xml")

@app.route("/voice/respond", methods=["POST"])
def voice_respond():
    sid    = request.form.get("CallSid", "")
    speech = request.form.get("SpeechResult", "").strip() or "[no response]"
    biz    = call_states.get(sid, {}).get("biz", "your business")

    for e in call_log:
        if e["call_sid"] == sid:
            e["transcript"].append({"role": "caller", "text": speech})
            break

    ending = caller_wants_to_end(speech)
    reply  = get_ai_reply(sid, speech, biz)
    ending = ending or is_farewell(reply)

    for e in call_log:
        if e["call_sid"] == sid:
            e["transcript"].append({"role": "agent", "text": reply})
            if ending:
                e["outcome"] = "Completed"
            break

    if ending:
        r = VoiceResponse()
        r.say(reply, voice="Polly.Raveena", language="en-IN")
        r.hangup()
        return Response(str(r), mimetype="text/xml")

    r = speak_and_listen(reply, f"{SERVER()}/voice/respond")
    return Response(str(r), mimetype="text/xml")

@app.route("/voice/status", methods=["POST"])
def voice_status():
    sid    = request.form.get("CallSid", "")
    status = request.form.get("CallStatus", "")
    for e in call_log:
        if e["call_sid"] == sid:
            e["status"] = status
            if status in ("no-answer", "busy", "failed", "canceled"):
                e["outcome"] = status.replace("-", " ").title()
            break
    return Response("", status=204)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
