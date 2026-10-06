# Demo video

The current demo video (2 min 30 s, 1080p, AI-narrated) is generated end to end by three scripts kept outside this
repo in `~/Documents/promise-keeper-video/`: `narration.py` (Kokoro text-to-speech), `record.py` (drives the real
dashboard in Chrome on the `showcase/` inbox, timed to the narration) and `assemble.py` (ffmpeg). Re-run them in
that order to regenerate it. The manual script below is the fallback for recording by hand.

# Manual script (about 3 minutes)

Record the screen at 1280×800 or larger.

## Before recording

```bash
python -m promise_keeper demo --reset        # demo inbox, replayed from the local cache (free)
python -m promise_keeper --demo serve        # dashboard at http://127.0.0.1:8765
```

For the drag-and-drop scene, start a second, empty dashboard (a fresh scan costs about 5 cents per take):

```bash
PK_DB=data/video.db PK_TODAY=2026-10-09 python -m promise_keeper serve --port 8766
```

## 0:00–0:20 The problem

Show `bench/chats/WhatsApp Chat with Mehta Family.txt`: good-morning messages, a forwarded fake RBI rule,
Papa's "from tomorrow I will start morning walk", and in the middle of it, "Haan kal bhejta hoon" about the
Netflix password.

> "Our chats are mostly noise, and somewhere in there are the promises we actually made. 'Kal bhejta hoon.'
> Then we forget, and someone has to chase us. Or we're the ones waiting."

## 0:20–1:00 Drop the chats in

Open http://127.0.0.1:8766 (empty). Drag all files from `bench/chats` onto the page. Show "Recognised you as
Aarav Mehta" and the live "Finding promises… 4/9 chats".

> "I export my WhatsApp chats and drop them here. It works out which sender is me, and only promise-like lines,
> with phone numbers and account numbers masked, go to NVIDIA Nemotron on Nebius. Everything else stays on
> my Mac."

## 1:00–1:45 What it found

Point at the summary chips, then the cards:
- **I promised**: "Send Netflix password, 10 days late", "Intro Sameer to Karthik"
- **Promised to me**: "Varun: pay me back ₹1,200", "Urban Fixit: ₹499 refund", "Kabir: return the drill"
- the **Kept** tab: the router fix, the laddoos, the Toit table, closed automatically from later messages

> "It ignored the forwards, the jokes, 'try karta hoon', and the promises between other people in my groups.
> And it follows the conversation: 'Laddoo mil gaye' closed the laddoo promise, 'sorry, will send tonight'
> moved Varun's date."

## 1:45–2:05 Ask it

Type "Who owes me money?" in the Ask box (answer: ₹499 + ₹1,200 = ₹1,699, with links to both cards), then
"Maa ko kya promise kiya tha?" (answers in Hinglish).

> "And I can just ask. A Nemotron agent looks it up with tools on my own data and answers in my language."

## 2:05–2:30 One tap to fix it

Switch to the demo dashboard (8765). Click **Draft update** on "Book the homestay" (Hinglish apology with a
new time) and show **Open in WhatsApp**; then **Draft reminder** on Vikram's quote and **Open in Mail**.

> "For anything late, it drafts the message in that chat's own voice, and one tap opens it in WhatsApp or as
> a reply in my mail."

## 2:30–2:45 Every morning

Run `python -m promise_keeper --demo digest --notify` to show the macOS notification.

> "It runs in the background and sends one digest every morning."

## 2:45–3:00 Accuracy and cost

Run `python -m promise_keeper eval --dataset bench` and `python -m promise_keeper --bench spend`.

> "On a hand-labelled everyday inbox, over three runs, it found 98% of promises with zero false alarms, and got
> every kept-or-late status right. A full scan costs about five cents."
