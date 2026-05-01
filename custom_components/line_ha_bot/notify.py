"""LINE Bot notify platform for Home Assistant.

This module implements the HA notify entity platform. One LineMessagingNotifyEntity
is created per configured recipient. Each entity exposes the standard notify.send_message
action supporting message and title only.

For richer messages (images, stickers, flex cards, locations, reply tokens) use the
line_ha_bot.send_message service instead.

Example automation action:
  action: notify.send_message
  target:
    entity_id: notify.line_bot_david
  data:
    message: "Front door opened"
    title: "Security Alert"
"""

from __future__ import annotations

import logging
import aiohttp

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_CHANNEL_ACCESS_TOKEN,
    LINE_PUSH_URL,
    RECIPIENTS_KEY,
    EVENT_SEND_FAILED,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LINE Bot notify entities from a config entry.

    Creates one LineMessagingNotifyEntity per recipient stored in the config
    entry. Also removes any stale entities from previous loads whose LINE user
    IDs are no longer in the recipients dict (e.g. after a recipient is removed
    via the options flow). Called by HA when the entry is loaded or reloaded.
    """
    token = entry.data[CONF_CHANNEL_ACCESS_TOKEN]
    recipients = entry.data.get(RECIPIENTS_KEY, {})

    # Remove stale entities from the registry whose user IDs are no longer
    # in the recipients dict. Each entity has a unique_id of DOMAIN_<user_id>.
    registry = er.async_get(hass)
    current_user_ids = {r["user_id"] for r in recipients.values()}
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    for entity_entry in entries:
        unique_id = entity_entry.unique_id or ""
        prefix = f"{DOMAIN}_"
        if unique_id.startswith(prefix):
            user_id = unique_id[len(prefix):]
            if user_id not in current_user_ids:
                registry.async_remove(entity_entry.entity_id)

    entities = [
        LineMessagingNotifyEntity(
            hass, entry, name, r.get("friendly_name", name), r["user_id"], token
        )
        for name, r in recipients.items()
    ]
    async_add_entities(entities)


class LineMessagingNotifyEntity(NotifyEntity):
    """A notify entity representing a single LINE recipient (user or group).

    Entity ID format:  notify.line_bot_<recipient_name_slugified>
    Unique ID format:  line_ha_bot_<line_user_id_or_group_id>
    Display name:      friendly_name (may contain emoji and unicode)

    The unique ID is based on the LINE ID (not the recipient name) so that
    renaming a recipient does not create a duplicate entity. The friendly_name
    is set as _attr_name so the HA UI shows the LINE display name rather than
    the ASCII entity name.
    """

    _attr_has_entity_name = True
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        recipient_name: str,
        friendly_name: str,
        user_id: str,
        token: str,
    ) -> None:
        """Initialise the notify entity."""
        self.hass = hass
        self._recipient_name = recipient_name
        self._user_id = user_id
        self._token = token
        self._attr_name = friendly_name
        self._attr_unique_id = f"{DOMAIN}_{user_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="LINE HA Bot",
            manufacturer="sfox38",
            model="Messaging API",
        )

    async def async_send_message(
        self,
        message: str,
        title: str | None = None,
        **kwargs,
    ) -> None:
        """Send a plain text message to this LINE recipient via the Push API.

        Supports message and optional title only. For richer messages
        (images, audio, video, stickers, flex cards, locations, templates,
        reply tokens) use the line_ha_bot.send_message service instead.
        Fires line_bot_send_failed on any error.
        """
        import time
        entity_id = self.entity_id

        def _fire_error(error_type: str, error_message: str, http_status: int | None = None) -> None:
            self.hass.bus.async_fire(
                EVENT_SEND_FAILED,
                {
                    "entity_id": entity_id,
                    "recipient_name": self._recipient_name,
                    "error_type": error_type,
                    "error_message": error_message,
                    "http_status": http_status,
                    "timestamp": int(time.time()),
                },
            )

        text = f"{title}\n{message}" if title else message
        payload = {
            "to": self._user_id,
            "messages": [{"type": "text", "text": text}],
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                LINE_PUSH_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return
                body = await resp.text()
                if resp.status == 401:
                    msg = (
                        f"Token is invalid or revoked for recipient {self._recipient_name}. "
                        "Please update your Channel Access Token via the integration options."
                    )
                    _LOGGER.error(msg)
                    _fire_error("token_invalid", msg, 401)
                elif resp.status == 400:
                    msg = f"Bad request for {self._recipient_name} (check user ID and message format): {body}"
                    _LOGGER.error(msg)
                    _fire_error("bad_request", msg, 400)
                else:
                    msg = f"Push failed for {self._recipient_name}: HTTP {resp.status} - {body}"
                    _LOGGER.error(msg)
                    _fire_error("http_error", msg, resp.status)
        except aiohttp.ClientError as err:
            msg = str(err)
            _LOGGER.error("LINE Bot connection error for %s: %s", self._recipient_name, err)
            _fire_error("connection_error", msg)