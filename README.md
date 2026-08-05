# Face-Recognition Telegram Bot (Employee Check-In) — Face++ version

Detects who's in a circular Telegram "video note" using the free
Face++ face recognition API — no local face model, no build issues,
no threshold-tuning guesswork.

## Files

- `bot.py` — the Telegram bot (runs as a webhook web service)
- `face_api.py` — talks to Face++ for detection/enrollment/matching
- `db.py` — talks to your Neon database (stores name ↔ face_token)
- `requirements.txt` — Python packages to install

## How it works, simply

1. `/enroll Bobur` + a clear photo → bot sends the photo to Face++,
   gets back a `face_token` (their internal face ID), saves it in
   your Neon `people` table alongside "Bobur".
2. Someone sends a circular video → bot grabs one frame, sends it to
   Face++'s Search API, which checks it against everyone enrolled and
   returns a confidence score + the matching `face_token`.
3. If the confidence clears Face++'s own recommended threshold →
   looks up the name for that `face_token` in Neon → replies
   "Accepted, Welcome Bobur!" to the employee, and forwards the video
   + name to the admin (chat ID `5523761749`).
4. Nothing is logged or stored beyond the enrollment record itself —
   the downloaded video file is deleted right after processing either way.

## 1. Get your free Face++ API key

1. Go to https://console.faceplusplus.com and sign up (no credit card)
2. Create an API Key from the console — you'll get an `API Key` and
   `API Secret`
3. Keep these handy for the environment variables below

## 2. Push these files to a GitHub repo
Create a repo, upload `bot.py`, `db.py`, `face_api.py`,
`requirements.txt`, `README.md` (all at the root, not in a subfolder).

## 3. Create the Web Service on Render
- Render Dashboard → **New** → **Web Service**
- Connect your GitHub repo
- **Runtime:** Python 3
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python bot.py`
- **Instance Type:** Starter tier or above (free tier sleeps when idle,
  which breaks the webhook)

## 4. Environment Variables
Render service → **Environment**:

| Key | Value |
|---|---|
| `BOT_TOKEN` | your token from @BotFather |
| `DATABASE_URL` | your Neon connection string |
| `FACEPP_API_KEY` | from console.faceplusplus.com |
| `FACEPP_API_SECRET` | from console.faceplusplus.com |

Render auto-provides `PORT` and `RENDER_EXTERNAL_URL` — don't set those.

## 5. Deploy
Click **Deploy**. Watch the logs — first boot will show:
```
Face++ FaceSet ready.
Database ready.
Bot starting (webhook mode) on port 10000 -> https://your-app.onrender.com/xxxx
```

## 6. Let the admin start a chat with the bot
Telegram won't let a bot message someone who hasn't opened a chat with
it first. From the admin's Telegram account (`5523761749`), send
`/start` to the bot once.

## Using the bot

**Admin only** (chat ID `5523761749`):
- `/enroll <tg_id> <Ism>` then send a photo — registers someone by
  their Telegram ID + name + face. Example: `/enroll 123456789 Bobur`
- `/enrollist` — shows everyone enrolled as tappable buttons; tapping
  a name lets you rename them, replace their photo, or delete them
- Receives a message + the forwarded video every time someone checks in

**Everyone else:**
- Send a circular video note — if their Telegram account is enrolled
  *and* their face matches, get "Qabul qilindi, Xush kelibsiz {Ism}!"
- If their Telegram account isn't enrolled at all (whether they send a
  video or just text the bot), they get told to contact the admin
  instead of getting any check-in attempt

## How access control works

Two separate checks now happen, in this order:
1. **Is this Telegram account enrolled at all?** (checked by `tg_id`)
   If not, the bot immediately replies with the "not in the list"
   message and stops — no face processing happens.
2. **Does their face match someone in Face++'s FaceSet?** (as before)

## Notes

- **Free tier**: no hard cap on total usage, but requests share queue
  capacity with other Face++ users — fine for a small team's check-ins.
- **Accuracy**: much better calibrated than a self-hosted model since
  Face++ tells us exactly what confidence threshold to use — no more
  guessing why something says "not recognized."
- **Data**: each check sends one video frame to Face++'s servers for
  comparison. If keeping biometric data fully in-house is a hard
  requirement, that's a reason to go back to a self-hosted model
  instead — happy to help with that trade-off if it matters here.
- **Biometric data / compliance**: still worth checking what's
  required where you operate (consent, disclosure) before rolling
  this out for real employees.
