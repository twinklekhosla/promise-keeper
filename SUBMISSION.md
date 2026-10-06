# Devpost write-up (draft)

**Track:** Personal AI
**Built with:** NVIDIA Nemotron 3 Super and Nemotron 3 Ultra · Nebius Token Factory · Python · SQLite · FastAPI

## Inspiration

"Kal tak bhej dunga." "Plumber Tuesday tak aa jayega." "I'll send the floor plan by EOD tomorrow."
Small promises fly across WhatsApp and email all week, buried in family forwards, memes and banter, in
English, Hindi and everything in between. The ones we forget quietly cost trust; the ones made to us get lost
until we remember, too late, that the plumber never came. To-do apps need you to type the task in. The
promise is already written down, in the chat.

## What it does

Promise Keeper reads your own WhatsApp and email exports on your machine and keeps a ledger of promises in
both directions: what you owe people, and what people owe you.

- Picks the real promises out of everyday noise, in English, Hindi (Devanagari too) and Hinglish, and ignores
  forwards, jokes, maybes, RSVPs, promises to yourself, marketing, and other people's promises in your groups.
- Works out due dates from the message date ("by Monday", "EOD tomorrow", "5-7 working days").
- Follows the conversation: "Here's the deck" marks it kept; "sorry ji, Monday pakka" moves the date.
- Knows what matters: money, documents and bookings rank high; family micro-logistics ("make kebabs", "come
  around 7:30") are hidden as minor, and a code check makes sure sending, returning or paying never is.
- Totals the money: "₹1,699 owed to you" from "I'll pay you back ₹1,200" and "refund of ₹499".
- Answers questions in plain words ("Who owes me money?", "Maa ko kya promise kiya tha?") through a Nemotron
  agent that calls local tools over your promise list and chats.
- One-tap follow-up: a draft in that chat's own tone and language, opened straight in WhatsApp or as an email
  reply to the right person. Morning digest as a macOS notification or on Telegram.
- Drag-and-drop setup: drop your exports on the page, it recognises which sender is you, and scans in the
  background. "Not a promise" teaches it what you don't care about.

## How it's built

- Local parsers for WhatsApp exports (Android `.txt`, iPhone `.zip`, day- or month-first dates, system lines
  and edits removed) and `.eml` / `.mbox` email (newsletters skipped via list headers).
- Privacy by selection: only promise-like messages and requests go to the model, each with minimal context
  (the message it answers, the replies to a request, the nearest earlier mention of a date). Phone numbers,
  emails, links and account numbers are masked locally first, including phone-number senders in groups.
- NVIDIA Nemotron 3 Super on Nebius Token Factory extracts promises (with amounts), writes drafts, and runs the
  Ask agent with tool calling (find_promises, money_summary, search_messages, all executed locally with
  masked results); Nemotron 3 Ultra
  decides whether each was kept, the step where a mistake produces a false "3 days late". All calls use
  structured JSON output, four chats in parallel.
- Code checks around the model: the owner comes from who sent the message; a promise made to someone else in
  a group is dropped; only a later message can close a promise; reschedules merge while follow-ups stay
  separate; impossible dates (wrong weekday, before the promise, invented) are corrected or dropped; answers
  cut off by the length limit are retried on half the messages; one failing chat never stops the others.
- Hard budget cap and a local response cache. The whole build, including every test run, used about $2.50 of
  Nebius credit.

## Accuracy

Two hand-labelled inboxes ship with the repo and an `eval` command. The "everyday" one is mostly noise: a
family group full of forwards, a college group full of banter, a flatmate, a repair service, marketing email.
Over three fresh runs: 98% of promises found, zero false alarms, 97% of due dates right, 100% of kept/late
statuses right. The first version of the app, before this work, found 7 of 21 on the same inbox.

## Challenges

- Everyday chat is mostly not promises. Getting from "flags everything" to zero false alarms took both prompt
  rules and deterministic checks, measured on a noisy benchmark after every change.
- Reschedules versus new promises: "Sorry ji, Monday pakka" is the same promise moved; "we'll send a senior
  technician" after "service completed" is a new one. The difference is whether the first was fulfilled in
  between.
- Nemotron's reasoning counts toward the output limit, so long chats got cut off mid-answer. Detecting that and
  splitting the window fixed a crash that once skipped half the inbox.
- Run-to-run variation: every number above is over several fresh runs, not the best one.

## What's next

- Direct Gmail and IMAP sync instead of exports.
- An Android companion that reads WhatsApp notifications on-device, so no export is needed.
- Personal patterns: which kinds of promises you tend to miss, and nudging earlier for those.

## Links

- Live demo (no sign-up): https://twinklekhosla.github.io/promise-keeper/demo/
- Code: (GitHub link)
- Demo video: (YouTube link)

Gallery images: `docs/architecture.png`, `docs/dashboard.png`.
