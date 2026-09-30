<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=220&section=header&text=Merchant%20Bot&fontSize=64&fontAlignY=38&desc=Auto-verified%20UPI%20payments%20%E2%80%A2%20Instant%20delivery%20%E2%80%A2%20Telegram&descAlignY=60&animation=fadeIn" width="100%"/>

<a href="https://git.io/typing-svg"><img src="https://readme-typing-svg.demolab.com?font=Fira+Code&weight=600&size=22&pause=1200&color=00D4FF&center=true&vCenter=true&width=720&lines=Sell+anything+on+Telegram+%F0%9F%9B%8D;Payments+verified+from+SMS+%F0%9F%93%B1;Item+delivered+in+seconds+%E2%9A%A1;Owner+panel+with+inline+buttons+%F0%9F%8E%9B" alt="Typing SVG"/></a>

<br/>

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Pyrogram](https://img.shields.io/badge/Pyrogram-2.0-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-Motor-47A248?style=for-the-badge&logo=mongodb&logoColor=white)
![aiohttp](https://img.shields.io/badge/aiohttp-Webhook-2C5BB4?style=for-the-badge&logo=aiohttp&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Heroku](https://img.shields.io/badge/Heroku-Deployable-430098?style=for-the-badge&logo=heroku&logoColor=white)

**A Telegram merchant bot that sells digital items, takes UPI payments, verifies them by reading your bank SMS through Deku, and delivers automatically.**

[Features](#-features) • [How it works](#-how-it-works) • [Deku SMS integration](#-deku-sms-integration) • [Setup](#-installation) • [Owner guide](#-owner-guide) • [Troubleshooting](#-troubleshooting)

</div>

---

## ✨ Features

<table>
<tr>
<td width="50%" valign="top">

### 🛒 For buyers
- 🔍 **Discover** page: every item, price and stock left
- 📄 **Inline pagination** for large catalogs
- 💳 **QR with the exact amount** baked in
- ⏳ **15-minute payment window** per purchase
- ⚡ **Instant auto-delivery** after payment
- ❌ One-tap **cancel**; old sessions are replaced automatically
- 🎨 Small-caps text and a **random image on every message**

</td>
<td width="50%" valign="top">

### 🎛 For the owner
- 📦 **`/stock`**: add, edit, delete and restock items with buttons
- 🧙 **Step-by-step listener**: item, no., price, warranty, delivery details
- ⚙️ **Live payment settings**: change UPI ID or QR image from the bot
- 📢 **`/broadcast`** with preview, confirm and live progress
- 💰 **Sale alerts** and unmatched-payment alerts
- 📊 Stats: items, units, sold, revenue, users

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔐 Payment engine
- 📱 **Deku SMS** forwards your bank SMS to a secret webhook
- 🧮 **Regex verification** (credit only, balance ignored)
- 🎯 **Unique-paise matching** so two buyers never collide
- ♻️ **Duplicate SMS protection** (retries are ignored)
- 🔒 Unit is **reserved before payment**, so paid means guaranteed stock

</td>
<td width="50%" valign="top">

### 🧱 Engineering
- 🐍 Six focused files, no bloat
- 🍃 Async MongoDB (Motor) with atomic claims
- 🧵 Race-safe: unique partial index + `find_one_and_update`
- 🧹 Expiry loop releases stock automatically
- 🐳 Dockerfile + Procfile included

</td>
</tr>
</table>

---

## 🧠 How it works

```mermaid
sequenceDiagram
    autonumber
    participant U as 👤 Buyer
    participant B as 🤖 Bot
    participant DB as 🍃 MongoDB
    participant P as 📱 Owner phone with Deku
    participant W as 🔐 SMS webhook

    U->>B: tap item, then tap Pay
    B->>DB: reserve 1 unit + lock unique amount, e.g. 99.01
    B-->>U: QR for exactly 99.01, valid 15 min
    U->>P: pays via any UPI app
    P->>W: bank SMS forwarded with ?secret=...
    W->>W: regex checks credit, amount, reference
    W->>DB: atomically claim the pending session
    W-->>B: matched
    B-->>U: payment verified, item delivered
    B-->>P: owner gets a sale alert
```

### Session lifecycle

```mermaid
stateDiagram-v2
    [*] --> created: buyer taps an item
    created --> pending: buyer taps Pay, unit reserved, amount locked
    pending --> paid: SMS amount matches
    pending --> expired: 15 minutes pass
    pending --> cancelled: buyer cancels
    created --> [*]: swept if never paid
    paid --> [*]
    expired --> [*]
    cancelled --> [*]
```

> 💡 **Why unique amounts?** An SMS only tells you *how much* arrived, not *who* paid. If two people buy a ₹99 item at once, the first sees **₹99.00** and the second **₹99.01**. The amount identifies the buyer. Buyers must pay the **exact** amount shown.

---

## 📁 Project structure

```
merchant_bot/
├── main.py           # starts pyrogram, the webhook server and the expiry loop
├── cmds.py           # /start /stock /broadcast, listener (ask), small-caps + image helpers
├── inline.py         # every inline-button callback (shop + owner panels)
├── database.py       # all MongoDB access (motor)
├── verify.py         # Deku webhook, regex verification, delivery, expiry loop
├── config.py         # settings + the random image list
├── requirements.txt
├── Dockerfile
├── Procfile
├── .python-version   # contains exactly: 3.11
└── README.md
```

<details>
<summary><b>🍃 MongoDB collections</b></summary>

| Collection | Purpose |
|---|---|
| `users` | Everyone who pressed `/start` (used for broadcast) |
| `products` | Name, price, warranty |
| `units` | Individual sellable units: `available` → `reserved` → `sold` |
| `sessions` | Purchases: `created` → `pending` → `paid` / `expired` / `cancelled` |
| `payments` | Log of every credit SMS (deduplication and audit) |
| `settings` | UPI ID, QR image and QR mode saved from the bot |

A **unique partial index** on `sessions.amount_paise` (only for `pending`) guarantees two open payments never share an amount.

</details>

---

## 📱 Deku SMS integration

The bot never talks to your bank. Your phone receives the bank or UPI credit SMS, **Deku forwards it to your bot's webhook**, and the bot verifies it with regex.

```
Bank SMS ──► Deku (on your phone) ──► https://YOUR-DOMAIN/api/payment/sms-webhook?secret=YOUR_SECRET
```

### Step 1: Prepare the webhook URL

```
https://YOUR-DOMAIN/api/payment/sms-webhook?secret=YOUR_SECRET
```

- `YOUR-DOMAIN` must be **public HTTPS** (for Heroku: `https://your-app-name.herokuapp.com`).
- `YOUR_SECRET` must equal the `WEBHOOK_SECRET` variable. **Change the default `seishiro` to a long random string.**
- The route accepts **any HTTP method**.

### Step 2: Set up Deku on the phone that receives the payment SMS

1. Install **Deku SMS** on the Android phone whose number is linked to your bank or UPI account.
2. Grant SMS permissions so it can read incoming messages.
3. Open Deku's **forwarding / gateway settings** and add a new **HTTP endpoint** using the URL from Step 1. Menu names differ between Deku versions, so look for the option that forwards incoming SMS to a URL.
4. Choose **POST** with a **JSON body** if you're given a choice.
5. If Deku can filter by sender, allow only your bank and UPI sender IDs.
6. Turn off battery optimization for Deku so Android doesn't kill it in the background.

### Step 3: Test the webhook without any SMS

Start a purchase in the bot, note the **exact amount** shown (for example ₹99.01), then run:

```bash
curl -X POST "https://YOUR-DOMAIN/api/payment/sms-webhook?secret=YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"address":"HDFCBK","text":"Rs 99.01 credited to A/c XX1234 on 30-09-26 by UPI Ref No 412345678901. Avl Bal Rs 5,000.00"}'
```

| Response | Meaning |
|---|---|
| `{"ok": true, "matched": true}` | ✅ Payment matched. The item was delivered |
| `"reason": "no_session"` | Valid credit, but no open session has that exact amount |
| `"reason": "not_a_credit"` | The SMS was not recognised as money received |
| `"reason": "duplicate"` | This SMS was already processed |
| `"reason": "sender"` | Blocked by `ALLOWED_SENDERS` |
| HTTP `403` | Wrong `secret` |
| HTTP `400` | No SMS text found in the request |

### What payloads are accepted

The parser digs the SMS text out of whatever Deku sends:

- **JSON**: any of the keys `text`, `message`, `body`, `sms`, `content`, `msg`, `data` (nested objects too)
- **Sender** (optional): `address`, `sender`, `from`, `number`, `phone`
- **Form data** (`application/x-www-form-urlencoded`, `multipart/form-data`)
- **Raw text** body, or a **GET** request with the SMS in the query string

### 🔍 How verification works (regex only)

```mermaid
flowchart TD
    A[SMS text arrives] --> B[Strip balance text like Avl Bal Rs 5,000]
    B --> C{Has credited, received, deposited, paid you or sent you?}
    C -- no --> X1[Ignore: not a credit]
    C -- yes --> D{Has debited, withdrawn, spent, sent to, paid to?}
    D -- yes --> X1
    D -- no --> E[Extract first amount after Rs, INR or rupee sign]
    E --> F[Extract UPI ref or UTR for dedupe]
    F --> G{SMS already seen?}
    G -- yes --> X2[Ignore: duplicate]
    G -- no --> H{Pending session with this exact amount, not expired?}
    H -- no --> X3[Alert owner: unmatched credit]
    H -- yes --> OK[Mark paid, deliver item]
```

| Rule | Regex idea | Why |
|---|---|---|
| Credit only | `credited\|received\|deposited\|paid you\|sent you` | Never react to money leaving your account |
| Reject debits | `debited\|withdrawn\|spent\|sent to\|paid to…` | Prevents false positives |
| Ignore balance | strips `Avl Bal Rs …` | Your balance must not be read as the payment |
| Amount | `(₹\|Rs.?\|INR) 1,234.56` | Converted to paise for exact comparison |
| Reference | `UPI Ref / UTR / Txn ID` (8+ chars) | Used as the dedupe key |

Verified payments are claimed atomically, so **one SMS can only ever pay one order**.

---

## 🚀 Installation

### Prerequisites

| Need | Where |
|---|---|
| Telegram `API_ID` and `API_HASH` | <https://my.telegram.org> |
| Bot token | [@BotFather](https://t.me/BotFather) |
| Your numeric Telegram user ID | e.g. [@userinfobot](https://t.me/userinfobot) |
| MongoDB URI | [MongoDB Atlas](https://www.mongodb.com/atlas) free tier works |
| UPI ID | Your own, for example `name@bank` |
| Android phone with Deku | Receives the bank SMS |

### ⚙️ Environment variables

| Variable | Required | Default | Description |
|---|:---:|---|---|
| `API_ID` | ✅ | `0` | Telegram API ID |
| `API_HASH` | ✅ | | Telegram API hash |
| `BOT_TOKEN` | ✅ | | Token from BotFather |
| `OWNER_IDS` | ✅ | `123456789` | Owner IDs, **space separated** |
| `MONGO_URI` | ✅ | `mongodb://localhost:27017` | MongoDB connection string |
| `WEBHOOK_SECRET` | ✅ | `seishiro` | Secret in the webhook URL. **Change it** |
| `UPI_ID` | ⭕ | `yourname@upi` | Default UPI ID (changeable later from `/stock`) |
| `PAYEE_NAME` | ⭕ | `My Store` | Name shown in UPI apps |
| `STATIC_QR` | ⭕ | | Default static QR: path, URL or file_id |
| `SUPPORT` | ⭕ | `@your_support` | Shown to buyers when something fails |
| `DB_NAME` | ⭕ | `merchant_bot` | Database name |
| `ALLOWED_SENDERS` | ⭕ | *(any)* | Regex, e.g. `HDFC\|SBI\|PAYTM`. Checked against sender and message text |
| `PORT` | ⭕ | `8080` | Web server port (hosts set this automatically) |

Tunables in `config.py`: `SESSION_TIMEOUT` (15 min), `ITEMS_PER_PAGE` (6), `MAX_PAISE_OFFSET` (99, so up to 100 simultaneous buyers per price), `IMAGES` (the random image list).

### 💻 Option A: Run locally

```bash
git clone <your-repo-url> merchant_bot && cd merchant_bot
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export API_ID=123456 API_HASH=abcdef... BOT_TOKEN=123:ABC...
export OWNER_IDS="111111111" MONGO_URI="mongodb+srv://..."
export UPI_ID="name@bank" WEBHOOK_SECRET="a-long-random-string"

python main.py
```

To receive the Deku webhook on your own machine, expose it with a tunnel such as `cloudflared tunnel --url http://localhost:8080` or `ngrok http 8080`, and use that HTTPS URL in Deku.

### 🐳 Option B: Docker

```bash
docker build -t merchant-bot .
docker run -d --name merchant-bot --restart unless-stopped \
  --env-file .env -p 8080:8080 merchant-bot
```

`.env` example:

```env
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=123456:ABC-DEF
OWNER_IDS=111111111
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net
UPI_ID=name@bank
PAYEE_NAME=My Store
WEBHOOK_SECRET=change-me-to-something-long
SUPPORT=@yoursupport
```

### ☁️ Option C: Heroku

1. Push this folder to a Git repo. `.python-version` must contain **exactly** `3.11`. Only the number, no `Python` word, no quotes.
2. Create the app and set the config vars:
   ```bash
   heroku create your-app-name
   heroku config:set API_ID=... API_HASH=... BOT_TOKEN=... OWNER_IDS="111111111" \
     MONGO_URI="..." UPI_ID="name@bank" WEBHOOK_SECRET="long-random-string"
   git push heroku main
   ```
3. Scale the **web** process, since the Deku webhook needs a web dyno:
   ```bash
   heroku ps:scale web=1
   ```
4. Your webhook URL is `https://your-app-name.herokuapp.com/api/payment/sms-webhook?secret=long-random-string`.

> ⚠️ Use a dyno type that **does not sleep**. A sleeping dyno misses SMS webhooks and stops answering users.
> ⚠️ In MongoDB Atlas, allow Heroku to connect under **Network Access** (Heroku IPs change, so many people allow `0.0.0.0/0` with a strong DB password).

---

## 🎛 Owner guide

### Commands

| Command | Who | What it does |
|---|---|---|
| `/start` | everyone | Welcome message and the **Discover** button |
| `/stock` | owner | Stock manager and payment settings |
| `/broadcast` | owner | Send a message to all users (or reply to a message with it) |
| `/cancel` | owner | Abort any running step-by-step prompt |

### 📦 Adding an item (`/stock` → ➕ Add Item)

The bot asks, one step at a time, with a **Cancel** button on every prompt:

| Step | Question | Example |
|---|---|---|
| 1 | Item name | `Netflix 1 Month` |
| 2 | **No.** (how many units) | `5` |
| 3 | Price per unit | `149` |
| 4 | Warranty | `30 days` |
| 5 | Delivery details the buyer receives | 5 lines (one per unit) **or** 1 line reused for all units |

Each unit is stored separately. When one sells, it is marked **sold** and the stock count drops automatically.

### 🛠 Managing items (`/stock` → 📋 Manage Items)

➕ Add more units • ✏️ Edit price • 🗑 Delete item (unsold units only) • live stock and sold counters.

### ⚙️ Payment settings (`/stock` → ⚙️ Payment Settings)

| Button | Action |
|---|---|
| ✏️ Set UPI ID | Validates `name@bank` format and saves it instantly |
| 🖼 Set QR Image | Upload a photo or send an image link. Switches to **static** mode |
| 🔀 Mode | Toggle **dynamic** and **static** |
| 🗑 Clear QR | Removes the static QR and returns to dynamic |
| 👁 Preview | Sends you exactly what a buyer would receive |

| Mode | Behaviour | Best for |
|---|---|---|
| **dynamic** (recommended) | QR generated from your UPI ID with the exact amount inside | Reliable auto-matching |
| **static** | Your uploaded QR image, sent as is | If you must show a fixed QR. Buyers must type the exact amount, including paise |

Changes apply to the **next** payment. Values saved in the bot override `config.py` and env defaults.

### 📢 Broadcasting

1. Send `/broadcast`, or reply to any message with `/broadcast`.
2. Send the content (text, photo, video, anything).
3. Review the preview, then tap **✅ Send** or **❌ Cancel**.
4. Watch live progress. Users who blocked the bot are skipped and flagged.

Broadcast content is delivered **exactly as you wrote it**, with no small-caps conversion.

---

## 🎨 UI details

- 🔡 **Small caps everywhere**: messages, captions, buttons and popups. Credentials, UPI IDs (inside `<code>`), `/commands` and `@usernames` are deliberately left untouched.
- 🖼 **Random image on every message**, and every screen change swaps to a new one. Images Telegram rejects are skipped automatically, with a text fallback.
- 🧭 Buttons everywhere: pagination, back, home, cancel, confirmations.

---

## 🔒 Security notes

- Use a **long random `WEBHOOK_SECRET`**. Anyone with the URL could fake a payment.
- Serve the webhook over **HTTPS** only.
- Set `ALLOWED_SENDERS` to your bank's sender IDs to ignore unrelated SMS.
- Keep the phone with Deku secure, since whoever can send it SMS can influence verification.
- Owner-only actions are checked against `OWNER_IDS` on every command and button press.
- Delivery details are stored in MongoDB as plain text, so restrict database access.

---

## 🩺 Troubleshooting

<details>
<summary><b>Heroku: <code>RuntimeError: There is no current event loop</code></b></summary>

Heroku defaulted to Python 3.14, which Pyrogram 2.0.x doesn't support. Add a `.python-version` file containing exactly `3.11`.

</details>

<details>
<summary><b>Heroku: <code>Invalid Python version in .python-version</code></b></summary>

The file must contain only the number: `3.11`. No `Python` prefix, no quotes, no `.x`.

</details>

<details>
<summary><b>Payment done but item not delivered</b></summary>

- Check Deku actually forwarded the SMS (Deku logs, or your app logs for a webhook request).
- Test with the `curl` command above and read the `reason` field.
- The buyer must pay the **exact** amount shown, including paise. A different amount can't be matched.
- The session must still be open (15 minutes). Late payments trigger an **unmatched credit** alert to the owner for a manual refund or delivery.

</details>

<details>
<summary><b>The webhook returns 403</b></summary>

The `secret` in the URL doesn't match `WEBHOOK_SECRET`. Check for typos and stray spaces.

</details>

<details>
<summary><b>Some SMS return <code>not_a_credit</code></b></summary>

Your bank's wording may not contain the credit words. Add the phrase to `CREDIT_RE` in `verify.py`. Also check `DEBIT_RE` isn't matching text inside the credit SMS.

</details>

<details>
<summary><b>Images don't show</b></summary>

Telegram may reject some URLs. The bot skips them automatically. If every image fails, messages fall back to plain text. Check the logs for `image rejected` lines.

</details>

<details>
<summary><b>Dynamic QR isn't generated</b></summary>

`qrcode[pil]` must be installed and a UPI ID must be set. Otherwise the bot falls back to your static QR image, or shows the UPI ID as text.

</details>

<details>
<summary><b>Bot restarts and hits a FloodWait</b></summary>

Avoid rapid restart loops. Telegram rate-limits repeated bot logins. Fix the crash cause and redeploy once.

</details>

---

## ⚠️ Limitations to know

- Verification depends on **SMS delivery** and **wording** from your bank. Test with your own bank's real SMS before going live.
- Payments made with the **wrong amount** aren't auto-matched, so the owner gets an alert and handles them manually. There are no automatic refunds.
- The unique-paise method supports up to **100 simultaneous buyers per price**. More get a "try again in a minute" message.
- This automates payments from your own account. You are responsible for complying with the laws, payment rules and Telegram's Terms that apply to what you sell.

---

## 🤝 Contributing

Issues and pull requests are welcome. Keep the six-file layout, run `python -m py_compile *.py` before submitting, and test the webhook with `curl`.

<div align="center">

<br/>

**Built for sellers who'd rather sleep than check SMS.** 🌙

<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=120&section=footer" width="100%"/>

</div>
<p align="center">
  <b>Channel Credits: <a href="https://t.me/cantarellabots">@cantarellabots</a></b>
</p>
