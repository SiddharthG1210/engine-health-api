# How to Use Every Feature — Engine Health App

This is a plain-language walkthrough of everything the app can do. No technical
background needed. Each item below is a separate feature — read them in order,
or jump straight to the one you want.

**Before you start**, the app needs to be running. Open a terminal in the
project folder and run:

```
uvicorn app.main:app --reload
```

Then open your browser to **http://127.0.0.1:8000/**. Leave that terminal
window open the whole time you're using the app — closing it shuts the app
down.

**One thing to know up front**: the AI assistant this app talks to has a
limit — roughly **5 to 7 questions per minute**. If you ask questions too
quickly, it will reply "The assistant is busy, try again in a few seconds."
That's not broken — it's the limit doing its job. Just wait a moment and ask
again.

---

## The full list

1. Signing in
2. Signing out
3. (Engineer) Checking one engine's remaining lifespan
4. (Engineer) Checking if an engine is behaving abnormally
5. (Engineer) Checking an engine's overall health stage
6. (Engineer) Getting a full report on one engine, all at once
7. (Engineer) Asking about several engines in one message
8. (Engineer) Reading the "What I checked" panel
9. (Customer) Checking how your engine is doing
10. (Customer) Checking on several engines you own
11. (Customer) What happens if you ask about an engine that isn't yours
12. Continuing a conversation naturally
13. What happens if the app restarts
14. What to do when you see "the assistant is busy"
15. Example questions, by account type

---

## 1. Signing in

This is always your first step.

1. Go to **http://127.0.0.1:8000/** in your browser.
2. You'll see a sign-in box asking for an email and a password.
3. Type in one of the demo accounts. Every demo account uses the same
   password: **demo1234**
   - `engineer@demo.local` — sees every engine in the fleet, full technical
     detail.
   - `customer1@demo.local` — owns one engine, sees plain-language answers
     only.
   - `customer4@demo.local` — owns two engines, good for trying out
     multi-engine questions as a customer.
4. Click **Sign in**.
5. You'll land on the chat screen. Your email and your role ("engineer" or
   "customer") show in the top corner, so you always know which account
   you're using.

If you type the wrong password, or an email that doesn't exist, you'll see
the same message either way — "Incorrect email or password." That's
deliberate: it stops anyone from guessing which email addresses are real.

---

## 2. Signing out

1. Click **Log out** in the top-right corner of the chat screen.
2. You're dropped back to the sign-in box.

Nothing about your account is lost — this just forgets your sign-in on this
browser. You can sign back in any time.

---

## 3. (Engineer) Checking one engine's remaining lifespan

Sign in as `engineer@demo.local`. Then, in the message box, ask something
like:

> What is the RUL of engine 34?

("RUL" stands for **Remaining Useful Life** — how many more operating cycles
the engine is predicted to have left before it needs servicing.)

You'll get back a plain-text prediction, in cycles, for that specific engine.
This is the app's core prediction — everything else builds on it.

---

## 4. (Engineer) Checking if an engine is behaving abnormally

Still as the engineer, ask:

> Is engine 34 showing any anomalies?

This checks the engine's most recent reading against what "normal healthy
behavior" looks like. You'll be told whether it looks normal or unusual, plus
the numbers behind that call.

---

## 5. (Engineer) Checking an engine's overall health stage

Ask:

> What stage is engine 34 in?

You'll get back one of three plain labels:
- **Healthy** — nothing to worry about
- **Warning** — worth keeping an eye on, service coming up
- **Critical** — needs attention soon

This is the same measurement customers see, just delivered with the raw
numbers attached for an engineer.

---

## 6. (Engineer) Getting a full report on one engine, all at once

Ask something like:

> Give me a full status report on engine 97.

Instead of asking three separate questions, one message like this runs all
three checks (lifespan, anomaly check, health stage) at once and gives you
one combined answer.

---

## 7. (Engineer) Asking about several engines in one message

Ask:

> What's the RUL of engine 31 and engine 39?

The app understands multiple engines named in the same message and checks
each one separately — the answer will clearly separate "Engine 31" from
"Engine 39" rather than mixing them together.

---

## 8. (Engineer) Reading the "What I checked" panel

Under every reply you get as an engineer, there's a small collapsible section
labeled **"What I checked"**.

1. Click it to expand.
2. You'll see one line per thing the app actually looked up — which tool ran,
   which engine it ran on, and the result.
3. A line in **green** means it succeeded. A line in **red** means it was
   refused (for example, an engine number that doesn't exist).

This panel exists so you can double-check that the written answer actually
matches what was looked up — nothing is hidden or summarized away.

---

## 9. (Customer) Checking how your engine is doing

Sign in as `customer1@demo.local`. Ask:

> How is my engine doing?

You'll get a short, plain-language answer with no jargon and no engine
numbers — something like "Your engine is running well, nothing needs
attention right now." Customers never see raw numbers; just the practical
takeaway.

---

## 10. (Customer) Checking on several engines you own

Sign in as `customer4@demo.local` (this account owns two engines, called
"Engine A" and "Engine B"). Try:

> How are all my engines doing?

You'll get one reply covering both, each named by its label, and each with
its own verdict — they won't be blended together.

You can also ask about just one:

> How's Engine B doing?

If you own more than one engine and don't say which one, the app will simply
ask you to clarify — for example:

> How's my engine?

→ *"You have Engine A and Engine B — which one would you like me to check?"*

---

## 11. (Customer) What happens if you ask about an engine that isn't yours

Still as `customer4@demo.local`, try asking about an engine number you don't
own, e.g.:

> How is engine 34 doing?

You will **not** get any data back — not your own engine's data, not a
"close guess," nothing. You'll just be told that engine isn't on your
account, and reminded which engines are. This is intentional: naming another
engine never lets you see its information.

---

## 12. Continuing a conversation naturally

You don't have to repeat yourself. If you ask about one engine and then
follow up without naming it again, the app remembers what you were just
talking about — for example:

> What's the RUL of engine 25?
> And what about engine 39?

The second question is understood correctly even though it doesn't repeat
"RUL" or mention which engine explicitly by full sentence.

---

## 13. What happens if the app restarts

If the terminal running `uvicorn` is stopped and started again, your chat
history is wiped — the app will no longer remember anything from before the
restart. This is expected behavior, not a bug: conversations are only kept
in memory while the app is running, not saved anywhere permanent.

If this happens, just keep chatting — a fresh conversation starts working
immediately, you'll just need to give it context again (e.g. name the engine
again instead of relying on "it" or "that one").

---

## 14. What to do when you see "the assistant is busy"

If you ask questions quickly, you may see:

> The assistant is busy, try again in a few seconds.

This means you've hit the free usage limit for the AI behind the app (about
5–7 messages per minute). It is not an error and nothing is broken.

**What to do:** wait about 15–20 seconds, then send your message again. No
need to refresh the page or sign in again — your conversation and sign-in
stay exactly as they were.

---

## 15. Example questions, by account type

You don't need to phrase things in any exact format — the app understands
natural, everyday sentences. Here are examples to get you started, grouped by
which account can ask them.

### As an engineer (`engineer@demo.local`)

You can name any engine in the fleet by its number.

- "What is the RUL of engine 34?"
- "How many cycles does engine 12 have left?"
- "Is engine 34 showing any anomalies?"
- "Does engine 50 look normal, or off in some way?"
- "What stage is engine 34 in — healthy, warning, or critical?"
- "Give me a full status report on engine 97."
- "Tell me everything you know about engine 25."
- "What's the RUL of engine 31 and engine 39?" *(two engines, one message)*
- "Compare engine 10 and engine 11 for me."
- "Which of these is worse off, engine 5 or engine 42?"
- "Check engines 3, 8, and 15 and tell me which ones need attention."

### As a customer with one engine (`customer1@demo.local`,
`customer2@demo.local`, `customer3@demo.local`)

You never need to know or mention an engine number — just talk about "my
engine."

- "How is my engine doing?"
- "Is everything okay with my engine?"
- "Should I be worried about anything?"
- "Do I need to book a service soon?"
- "Any updates on my equipment?"
- "Is my engine healthy?"

### As a customer with more than one engine (`customer4@demo.local`)

You can either ask about all of them together, or name one specifically by
its label (e.g. "Engine A", "Engine B").

- "How are all my engines doing?"
- "Give me an update on both my engines."
- "How's Engine A doing?"
- "Is Engine B okay?"
- "Which of my engines needs attention first?"
- "Do either of my engines need servicing soon?"
- "How's my engine?" *(ambiguous on purpose — the app will ask you which one
  you mean, since you own two)*

### Things that won't work, on purpose

- A customer asking about an engine number instead of their own labeled
  engine, or one they don't own — e.g. "How is engine 34 doing?" while signed
  in as `customer4`, who doesn't own engine 34. You'll be told it isn't on
  your account, with no data given.
- A customer asking for raw numbers — e.g. "What's the exact RUL in cycles?"
  The app is designed to answer customers in plain outcomes, not figures, so
  it will keep the reply in plain language even if asked directly for numbers.
