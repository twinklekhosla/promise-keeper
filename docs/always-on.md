# Running it in the background (macOS)

`watch` re-reads your export folder every 30 minutes (only files that changed), finds new promises, and sends
the digest once a day. To start it at login:

```bash
python -m promise_keeper install ~/Downloads --digest-at 09:00
```

This writes `~/Library/LaunchAgents/com.promisekeeper.watch.plist` and prints the command to switch it on:

```bash
launchctl load ~/Library/LaunchAgents/com.promisekeeper.watch.plist
```

Switch it off with `launchctl unload` on the same file. Its log is `data/watch.log`.

Pointing it at `~/Downloads` works: only WhatsApp exports and email files are read, everything else there is
ignored. The Mac has to be awake for it to run.
