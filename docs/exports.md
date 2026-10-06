# Getting your chats and email out

Promise Keeper only reads files you export yourself. It never logs in to WhatsApp or your email.

## WhatsApp

On your phone, open a chat → ⋮ (Android) or the contact name (iPhone) → **Export chat** → **Without media**,
and get the file onto your computer (AirDrop, email to yourself, Drive). Android gives a `.txt`; iPhone gives
a `.zip`, which you can use as it is. Drop the files on the dashboard, or put them in one folder.

- Keep the file name WhatsApp gives it (`WhatsApp Chat with Rohit Sharma.txt`); the name becomes the
  conversation name.
- Re-export a chat any time; messages already loaded are skipped.
- Day/month order is detected per file (Indian and UK phones write day first, US phones month first).

## Gmail

Use [Google Takeout](https://takeout.google.com), select only **Mail**, and choose the labels you want
(for example *Sent*). You get an `.mbox` file; put it in the same folder.

## Other email apps

Apple Mail, Outlook and Thunderbird can save messages as `.eml` files (drag them into a folder), or export a
mailbox as `.mbox`.

## Who you are

Promise Keeper works this out itself: in a one-to-one chat named after the other person, the other sender is
you, and in your own mailbox your address is on nearly every email. If it can't tell (only group chats, say),
the dashboard asks. From the command line: `python -m promise_keeper init --name "Your Name" --email you@example.com`.
