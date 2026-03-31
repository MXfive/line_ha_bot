"""
LINE Bot integration for Home Assistant.

Allows Home Assistant to send messages to LINE users and groups via the LINE
Messaging API, and to react to incoming LINE messages via HA bus events.

Architecture overview:
- __init__.py   : Integration setup, teardown, custom service, and permanent webhook view.
- config_flow.py: UI-driven setup and options flows (credentials, recipients).
- notify.py     : NotifyEntity platform - one entity per recipient, text and title only.
- sensor.py     : Quota sensors showing monthly message limit and consumption.
- const.py      : All constants (domain, URLs, config keys, attribute names).

Custom service:
  line_ha_bot.send_message supports the full LINE message feature set: text, title,
  image, sticker, audio, video, flex card, location, button/confirm templates, and
  reply tokens. It fires a line_bot_send_failed HA bus event on any error.

Incoming events:
  The webhook fires line_bot_message_received for each text, sticker, image, audio,
  video, and postback event from a known recipient or group. Unknown senders are
  captured into pending_users for recipient setup via the options flow.

Webhook design:
  A permanent HomeAssistantView is registered at LINE_WEBHOOK_PATH on HA startup
  (via async_setup) and also defensively in async_setup_entry. The view handles:
    - Empty events array: LINE's Verify button health check - return 200 immediately.
    - All-zeros reply token: LINE's internal test event - return 200 immediately.
    - No config entry yet: return 200 to keep LINE happy during initial setup.
    - Known recipients (user or group): verify signature, fire line_bot_message_received.
    - Unknown senders: verify signature, capture to pending_users for options flow.

Group support:
  Group events use groupId as the recipient lookup key. The bot must be a member
  of the group. All messages in a registered group fire line_bot_message_received
  regardless of sender.

Signature verification:
  Every real event from LINE is signed with HMAC-SHA256 using the channel secret.
  The signature is base64-encoded and sent in the X-Line-Signature header. We
  verify this before processing any event. Requests that fail verification are
  rejected with HTTP 400.
"""

import base64
import hashlib
import hmac
import json
import logging

import aiohttp
import voluptuous as vol
from aiohttp.web import Request, Response
from aiohttp.web_exceptions import HTTPBadRequest, HTTPForbidden

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    CONF_CHANNEL_ACCESS_TOKEN,
    CONF_CHANNEL_SECRET,
    ATTR_IMAGE_URL,
    ATTR_STICKER_PACKAGE_ID,
    ATTR_STICKER_ID,
    ATTR_REPLY_TOKEN,
    ATTR_FLEX_MESSAGE,
    ATTR_FLEX_ALT_TEXT,
    ATTR_LOCATION_TITLE,
    ATTR_LOCATION_ADDRESS,
    ATTR_LOCATION_LATITUDE,
    ATTR_LOCATION_LONGITUDE,
    ATTR_TEMPLATE_TYPE,
    ATTR_TEMPLATE_TITLE,
    ATTR_TEMPLATE_DEFAULT_URL,
    ATTR_BUTTONS,
    ATTR_QUICK_REPLIES,
    ATTR_AUDIO_URL,
    ATTR_AUDIO_DURATION,
    ATTR_VIDEO_URL,
    ATTR_VIDEO_PREVIEW_URL,
    LINE_CONTENT_URL,
    LINE_PROFILE_URL,
    LINE_GROUP_SUMMARY_URL,
    LINE_BOT_INFO_URL,
    LINE_PUSH_URL,
    LINE_REPLY_URL,
    LINE_WEBHOOK_PATH,
    PENDING_USERS_KEY,
    RECIPIENTS_KEY,
    EVENT_MESSAGE_RECEIVED,
    EVENT_SEND_FAILED,
    SERVICE_SEND_MESSAGE,
    KEY_VIEW_REGISTERED,
    KEY_CONFIG_SNAPSHOT,
    DEFAULT_FLEX_ALT_TEXT,
    DEFAULT_LOCATION_TITLE,
    DEFAULT_ACTION_LABEL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["notify", "sensor"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the LINE Bot integration from configuration.yaml (if present).

    This runs before any config entry is loaded. Registering the webhook view
    here ensures it is available immediately, including during the initial
    config flow when no entry exists yet and the user needs to click Verify
    in the LINE Developers Console.
    """
    hass.data.setdefault(DOMAIN, {})
    if not hass.data[DOMAIN].get(KEY_VIEW_REGISTERED):
        hass.http.register_view(LineMessagingWebhookView(hass))
        hass.data[DOMAIN][KEY_VIEW_REGISTERED] = True
        _LOGGER.debug("LINE Bot webhook registered at %s", LINE_WEBHOOK_PATH)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LINE Bot from a config entry.

    Called once per config entry on HA startup (or after a reload). Registers
    the webhook view if not already registered (defensive, in case async_setup
    was not called), initialises per-entry runtime data (loading any persisted
    pending_users from config entry data), sets up the notify and sensor platforms,
    registers the line_ha_bot.send_message custom service, and registers an update
    listener to reload on options changes.
    """
    hass.data.setdefault(DOMAIN, {})
    if not hass.data[DOMAIN].get(KEY_VIEW_REGISTERED):
        hass.http.register_view(LineMessagingWebhookView(hass))
        hass.data[DOMAIN][KEY_VIEW_REGISTERED] = True
        _LOGGER.debug("LINE Bot webhook registered at %s", LINE_WEBHOOK_PATH)

    # Per-entry runtime dict. pending_users holds LINE user IDs captured by
    # the webhook that have not yet been confirmed as recipients. Load from
    # config entry data so captures survive HA restarts.
    hass.data[DOMAIN].setdefault(entry.entry_id, {})
    persisted_pending = dict(entry.data.get(PENDING_USERS_KEY, {}))
    hass.data[DOMAIN][entry.entry_id][PENDING_USERS_KEY] = persisted_pending
    # Snapshot of reload-relevant keys used by the update listener to detect
    # whether a reload is actually needed (vs a pending_users-only write).
    hass.data[DOMAIN][entry.entry_id][KEY_CONFIG_SNAPSHOT] = {
        CONF_CHANNEL_ACCESS_TOKEN: entry.data.get(CONF_CHANNEL_ACCESS_TOKEN),
        CONF_CHANNEL_SECRET: entry.data.get(CONF_CHANNEL_SECRET),
        RECIPIENTS_KEY: dict(entry.data.get(RECIPIENTS_KEY, {})),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_listener))

    def _fire_send_error(
        entity_id: str,
        recipient_name: str,
        error_type: str,
        error_message: str,
        http_status: int | None = None,
    ) -> None:
        """Fire a line_bot_send_failed event on the HA bus."""
        import time
        hass.bus.async_fire(
            EVENT_SEND_FAILED,
            {
                "entity_id": entity_id,
                "recipient_name": recipient_name,
                "error_type": error_type,
                "error_message": error_message,
                "http_status": http_status,
                "timestamp": int(time.time()),
            },
        )

    async def handle_send_message(call: ServiceCall) -> None:
        """Handle the line_ha_bot.send_message service call.

        Supports the full LINE message feature set. Message type priority order:
          1. template (buttons or confirm) - if template_type is set
          2. flex - if flex_message is set
          3. text only, then one of: image, location, audio, video, sticker

        For reply token sends, uses the free Reply API. Otherwise uses Push API.
        Fires line_bot_send_failed on any error. Supports multiple entity_id targets.
        """
        entity_ids = call.data.get("entity_id", [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]

        message = call.data.get("message")
        title = call.data.get("title")
        image_url = call.data.get(ATTR_IMAGE_URL)
        sticker_package_id = call.data.get(ATTR_STICKER_PACKAGE_ID)
        sticker_id = call.data.get(ATTR_STICKER_ID)
        reply_token = call.data.get(ATTR_REPLY_TOKEN)
        flex_message = call.data.get(ATTR_FLEX_MESSAGE)
        flex_alt_text = call.data.get(ATTR_FLEX_ALT_TEXT, DEFAULT_FLEX_ALT_TEXT)
        location_title = call.data.get(ATTR_LOCATION_TITLE)
        location_address = call.data.get(ATTR_LOCATION_ADDRESS)
        location_latitude = call.data.get(ATTR_LOCATION_LATITUDE)
        location_longitude = call.data.get(ATTR_LOCATION_LONGITUDE)
        has_location = location_latitude is not None and location_longitude is not None
        audio_url = call.data.get(ATTR_AUDIO_URL)
        audio_duration = call.data.get(ATTR_AUDIO_DURATION, 1)
        video_url = call.data.get(ATTR_VIDEO_URL)
        video_preview_url = call.data.get(ATTR_VIDEO_PREVIEW_URL)
        template_type = call.data.get(ATTR_TEMPLATE_TYPE)
        template_title = call.data.get(ATTR_TEMPLATE_TITLE)
        template_default_url = call.data.get(ATTR_TEMPLATE_DEFAULT_URL)
        buttons = call.data.get(ATTR_BUTTONS, [])
        quick_replies = call.data.get(ATTR_QUICK_REPLIES, [])

        registry = er.async_get(hass)
        platform_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        entity_map = {e.entity_id: e for e in platform_entries}

        token = entry.data.get(CONF_CHANNEL_ACCESS_TOKEN, "")
        # Build a map from entity_id to user_id via unique_id in the entity registry
        eid_to_user_id = {}
        for eid, entry_obj in entity_map.items():
            unique_id = entry_obj.unique_id or ""
            prefix = f"{DOMAIN}_"
            if unique_id.startswith(prefix):
                eid_to_user_id[eid] = unique_id[len(prefix):]

        messages = []

        if template_type:
            built_buttons = []
            for btn in buttons:
                action_type = btn.get("action", "message")
                if action_type == "uri":
                    built_buttons.append({
                        "type": "uri",
                        "label": btn["label"],
                        "uri": btn["data"],
                    })
                elif action_type == "postback":
                    built_buttons.append({
                        "type": "postback",
                        "label": btn["label"],
                        "data": btn["data"],
                        "displayText": btn.get("display_text", btn["label"]),
                    })
                else:
                    built_buttons.append({
                        "type": "message",
                        "label": btn["label"],
                        "text": btn["data"],
                    })

            if template_type == "confirm":
                template = {
                    "type": "confirm",
                    "text": message or "",
                    "actions": built_buttons,
                }
            else:
                template = {
                    "type": "buttons",
                    "text": message or "",
                    "actions": built_buttons,
                }
                if template_title:
                    template["title"] = template_title
                if image_url:
                    template["thumbnailImageUrl"] = image_url
                if template_default_url:
                    template["defaultAction"] = {
                        "type": "uri",
                        "label": DEFAULT_ACTION_LABEL,
                        "uri": template_default_url,
                    }

            messages.append({
                "type": "template",
                "altText": flex_alt_text,
                "template": template,
            })

        elif flex_message:
            if message:
                text = f"{title}\n{message}" if title else message
                messages.append({"type": "text", "text": text})
            messages.append({
                "type": "flex",
                "altText": flex_alt_text,
                "contents": flex_message,
            })
        else:
            if message:
                text = f"{title}\n{message}" if title else message
                messages.append({"type": "text", "text": text})
            if image_url:
                messages.append({
                    "type": "image",
                    "originalContentUrl": image_url,
                    "previewImageUrl": image_url,
                })
            elif has_location:
                messages.append({
                    "type": "location",
                    "title": location_title or DEFAULT_LOCATION_TITLE,
                    "address": location_address or "",
                    "latitude": location_latitude,
                    "longitude": location_longitude,
                })
            elif audio_url:
                if not audio_duration:
                    _LOGGER.error(
                        "LINE Bot send_message: audio_duration is required when sending audio "
                        "and must be a positive integer in milliseconds."
                    )
                    return
                messages.append({
                    "type": "audio",
                    "originalContentUrl": audio_url,
                    "duration": audio_duration,
                })
            elif video_url:
                messages.append({
                    "type": "video",
                    "originalContentUrl": video_url,
                    "previewImageUrl": video_preview_url or "",
                })
            elif sticker_package_id and sticker_id:
                messages.append({
                    "type": "sticker",
                    "packageId": str(sticker_package_id),
                    "stickerId": str(sticker_id),
                })

        if not messages:
            _LOGGER.error("LINE Bot send_message: no message content provided")
            return

        # Attach quick replies to the last message object if provided.
        # Quick reply chips appear above the LINE keyboard after the message
        # and disappear once tapped. Supported on all message types.
        if quick_replies:
            built_qr = []
            for qr in quick_replies:
                action_type = qr.get("action", "message")
                if action_type == "uri":
                    built_qr.append({
                        "type": "action",
                        "action": {
                            "type": "uri",
                            "label": qr["label"],
                            "uri": qr["data"],
                        },
                    })
                elif action_type == "postback":
                    built_qr.append({
                        "type": "action",
                        "action": {
                            "type": "postback",
                            "label": qr["label"],
                            "data": qr["data"],
                            "displayText": qr.get("display_text", qr["label"]),
                        },
                    })
                else:
                    built_qr.append({
                        "type": "action",
                        "action": {
                            "type": "message",
                            "label": qr["label"],
                            "text": qr["data"],
                        },
                    })
            messages[-1]["quickReply"] = {"items": built_qr}

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        session = async_get_clientsession(hass)

        user_id_to_name = {
            r["user_id"]: name
            for name, r in entry.data.get(RECIPIENTS_KEY, {}).items()
        }

        for eid in entity_ids:
            user_id = eid_to_user_id.get(eid)
            if not user_id:
                _LOGGER.error(
                    "LINE Bot send_message: could not resolve entity_id %s to a LINE user ID",
                    eid,
                )
                continue

            if reply_token:
                url = LINE_REPLY_URL
                payload = {"replyToken": reply_token, "messages": messages}
            else:
                url = LINE_PUSH_URL
                payload = {"to": user_id, "messages": messages}

            try:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        continue
                    body = await resp.text()
                    if resp.status == 401:
                        msg = (
                            "LINE Bot token is invalid or revoked. "
                            "Please update your Channel Access Token via the integration options."
                        )
                        _LOGGER.error(msg)
                        _fire_send_error(eid, user_id_to_name.get(user_id, eid), "token_invalid", msg, 401)
                    elif resp.status == 400:
                        if reply_token:
                            msg = (
                                "Reply token has expired or has already been used. "
                                "Reply tokens are valid for 30 seconds and single-use only. "
                                "Use push messaging for delayed responses."
                            )
                            _LOGGER.error("LINE Bot reply failed for %s: %s", eid, msg)
                            _fire_send_error(eid, user_id_to_name.get(user_id, eid), "reply_token_expired", msg, 400)
                        else:
                            msg = f"Bad request (check user ID and message format): {body}"
                            _LOGGER.error("LINE Bot bad request for %s: %s", eid, body)
                            _fire_send_error(eid, user_id_to_name.get(user_id, eid), "bad_request", msg, 400)
                    else:
                        msg = f"HTTP {resp.status}: {body}"
                        _LOGGER.error(
                            "LINE Bot %s failed for %s: HTTP %s - %s",
                            "reply" if reply_token else "push",
                            eid, resp.status, body,
                        )
                        _fire_send_error(eid, user_id_to_name.get(user_id, eid), "http_error", msg, resp.status)
            except aiohttp.ClientError as err:
                msg = str(err)
                _LOGGER.error("LINE Bot connection error for %s: %s", eid, err)
                _fire_send_error(eid, user_id_to_name.get(user_id, eid), "connection_error", msg)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        handle_send_message,
        schema=vol.Schema({
            vol.Required("entity_id"): vol.Any(str, [str]),
            vol.Optional("message"): str,
            vol.Optional("title"): str,
            vol.Optional(ATTR_IMAGE_URL): str,
            vol.Optional(ATTR_STICKER_PACKAGE_ID): vol.Coerce(str),
            vol.Optional(ATTR_STICKER_ID): vol.Coerce(str),
            vol.Optional(ATTR_REPLY_TOKEN): str,
            vol.Optional(ATTR_FLEX_MESSAGE): dict,
            vol.Optional(ATTR_FLEX_ALT_TEXT): str,
            vol.Optional(ATTR_LOCATION_TITLE): str,
            vol.Optional(ATTR_LOCATION_ADDRESS): str,
            vol.Optional(ATTR_LOCATION_LATITUDE): vol.Coerce(float),
            vol.Optional(ATTR_LOCATION_LONGITUDE): vol.Coerce(float),
            vol.Optional(ATTR_TEMPLATE_TYPE): vol.In(["buttons", "confirm"]),
            vol.Optional(ATTR_TEMPLATE_TITLE): str,
            vol.Optional(ATTR_TEMPLATE_DEFAULT_URL): str,
            vol.Optional(ATTR_BUTTONS): [
                vol.Schema({
                    vol.Required("label"): str,
                    vol.Required("action"): vol.In(["message", "postback", "uri"]),
                    vol.Required("data"): str,
                    vol.Optional("display_text"): str,
                })
            ],
            vol.Optional(ATTR_AUDIO_URL): str,
            vol.Optional(ATTR_AUDIO_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1)),
            vol.Optional(ATTR_VIDEO_URL): str,
            vol.Optional(ATTR_VIDEO_PREVIEW_URL): str,
            vol.Optional(ATTR_QUICK_REPLIES): [
                vol.Schema({
                    vol.Required("label"): str,
                    vol.Required("action"): vol.In(["message", "postback", "uri"]),
                    vol.Required("data"): str,
                    vol.Optional("display_text"): str,
                })
            ],
        }),
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a LINE Bot config entry.

    Tears down the notify platform and removes per-entry runtime data. The
    webhook view is intentionally left registered because HA does not support
    unregistering HTTP views at runtime.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.services.async_remove(DOMAIN, SERVICE_SEND_MESSAGE)
    return unload_ok


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when credentials or recipients change.

    Triggered whenever the config entry data is changed. Compares the new entry
    data against the snapshot taken at setup time. If only pending_users changed,
    skips the reload since pending_users does not affect platforms or the service.
    A full reload is only triggered when the token, secret, or recipients change.
    """
    snapshot = (
        hass.data.get(DOMAIN, {})
        .get(entry.entry_id, {})
        .get(KEY_CONFIG_SNAPSHOT, {})
    )
    if (
        entry.data.get(CONF_CHANNEL_ACCESS_TOKEN) == snapshot.get(CONF_CHANNEL_ACCESS_TOKEN)
        and entry.data.get(CONF_CHANNEL_SECRET) == snapshot.get(CONF_CHANNEL_SECRET)
        and entry.data.get(RECIPIENTS_KEY) == snapshot.get(RECIPIENTS_KEY)
    ):
        _LOGGER.debug("LINE Bot: config unchanged (pending_users write only), skipping reload")
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def _get_profile(hass: HomeAssistant, token: str, user_id: str) -> str | None:
    """Fetch the LINE display name for a given user ID.

    Uses the Messaging API GET /v2/bot/profile/{userId} endpoint. Returns the
    displayName string on success, or None if the request fails (e.g. invalid
    token, network error, or user has not added the bot as a friend).

    Note: The LINE profile API only returns displayName, userId, pictureUrl,
    and statusMessage for regular users. The LINE ID (e.g. 'secret-friend')
    is not available via this endpoint.
    """
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {token}"}
    url = LINE_PROFILE_URL.format(user_id=user_id)
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("displayName", user_id)
            return None
    except Exception:
        return None


async def _get_group_summary(hass: HomeAssistant, token: str, group_id: str) -> str | None:
    """Fetch the display name for a LINE group.

    Uses GET /v2/bot/group/{groupId}/summary. Returns the groupName string
    on success, or None if the request fails (e.g. bot not yet a member).
    """
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {token}"}
    url = LINE_GROUP_SUMMARY_URL.format(group_id=group_id)
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("groupName")
            return None
    except Exception:
        return None


async def _get_bot_info(hass: HomeAssistant, token: str) -> str | None:
    """Fetch the bot's own LINE user ID from GET /v2/bot/info.

    Returns the userId string on success, or None on failure.
    """
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with session.get(LINE_BOT_INFO_URL, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("userId")
            return None
    except Exception:
        return None


class LineMessagingWebhookView(HomeAssistantView):
    """Permanent HTTP view that receives webhook events from the LINE Platform.

    Registered at LINE_WEBHOOK_PATH (/api/line_ha_bot/webhook). LINE must be
    configured to POST events to this URL via the LINE Developers Console.

    All real events are signature-verified before processing. Two special cases
    bypass entry and signature checks:
      1. Empty events array: LINE's Verify button health check - return 200.
      2. All-zeros reply token: LINE's internal test event - return 200.

    For known recipients (user or group):
      - message events fire line_bot_message_received with full payload including
        content_url and message_id for media types (image, video, audio).
      - postback events fire line_bot_message_received with postback_data.
      - Other event types (follow, unfollow, join, leave) are silently ignored.

    For unknown senders, the user ID or group ID is captured into the per-entry
    pending_users dict so the options flow can present them as recipient candidates.
    """

    url = LINE_WEBHOOK_PATH
    name = "api:line_ha_bot:webhook"
    requires_auth = False  # Must be False - LINE cannot authenticate with HA credentials

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the view with a reference to hass."""
        self.hass = hass

    async def post(self, request: Request) -> Response:
        """Handle an incoming POST from the LINE Platform."""
        body = await request.read()
        signature = request.headers.get("X-Line-Signature", "")

        _LOGGER.debug(
            "LINE Bot webhook received: %d bytes, signature present: %s",
            len(body),
            bool(signature),
        )

        try:
            data = json.loads(body)
        except Exception:
            _LOGGER.error("LINE Bot webhook: could not parse JSON body")
            raise HTTPBadRequest

        events = data.get("events", [])

        # Empty events array = LINE's Verify button health check. Return 200 immediately.
        # This also fires before any config entry exists, so we must not look one up.
        if not events:
            _LOGGER.debug("LINE Bot webhook: empty events (Verify request), returning 200")
            return Response(text="OK", status=200)

        # All-zeros reply token = LINE internal test event. Return 200 immediately.
        if all(e.get("replyToken") == "00000000000000000000000000000000" for e in events):
            _LOGGER.debug("LINE Bot webhook: test reply token event, returning 200")
            return Response(text="OK", status=200)

        entry_id = self._get_entry_id()
        if entry_id is None:
            # No config entry yet. The config flow no longer adds recipients
            # during setup, so we just return 200 to keep LINE happy.
            _LOGGER.debug("LINE Bot webhook: no active config entry, returning 200")
            return Response(text="OK", status=200)

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            _LOGGER.warning("LINE Bot webhook: config entry %s not found", entry_id)
            raise HTTPForbidden

        channel_secret = entry.data.get(CONF_CHANNEL_SECRET, "")
        token = entry.data.get(CONF_CHANNEL_ACCESS_TOKEN, "")

        if not self._verify_signature(channel_secret, body, signature):
            _LOGGER.warning(
                "LINE Bot webhook signature verification failed. "
                "Check that your channel secret is correct."
            )
            raise HTTPBadRequest

        pending = self.hass.data[DOMAIN][entry_id][PENDING_USERS_KEY]
        _LOGGER.debug(
            "LINE Bot webhook: processing %d events, current pending count: %d",
            len(events), len(pending)
        )

        recipients = entry.data.get(RECIPIENTS_KEY, {})
        user_id_to_recipient = {
            r["user_id"]: {"name": name, "display_name": r.get("display_name", "")}
            for name, r in recipients.items()
        }
        registry = er.async_get(self.hass)
        platform_entries = er.async_entries_for_config_entry(registry, entry_id)
        user_id_to_entity_id = {
            e.unique_id[len(DOMAIN) + 1:]: e.entity_id
            for e in platform_entries
            if e.unique_id and e.unique_id.startswith(f"{DOMAIN}_")
        }

        for event in events:
            if event.get("replyToken") == "00000000000000000000000000000000":
                _LOGGER.debug("LINE Bot webhook: skipping test event")
                continue

            source = event.get("source", {})
            source_type = source.get("type", "user")
            user_id = source.get("userId")
            group_id = source.get("groupId") if source_type == "group" else None
            if not user_id:
                continue

            event_type = event.get("type", "")
            timestamp = int(event.get("timestamp", 0) / 1000)

            # For group events match on group_id; for user events match on user_id
            lookup_id = group_id if group_id else user_id
            if lookup_id in user_id_to_recipient:
                recipient = user_id_to_recipient[lookup_id]
                reply_token = event.get("replyToken")
                if event_type == "message":
                    msg = event.get("message", {})
                    msg_type = msg.get("type", "")
                    message_id = msg.get("id")
                    has_content = msg_type in ("image", "video", "audio", "file")
                    content_url = (
                        LINE_CONTENT_URL.format(message_id=message_id)
                        if has_content and message_id else None
                    )
                    self.hass.bus.async_fire(
                        EVENT_MESSAGE_RECEIVED,
                        {
                            "type": msg_type,
                            "user_id": user_id,
                            "group_id": group_id,
                            "entity_id": user_id_to_entity_id.get(lookup_id),
                            "recipient_name": recipient["name"],
                            "display_name": recipient["display_name"],
                            "message_text": msg.get("text") if msg_type == "text" else None,
                            "message_id": message_id,
                            "content_url": content_url,
                            "postback_data": None,
                            "reply_token": reply_token,
                            "timestamp": timestamp,
                        },
                    )
                    _LOGGER.debug(
                        "LINE Bot webhook: fired line_bot_message_received for %s (%s)",
                        recipient["name"],
                        msg_type,
                    )
                elif event_type == "postback":
                    self.hass.bus.async_fire(
                        EVENT_MESSAGE_RECEIVED,
                        {
                            "type": "postback",
                            "user_id": user_id,
                            "group_id": group_id,
                            "entity_id": user_id_to_entity_id.get(lookup_id),
                            "recipient_name": recipient["name"],
                            "display_name": recipient["display_name"],
                            "message_text": None,
                            "postback_data": event.get("postback", {}).get("data"),
                            "reply_token": reply_token,
                            "timestamp": timestamp,
                        },
                    )
                    _LOGGER.debug(
                        "LINE Bot webhook: fired line_bot_message_received (postback) for %s",
                        recipient["name"],
                    )
                else:
                    _LOGGER.debug(
                        "LINE Bot webhook: ignoring event type '%s' for known recipient %s",
                        event_type,
                        lookup_id,
                    )
                continue

            line_id = group_id if group_id else user_id
            if not line_id:
                continue

            if line_id in pending:
                _LOGGER.debug("LINE Bot webhook: %s %s already in pending", source_type, line_id)
                continue

            if source_type == "group":
                _LOGGER.debug("LINE Bot webhook: capturing group %s", line_id)
                display_name = await _get_group_summary(self.hass, token, line_id)
            else:
                _LOGGER.debug("LINE Bot webhook: capturing user %s", line_id)
                display_name = await _get_profile(self.hass, token, line_id)
            pending[line_id] = display_name or line_id
            _LOGGER.info(
                "LINE Bot webhook: captured %s %s (%s)",
                source_type,
                line_id,
                pending[line_id],
            )
            # Persist pending_users to config entry data so captures survive HA restarts.
            # This writes only pending_users; the update listener detects this and skips reload.
            new_data = dict(entry.data)
            new_data[PENDING_USERS_KEY] = dict(pending)
            self.hass.config_entries.async_update_entry(entry, data=new_data)

        return Response(text="OK", status=200)

    def _get_entry_id(self) -> str | None:
        """Find the active config entry ID from hass.data.

        Looks for the first entry in hass.data[DOMAIN] whose value is a dict
        containing PENDING_USERS_KEY, which is the marker we set in
        async_setup_entry. Returns None if no such entry exists (i.e. during
        initial setup before any entry has been created).
        """
        entry_data = self.hass.data.get(DOMAIN, {})
        for key, val in entry_data.items():
            if isinstance(val, dict) and PENDING_USERS_KEY in val:
                return key
        return None

    def _verify_signature(self, secret: str, body: bytes, signature: str) -> bool:
        """Verify the X-Line-Signature header against the request body.

        LINE signs each webhook request body using HMAC-SHA256 with the channel
        secret as the key, then base64-encodes the result. We compute the same
        value and compare using hmac.compare_digest to prevent timing attacks.

        Returns True if the signature is valid, False otherwise.
        """
        if not signature or not secret:
            _LOGGER.warning(
                "LINE Bot webhook: missing signature (%s) or secret (%s)",
                bool(signature),
                bool(secret),
            )
            return False
        expected = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        expected_b64 = base64.b64encode(expected).decode("utf-8")
        return hmac.compare_digest(expected_b64, signature)