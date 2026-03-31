# LINE Bot for Home Assistant

A Home Assistant custom integration that connects your LINE Official Account to Home Assistant. Send rich messages to LINE users and groups from automations, and trigger automations from incoming LINE messages.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
![HA Version](https://img.shields.io/badge/HA-2024.11%2B-blue)

---

## Index

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Supported Languages](#supported-languages)
- [Installation](#installation)
- [Setup](#setup)
- [Recipients](#recipients)
- [Sending Messages](#sending-messages)
  - [Standard notify service](#standard-notify-service)
  - [Custom send service](#custom-send-service)
  - [Text and title](#text-and-title)
  - [Image](#image)
  - [Audio](#audio)
  - [Video](#video)
  - [Sticker](#sticker)
  - [Location](#location)
  - [Flex message](#flex-message)
  - [Button template](#button-template)
  - [Confirm template](#confirm-template)
  - [Reply token](#reply-token)
  - [Quick replies](#quick-replies)
- [Incoming Messages](#incoming-messages)
  - [line_bot_message_received event](#line_bot_message_received-event)
  - [Responding to messages](#responding-to-messages)
  - [Postback events](#postback-events)
- [Send Error Events](#send-error-events)
- [Group Chats](#group-chats)
- [Quota Sensors](#quota-sensors)
- [Options](#options)
- [Troubleshooting](#troubleshooting)
- [Message Quota](#message-quota)
- [License](#license)

---

## Features

- Send text, images, audio, video, stickers, locations, flex cards, and button/confirm templates to LINE users and groups.
- Two send interfaces: standard `notify.send_message` for simple text, and `line_ha_bot.send_message` for the full feature set.
- Trigger HA automations from incoming LINE messages via the `line_bot_message_received` event.
- Reply to incoming messages using LINE's free Reply API via reply tokens.
- Group chat support: add a LINE group as a recipient and send to or receive from the whole group.
- Webhook-based recipient discovery: message the bot and it appears automatically in the setup flow.
- Monthly quota sensors showing message limit and consumption.
- Error events (`line_bot_send_failed`) for failed sends, enabling retry or alert automations.
- Quick reply chips on any message type, giving recipients one-tap response options.
- Full UI configuration. No YAML required.
- Signature-verified webhook for security.

---

## Prerequisites

Before installing, you need a LINE Official Account with the Messaging API enabled. This takes about 15 minutes the first time.

### 1. Create a LINE Official Account

1. Go to https://account.line.biz/signup and log in with your personal LINE account.
2. Fill in the form (Account name, Category, Country, Email) and submit.

### 2. Enable the Messaging API

1. Go to https://manager.line.biz and open your new account.
2. Go to **Settings > Messaging API** and click **Enable**.
3. Select or create a Developer provider and confirm.

### 3. Get your credentials

1. You will be taken to the LINE Developers Console. If not, go to https://developers.line.biz/console and log in.
2. Select your provider, then your channel.
3. On the **Basic settings** tab, copy your **Channel secret**.
4. On the **Messaging API** tab, scroll to **Channel access token (long-lived)**, click **Issue**, and copy the token.

Keep both values private. Anyone with your Channel Access Token can send messages as your bot.

### 4. Configure your Home Assistant external URL

LINE requires your Home Assistant instance to be reachable from the internet via a **public HTTPS URL with a valid SSL certificate**. A plain HTTP address (e.g. ) or a self-signed certificate will not work. This is a hard requirement on LINE's side.

You have several options, listed from easiest to most technical:

**Option A: Nabu Casa (easiest)**
A Home Assistant Cloud subscription gives you a ready-made HTTPS domain with no configuration required. Costs approximately USD /month. See https://www.nabucasa.com.

**Option B: Cloudflare Tunnel (free)**
Cloudflare Tunnel routes your local HA through Cloudflare's network, giving you a proper HTTPS domain for free. Install the **Cloudflare** add-on from the HA add-on store, create a free Cloudflare account, and follow the add-on instructions.

**Option C: DuckDNS + Let's Encrypt (free)**
DuckDNS gives you a free dynamic DNS hostname (e.g. ). Combined with the **DuckDNS** and **Let's Encrypt** HA add-ons, you get a valid HTTPS URL at no cost. Requires your router to forward port 443 to your HA instance.

**Option D: Reverse proxy with your own domain**
If you own a domain name, point it at your home IP, set up a reverse proxy (e.g. NGINX), and use Let's Encrypt for SSL. Most technical option but gives you the most control.

Once you have a working HTTPS URL, set it in HA at **Settings > System > Network > Home Assistant URL (External)**.

---

## Supported Languages

The setup and configuration UI is available in the following languages:

- English
- Thai (ภาษาไทย)
- Japanese (日本語)
- Traditional Chinese (正體中文)
- Indonesian (Bahasa Indonesia)
- Korean (한국어)

Home Assistant will automatically use your configured language if it is supported, falling back to English otherwise.

---

## Installation

### Via HACS (recommended)

1. In Home Assistant, go to **HACS > Integrations**.
2. Click the three-dot menu and select **Custom repositories**.
3. Add `https://github.com/sfox38/line_ha_bot` as an **Integration**.
4. Search for **LINE Bot** and install it.
5. Restart Home Assistant.

### Manual installation

1. Download or clone this repository.
2. Copy the `custom_components/line_ha_bot` folder into your HA `config/custom_components/` directory.
3. Restart Home Assistant.

---

## Setup

1. Go to **Settings > Devices & Services > Add Integration** and search for **LINE Bot**.
2. Enter your **Channel Access Token** and **Channel Secret**.
3. The next screen shows your **Webhook URL**. Copy it.
4. In the LINE Developers Console, open your channel's **Messaging API** tab.
5. Under **Webhook settings**, paste the URL, enable **Use webhook**, and click **Verify**. It should return success.
6. Tick the confirmation checkbox and click **Submit**.

After setup, add recipients via the gear icon on the integration card.

---

## Recipients

Each recipient becomes a `notify` entity named `notify.line_bot_<name>`. Recipients can be individual LINE users or LINE groups.

### Adding a user recipient

1. Have the person **add your bot as a friend** in LINE (use the QR code or bot Basic ID from the LINE Developers Console).
2. Have them **send any message** to the bot.
3. In HA, go to the integration options (gear icon) and select **Add a recipient**. The person will appear in a dropdown automatically.
4. Select them and enter a name to use in Home Assistant.

### Adding a group recipient

1. Add your bot to a LINE group.
2. Have someone in the group **send any message**.
3. In HA, go to the integration options (gear icon) and select **Add a recipient**. The group will appear in the dropdown.
4. Select it and enter a name.

### Recipient names

When adding a recipient, two name fields are shown:

- **Display name** - shown in the HA UI. Accepts any characters including emoji, Thai, Japanese, and other unicode. Defaults to the LINE display name.
- **Entity name** - used to generate the HA entity ID (`notify.line_bot_<entity_name>`). Must contain only ASCII letters (a-z, A-Z), digits (0-9), spaces, hyphens, and underscores.

The integration suggests a safe entity name from the LINE display name automatically. Emoji are converted to their English name (e.g. 🤓 becomes "nerd"), Thai and Japanese characters are romanized, and ASCII characters are kept as-is. For example, `"สวัสดี 🤓 David"` suggests `"swasdii_nerd_david"`. You can override the suggestion with anything that meets the rules.

Note: captured recipients survive HA restarts. If someone messages your bot before you open the options flow, they will still appear in the dropdown after a restart.

---

## Sending Messages

There are two ways to send messages.

### Standard notify service

Use `notify.send_message` for simple text messages and title. This is the standard HA notify interface and works with any automation.

```yaml
action: notify.send_message
target:
  entity_id: notify.line_bot_david
data:
  message: "Front door opened"
  title: "Security Alert"
```

### Custom send service

Use `line_ha_bot.send_message` for the full feature set: images, audio, video, stickers, locations, flex cards, button and confirm templates, and reply tokens. The `entity_id` goes in `data`, not `target`.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  message: "Front door opened"
```

Multiple recipients can be targeted in a single call:

```yaml
action: line_ha_bot.send_message
data:
  entity_id:
    - notify.line_bot_david
    - notify.line_bot_gretel
  message: "Dinner is ready"
```

### Text and title

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  title: "Security Alert"
  message: "Motion detected in the garden"
```

### Image

The image URL must be publicly accessible via HTTPS.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  message: "Camera snapshot"
  image_url: "https://example.com/snapshot.jpg"
```

### Audio

Audio must be in M4A format and accessible via HTTPS. `audio_duration` is in milliseconds and is used by LINE as a cosmetic label on the message.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  audio_url: "https://example.com/alert.m4a"
  audio_duration: 3000
```

### Video

`video_preview_url` should point to a JPEG or PNG thumbnail image.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  video_url: "https://example.com/clip.mp4"
  video_preview_url: "https://example.com/clip-thumb.jpg"
```

### Sticker

LINE sticker package and sticker IDs can be found in the [LINE sticker list](https://developers.line.biz/en/docs/messaging-api/sticker-list/).

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  message: "Done!"
  sticker_package_id: "1"
  sticker_id: "1"
```

### Location

`location_title` and `location_address` are optional. If `message` is omitted, only the location is sent.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  location_title: "Home"
  location_address: "123 Main St, Seattle"
  location_latitude: 13.7563
  location_longitude: 100.5018
```

### Flex message

Pass the raw LINE flex message JSON as `flex_message` (the `contents` object). `flex_alt_text` is the fallback text shown in notifications on devices that do not support flex. If a `message` is also provided, it is sent as a text message before the flex card.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  flex_alt_text: "Motion alert"
  flex_message:
    type: bubble
    body:
      type: box
      layout: vertical
      contents:
        - type: text
          text: Motion detected
          weight: bold
          size: xl
```

See the [LINE Flex Message documentation](https://developers.line.biz/en/docs/messaging-api/flex-message-overview/) for the full schema.

### Button template

Supports up to 4 buttons. Each button needs `label`, `action` (`message`, `postback`, or `uri`), and `data` (text sent, postback data string, or URL). `flex_alt_text` is used as the template fallback text. `image_url` sets a thumbnail at the top. `template_default_url` opens a URL when the user taps the card body.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  message: "Choose an action"
  template_type: buttons
  template_title: "Home Control"
  flex_alt_text: "Home Control"
  buttons:
    - label: "Lock door"
      action: postback
      data: "lock=true"
    - label: "Unlock door"
      action: postback
      data: "lock=false"
    - label: "Camera"
      action: uri
      data: "https://example.com/camera"
```

### Confirm template

Requires exactly 2 buttons. Useful for Yes/No decisions sent to LINE.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  message: "Motion detected. Turn on the lights?"
  template_type: confirm
  flex_alt_text: "Motion alert"
  buttons:
    - label: "Yes"
      action: postback
      data: "lights=on"
    - label: "No"
      action: postback
      data: "lights=off"
```

### Reply token

When responding to an incoming message within 30 seconds, use the `reply_token` from the `line_bot_message_received` event. This uses LINE's free Reply API instead of the paid Push API.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: "{{ trigger.event.data.entity_id }}"
  message: "Got your message!"
  reply_token: "{{ trigger.event.data.reply_token }}"
```

Reply tokens expire after 30 seconds and can only be used once. After expiry, omit `reply_token` to fall back to the Push API.

### Quick replies

Quick reply chips appear above the LINE keyboard after any message and disappear once tapped. They work with all message types. Each chip needs `label`, `action` (`message`, `postback`, or `uri`), and `data`.

```yaml
action: line_ha_bot.send_message
data:
  entity_id: notify.line_bot_david
  message: "Motion detected. What would you like to do?"
  quick_replies:
    - label: "Turn on lights"
      action: postback
      data: "lights=on"
    - label: "Ignore"
      action: postback
      data: "lights=ignore"
    - label: "View camera"
      action: uri
      data: "https://example.com/camera"
```

---

## Incoming Messages

When a known recipient or group member sends a message to your bot, HA fires a `line_bot_message_received` event.

### line_bot_message_received event

```yaml
event_type: line_bot_message_received
data:
  type: text               # message type: text, image, audio, video, sticker, postback
  user_id: "Uabc123..."   # LINE user ID of the sender
  group_id: null           # LINE group ID, or null for direct messages
  entity_id: notify.line_bot_david  # HA entity ID of the matched recipient
  recipient_name: david    # HA name of the matched recipient
  display_name: "David"   # LINE display name of the recipient
  message_text: "hello"   # message text, or null for non-text types
  message_id: "12345"     # LINE message ID
  content_url: "https://api-data.line.me/v2/bot/message/12345/content"  # for image/audio/video, null otherwise
  postback_data: null      # postback data string for postback events, null otherwise
  reply_token: "abc..."   # use within 30 seconds for a free reply, null for postbacks from old events
  timestamp: 1774855568   # Unix timestamp of the event
```

### Responding to messages

Use the `entity_id` field from the event to reply without hardcoding the recipient:

```yaml
triggers:
  - trigger: event
    event_type: line_bot_message_received
    event_data:
      type: text
actions:
  - action: line_ha_bot.send_message
    data:
      entity_id: "{{ trigger.event.data.entity_id }}"
      message: "You said: {{ trigger.event.data.message_text }}"
      reply_token: "{{ trigger.event.data.reply_token }}"
```

### Postback events

When a user taps a postback button, a `line_bot_message_received` event fires with `type: postback` and the `postback_data` string you defined on the button. Use `event_data` on the trigger to filter for specific postback values:

```yaml
triggers:
  - trigger: event
    event_type: line_bot_message_received
    event_data:
      type: postback
      postback_data: "lights=on"
actions:
  - action: light.turn_on
    target:
      entity_id: light.living_room
```

---

## Send Error Events

When a send fails for any reason, HA fires a `line_bot_send_failed` event. You can use this to alert yourself via another channel, retry, or log the failure.

```yaml
event_type: line_bot_send_failed
data:
  entity_id: notify.line_bot_david
  recipient_name: david
  error_type: connection_error   # token_invalid, bad_request, reply_token_expired, http_error, connection_error
  error_message: "Cannot connect to host api.line.me:443..."
  http_status: null              # HTTP status code, or null for connection errors
  timestamp: 1774855568
```

Example automation to notify on failure:

```yaml
triggers:
  - trigger: event
    event_type: line_bot_send_failed
actions:
  - action: notify.persistent_notification
    data:
      title: "LINE Bot send failed"
      message: "{{ trigger.event.data.error_message }}"
```

---

## Group Chats

Groups are added as recipients the same way as individual users. Any message sent to the group fires `line_bot_message_received`. The `group_id` field in the event identifies which group the message came from, and `user_id` identifies which member sent it.

For dedicated HA groups (e.g. a private group with just your household), all messages fire the event. For larger groups, filter by `message_text` content in your automation conditions.

Sending to a group uses the same `line_ha_bot.send_message` service with the group's `notify` entity ID.

---

## Quota Sensors

Two sensors are created automatically, polling LINE's API once per hour:

- `sensor.line_bot_monthly_message_limit` - your plan's monthly message limit, with `plan_type` as an attribute.
- `sensor.line_bot_monthly_message_consumption` - messages sent so far this month, with `remaining` as a calculated attribute.

Use these in automations or dashboards to monitor usage and avoid hitting your quota unexpectedly.

---

## Options

Click the gear icon on the integration card to access:

- **Add a recipient** - Webhook-based capture flow for users and groups.
- **Remove a recipient** - Select a recipient to delete.
- **Update credentials** - Replace the Channel Access Token and/or Channel Secret.

---

## Troubleshooting

### Webhook Verify returns 404

The webhook view is not registered yet. Make sure the integration is installed and HA has been restarted before clicking Verify.

### Webhook Verify returns 400 or 403

The request is failing signature verification. Check that your Channel Secret is correct. You can update it via the integration options (gear icon) and select **Update credentials**.

### No message received in the add recipient spinner

Make sure the person has added your bot as a friend in LINE and sent it a message, or that the group has your bot as a member and someone has sent a message. The webhook URL in the LINE Developers Console must be set correctly and **Use webhook** must be enabled.

### Token invalid or revoked error in logs

Your Channel Access Token has expired or been revoked. Go to the integration options (gear icon) and select **Update credentials** to enter a new one.

### Group messages are not triggering automations

Make sure the group has been added as a recipient via the options flow. The bot must be a member of the group and someone must have sent a message to it to trigger the capture flow.

### send_message fails with "reply token expired"

Reply tokens are valid for 30 seconds from when the original message was received. If your automation takes longer than that to run, omit `reply_token` and the service will fall back to the Push API.

### Enabling debug logging

Add the following to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.line_ha_bot: debug
```

---

## Message Quota

LINE Official Accounts on the free plan can send 500 messages per month. Each recipient counts as one message per send call, regardless of how many message objects are in the payload (text plus image is still one message). Reply API messages are free and do not count against the quota.

Monitor your usage with the [quota sensors](#quota-sensors).

For higher volume, see [LINE Messaging API pricing](https://developers.line.biz/en/docs/messaging-api/pricing/).

---

## License

MIT. See [LICENSE](LICENSE) for details.
