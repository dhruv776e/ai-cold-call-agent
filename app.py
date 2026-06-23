from flask import Flask, request, Response, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import OpenAI
import os, json, re
from datetime import datetime

app = Flask(__name__)

# ── clients ──────────────────────────────────────────────────────────────────
twilio_client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

TWILIO_NUMBER   = os.environ["TWILIO_PHONE_NUMBER"]   # e.g. +12345678900
YOUR_NAME       = os.environ.get("YOUR_NAME", "Rahul")
YOUR_COMPANY    = os.environ.get("YOUR_COMPANY", "WebPro Solutions")
SERVER_URL      = os.environ["SERVER_URL"]             # e.g. https://your-app.onrender.com

# ── in-memory call state (resets on redeploy — fine for small volume) ─────────
call_states = {}   # call_sid -> { history: [], phone: str, business_name: str, status: str }
call_log    = []   # list of dicts for dashboard

# ── system prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are {YOUR_NAME}, a friendly and professional website & SEO consultant from {YOUR_COMPANY}.

You are calling local Indian business owners to offer website creation and SEO services.

YOUR GOAL:
- Introduce yourself briefly
- Ask if they currently have a website
- If NO website → pitch website building (₹3,000–₹8,000, ready in 2–3 days)
- If YES website → pitch SEO (help them rank higher on Google and get more customers)
- Handle objections calmly
- Try to book a callback or get their interest
- Keep responses SHORT (2–3 sentences max) — this is a phone call, not an essay

PITCH POINTS:
Website pitch: "A professional website helps customers find you on Google 24/7. We build it in just 2–3 days at a very affordable price. Can I send you a sample?"
SEO pitch: "Having a website is great, but if customers can't find you on Google, you're losing business to competitors. We help you rank on the first page so more people call you directly."

RULES:
- Be friendly, not pushy
- Speak in clear simple English
- If they say not interested, thank them politely and say goodbye
- If they say call later / busy, ask for best time and say goodbye
- NEVER make up prices beyond ₹3,000–₹8,000 for websites or ₹2,000–₹5,000/month for SEO
- Keep each response under 40 words — phone call, keep it natural

START the call with: "Hello, am I speaking with someone from [business name]? This is {YOUR_NAME} from {YOUR_COMPANY}. I'm calling about a quick opportunity to help grow your business online. Do you have 30 seconds?"
"""

def get_ai_response(call_sid, user_input, business_name="your business"):
    """Get GPT response for the conversation turn."""
    state = call_states.get(call_sid, {"history": [], "business_name": business_name})
    history = state["history"]

    # inject business name into system prompt
    system = SYSTEM_PROMPT.replace("[business name]", business_name)

    messages = [{"role": "system", "content": system}] + history + \
               [{"role": "user", "content": user_input}]

    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        max_tokens=120,
        temperature=0.7,
    )
    ai_text = resp.choices[0].message.content.strip()

    # update history
    history.append({"role": "user",    "content": user_input})
    history.append({"role": "assistant","content": ai_text})
    state["history"] = history[-20:]  # keep last 20 turns
    call_states[call_sid] = state

    return ai_text

def twiml_say_and_gather(text, action_url, hint=""):
    """Return TwiML that speaks text then listens."""
    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        action=action_url,
        method="POST",
        timeout=5,
        speech_timeout="auto",
        language="en-IN",
    )
    gather.say(text, voice="Polly.Raveena", language="en-IN")
    resp.append(gather)
    # fallback if no speech detected
    resp.say("I didn't catch that. Let me try again.", voice="Polly.Raveena")
    resp.redirect(action_url, method="POST")
    return resp

# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/api/calls", methods=["GET"])
def get_calls():
    return jsonify(call_log)

@app.route("/api/make-call", methods=["POST"])
def make_call():
    """Trigger a call to a single number."""
    data = request.json
    phone   = data.get("phone", "").strip()
    biz     = data.get("business_name", "your business").strip()

    if not phone:
        return jsonify({"error": "Phone number required"}), 400

    try:
        call = twilio_client.calls.create(
            to=phone,
            from_=TWILIO_NUMBER,
            url=f"{SERVER_URL}/voice/start?biz={biz.replace(' ', '+')}",
            method="POST",
            status_callback=f"{SERVER_URL}/voice/status",
            status_callback_method="POST",
        )
        call_log.append({
            "call_sid": call.sid,
            "phone": phone,
            "business_name": biz,
            "status": "initiated",
            "started_at": datetime.now().strftime("%H:%M:%S"),
            "outcome": "—",
            "transcript": [],
        })
        return jsonify({"success": True, "call_sid": call.sid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/bulk-call", methods=["POST"])
def bulk_call():
    """Trigger calls to multiple numbers with delay."""
    data     = request.json
    numbers  = data.get("numbers", [])   # list of {phone, business_name}
    results  = []
    for item in numbers[:50]:            # cap at 50 per batch
        phone = item.get("phone", "").strip()
        biz   = item.get("business_name", "your business").strip()
        if not phone:
            continue
        try:
            call = twilio_client.calls.create(
                to=phone,
                from_=TWILIO_NUMBER,
                url=f"{SERVER_URL}/voice/start?biz={biz.replace(' ', '+')}",
                method="POST",
                status_callback=f"{SERVER_URL}/voice/status",
                status_callback_method="POST",
            )
            call_log.append({
                "call_sid": call.sid, "phone": phone, "business_name": biz,
                "status": "initiated", "started_at": datetime.now().strftime("%H:%M:%S"),
                "outcome": "—", "transcript": [],
            })
            results.append({"phone": phone, "call_sid": call.sid, "success": True})
        except Exception as e:
            results.append({"phone": phone, "error": str(e), "success": False})
    return jsonify({"results": results})

@app.route("/voice/start", methods=["POST"])
def voice_start():
    """Called by Twilio when the call connects."""
    call_sid = request.form.get("CallSid", "")
    biz      = request.args.get("biz", "your business").replace("+", " ")

    call_states[call_sid] = {"history": [], "business_name": biz, "phone": request.form.get("To",""), "status": "in-progress"}

    opening = (
        f"Hello, am I speaking with someone from {biz}? "
        f"This is {YOUR_NAME} from {YOUR_COMPANY}. "
        "I'm calling about a quick opportunity to help grow your business online. "
        "Do you have 30 seconds?"
    )

    resp = twiml_say_and_gather(opening, f"{SERVER_URL}/voice/respond")
    return Response(str(resp), mimetype="text/xml")

@app.route("/voice/respond", methods=["POST"])
def voice_respond():
    """Handle each speech turn."""
    call_sid    = request.form.get("CallSid", "")
    user_speech = request.form.get("SpeechResult", "").strip()
    biz         = call_states.get(call_sid, {}).get("business_name", "your business")

    if not user_speech:
        user_speech = "[silence]"

    # update transcript in log
    for entry in call_log:
        if entry["call_sid"] == call_sid:
            entry["transcript"].append({"role": "caller", "text": user_speech})
            break

    ai_reply = get_ai_response(call_sid, user_speech, biz)

    # detect end-of-call signals
    end_phrases = ["bye", "goodbye", "not interested", "remove", "don't call",
                   "call later", "busy", "no thank you", "no thanks"]
    is_ending = any(p in ai_reply.lower() for p in ["goodbye", "thank you for your time", "have a great day"])
    is_ending = is_ending or any(p in user_speech.lower() for p in end_phrases)

    for entry in call_log:
        if entry["call_sid"] == call_sid:
            entry["transcript"].append({"role": "agent", "text": ai_reply})
            if is_ending:
                entry["outcome"] = "Completed"
            break

    if is_ending:
        resp = VoiceResponse()
        resp.say(ai_reply, voice="Polly.Raveena", language="en-IN")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")

    resp = twiml_say_and_gather(ai_reply, f"{SERVER_URL}/voice/respond")
    return Response(str(resp), mimetype="text/xml")

@app.route("/voice/status", methods=["POST"])
def voice_status():
    """Twilio status callback."""
    call_sid    = request.form.get("CallSid", "")
    call_status = request.form.get("CallStatus", "")
    for entry in call_log:
        if entry["call_sid"] == call_sid:
            entry["status"] = call_status
            if call_status in ("no-answer", "busy", "failed"):
                entry["outcome"] = call_status.replace("-", " ").title()
            break
    return Response("", status=204)

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
