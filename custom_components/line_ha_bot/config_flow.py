"""Config flow and options flow for the LINE Bot integration.

This module handles all UI-driven configuration via the HA integrations page.

Config flow steps (initial setup) - intentionally minimal:
  1. user          - Enter Channel Access Token and Channel Secret.
  2. webhook_info  - Display the webhook URL to paste into LINE Developers Console.
                     The config entry is created on Submit. Recipients are added
                     separately via the options flow after setup completes.

Options flow steps (accessed via the gear icon on the integration card):
  init             - Menu: Add recipient / Remove recipient / Update credentials.
  add_recipient    - Progress spinner waiting for a LINE message or group event.
                     Skips spinner if messages are already captured in pending_users.
  select_recipient - Dropdown of captured LINE users and groups; enter an HA name.
                     Includes "Clear all pending" sentinel and "Add another" checkbox.
  remove_recipient - Dropdown of current recipients to delete.
  rotate_token     - Update Channel Access Token and/or Channel Secret.

Recipient storage format:
  Recipients are stored in the config entry data dict as:
    {"name": {"user_id": "U...", "display_name": "...", "type": "user"}}
  Groups use IDs starting with "C" and type "group". Users use IDs starting with
  "U" and type "user". The type is detected automatically from the LINE ID prefix.

Recipient name rules:
  Names must contain only ASCII letters, digits, spaces, hyphens, and underscores.
  Non-ASCII characters (Thai, emoji, etc.) are rejected with a clear error message.
  The _sanitize_name() helper suggests a safe default: emojis are converted to their
  Unicode name (e.g. nerd), non-ASCII scripts are romanized via slugify().

Webhook-based capture:
  The permanent webhook in __init__.py captures LINE user IDs and group IDs into
  the per-entry hass.data[DOMAIN][entry_id][PENDING_USERS_KEY] dict whenever
  someone messages the bot or sends a message in a registered group. The options
  flow polls this dict every 2 seconds and automatically advances to the select
  step when a user or group appears.
"""

import asyncio
import re
import unicodedata

from homeassistant.util import slugify
import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import network
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import (
    DOMAIN,
    CONF_CHANNEL_ACCESS_TOKEN,
    CONF_CHANNEL_SECRET,
    CONF_RECIPIENT_NAME,
    CONF_FRIENDLY_NAME,
    CONF_USER_ID,
    LINE_TOKEN_VERIFY_URL,
    LINE_WEBHOOK_PATH,
    PENDING_USERS_KEY,
    KEY_VIEW_REGISTERED,
    CLEAR_PENDING,
    CLEAR_PENDING_LABEL,
    RECIPIENTS_KEY,
)

CONF_ACTION = "action"
ACTION_ADD = "add_recipient"
ACTION_REMOVE = "remove_recipient"
ACTION_ROTATE = "rotate_token"

CONF_ADD_ANOTHER = "add_another"

# Polling task: checks every _POLL_INTERVAL seconds for up to _POLL_ITERATIONS cycles.
# Total wait time: 300 * 2 = 600 seconds (10 minutes).
_POLL_ITERATIONS = 300
_POLL_INTERVAL = 2


async def _verify_token(hass: HomeAssistant, token: str) -> str | None:
    """Verify a LINE channel access token against the LINE oauth endpoint.

    Returns None if valid (HTTP 200), or an error key string if not.
    """
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            LINE_TOKEN_VERIFY_URL,
            data={"access_token": token},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                return None
            return "invalid_token"
    except aiohttp.ClientError:
        return "cannot_connect"


def _get_external_url(hass: HomeAssistant) -> str | None:
    """Return the HA external SSL URL, or None if not configured."""
    try:
        return network.get_url(
            hass,
            allow_internal=False,
            allow_ip=False,
            require_ssl=True,
        )
    except network.NoURLAvailableError:
        return None


def _is_valid_name(name: str) -> bool:
    """Return True if name is safe to use as an HA recipient name.

    Accepts only ASCII letters, digits, spaces, hyphens, and underscores.
    """
    return bool(name) and bool(re.match(r'^[a-zA-Z0-9 _-]+$', name))


def _name_slug_conflicts(name: str, existing_names: dict) -> bool:
    """Return True if the slugified name conflicts with any existing recipient.

    Prevents cases like "Steve" and "steve" both producing notify.line_bot_steve.
    """
    new_slug = slugify(name)
    return any(slugify(existing) == new_slug for existing in existing_names)


def _is_emoji(char: str) -> bool:
    """Return True if char is an emoji or pictographic symbol.

    Uses the Unicode category and block ranges to detect emoji characters.
    """
    cp = ord(char)
    return (
        0x1F300 <= cp <= 0x1FAFF  # Misc symbols, emoticons, transport, etc.
        or 0x2600 <= cp <= 0x27BF  # Misc symbols and dingbats
        or 0xFE00 <= cp <= 0xFE0F  # Variation selectors (emoji modifiers)
        or 0x1F900 <= cp <= 0x1F9FF  # Supplemental symbols
        or 0x1FA00 <= cp <= 0x1FA6F  # Chess, etc.
    )


def _sanitize_name(display_name: str) -> str:
    """Derive a suggested HA entity name from a LINE display name.

     Two-step approach:
      1. Replace each emoji character with the first word of its Unicode name
         (e.g. U+1F913 NERD FACE becomes "nerd"). This preserves emoji semantics
         that would otherwise be silently dropped.
      2. Pass the result through HA's slugify(), which romanizes Thai, CJK,
         Japanese, and other non-ASCII scripts and produces a safe ASCII slug.

    Examples:
      "David"           -> "David"
      "สวัสดี"           -> "sawasdi" (or similar romanization)
      "🤓"              -> "nerd"
      "สวัสดี 🤓 David" -> "sawasdi_nerd_david"
    """
    parts = []
    for char in display_name:
        if _is_emoji(char):
            try:
                name = unicodedata.name(char, "")
                if name:
                    # Surround emoji name with spaces so slugify treats it as a separate word
                    first_word = name.split()[0].lower()
                    parts.append(f" {first_word} ")
            except (ValueError, TypeError):
                pass
        else:
            parts.append(char)
    preprocessed = "".join(parts)
    return slugify(preprocessed, separator="_")


class LineMessagingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration flow for LINE Bot.

    Intentionally kept to two steps: credentials and webhook setup.
    The config entry is created with empty recipients after webhook verification.
    All recipient management is done via the options flow after installation.
    """

    VERSION = 1

    def __init__(self):
        """Initialise flow state."""
        self._token: str | None = None
        self._secret: str | None = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Step 1: Collect and verify LINE API credentials.

        Asks for the Channel Access Token and Channel Secret, both found on the
        Basic Settings tab of the LINE Developers Console. Verifies the token
        against LINE's oauth endpoint before proceeding. Only one instance of
        this integration is allowed (enforced via unique_id).
        """
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors = {}
        if user_input is not None:
            token = user_input[CONF_CHANNEL_ACCESS_TOKEN].strip()
            secret = user_input[CONF_CHANNEL_SECRET].strip()
            error = await _verify_token(self.hass, token)
            if error:
                errors["base"] = error
            elif not secret:
                errors[CONF_CHANNEL_SECRET] = "invalid_secret"
            else:
                self._token = token
                self._secret = secret
                return await self.async_step_webhook_info()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_CHANNEL_ACCESS_TOKEN): str,
                vol.Required(CONF_CHANNEL_SECRET): str,
            }),
            errors=errors,
        )

    async def async_step_webhook_info(self, user_input=None) -> FlowResult:
        """Step 2: Show webhook URL and create the config entry on Submit.

        Registers the webhook view immediately so LINE's Verify button succeeds
        before the user clicks Submit. The config entry is created with empty
        recipients. Recipients are added via the options flow (gear icon) after
        installation.
        """
        from . import LineMessagingWebhookView

        external_url = _get_external_url(self.hass)
        if external_url is None:
            return self.async_abort(reason="no_external_url")

        webhook_url = f"{external_url}{LINE_WEBHOOK_PATH}"

        self.hass.data.setdefault(DOMAIN, {})
        if not self.hass.data[DOMAIN].get(KEY_VIEW_REGISTERED):
            self.hass.http.register_view(LineMessagingWebhookView(self.hass))
            self.hass.data[DOMAIN][KEY_VIEW_REGISTERED] = True

        if user_input is not None:
            return self.async_create_entry(
                title="LINE Bot",
                data={
                    CONF_CHANNEL_ACCESS_TOKEN: self._token,
                    CONF_CHANNEL_SECRET: self._secret,
                    RECIPIENTS_KEY: {},
                },
            )

        return self.async_show_form(
            step_id="webhook_info",
            data_schema=vol.Schema({
                vol.Optional("confirmed", default=False): bool,
            }),
            description_placeholders={"webhook_url": webhook_url},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler for this config entry."""
        return LineMessagingOptionsFlow(config_entry)


class LineMessagingOptionsFlow(config_entries.OptionsFlow):
    """Handle the options flow for LINE Bot (gear icon on the integration card).

    Provides three actions:
      - Add a recipient:    Webhook-based capture flow with spinner.
      - Remove a recipient: Dropdown of current recipients to delete.
      - Update token:       Replace the Channel Access Token.

    All changes are saved to the config entry data dict. The update listener in
    __init__.py triggers a reload so notify entities reflect the new state.
    """

    def __init__(self, config_entry):
        """Initialise with the current config entry state."""
        self._config_entry = config_entry
        self._recipients: dict[str, dict] = dict(config_entry.data.get(RECIPIENTS_KEY, {}))
        self._token = config_entry.data.get(CONF_CHANNEL_ACCESS_TOKEN, "")
        self._secret = config_entry.data.get(CONF_CHANNEL_SECRET, "")
        self._poll_task: asyncio.Task | None = None

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Show the action menu: Add / Remove / Update token."""
        if user_input is not None:
            action = user_input[CONF_ACTION]
            if action == ACTION_ADD:
                return await self.async_step_add_recipient()
            if action == ACTION_REMOVE:
                return await self.async_step_remove_recipient()
            if action == ACTION_ROTATE:
                return await self.async_step_rotate_token()

        action_options = {
            ACTION_ADD: "Add a recipient",
            ACTION_REMOVE: "Remove a recipient",
            ACTION_ROTATE: "Update credentials (token and secret)",
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_ACTION): vol.In(action_options),
            }),
        )

    async def async_step_add_recipient(self, user_input=None) -> FlowResult:
        """Show spinner waiting for a LINE message, or skip to select if pending exist.

        If pending_users already has entries AND no poll task has been started yet
        (meaning we are entering fresh, not returning from a progress step), skip
        directly to select_recipient.

        Once a poll task has been started we must always go through the
        async_show_progress / async_show_progress_done transition, because HA
        does not allow a progress step to transition directly to a form step.
        The poll task itself detects pending_users and returns early, causing
        async_show_progress_done to fire immediately.
        """
        # Fresh entry with pending users already present - skip spinner entirely.
        if self._poll_task is None and self._get_pending_users():
            return await self.async_step_select_recipient()

        if self._poll_task is None:
            self._poll_task = self.hass.async_create_task(
                self._poll_for_pending_user()
            )

        if not self._poll_task.done():
            return self.async_show_progress(
                step_id="add_recipient",
                progress_action="waiting_for_message",
                progress_task=self._poll_task,
            )

        self._poll_task = None
        return self.async_show_progress_done(next_step_id="select_recipient")

    async def _poll_for_pending_user(self) -> None:
        """Background task that polls pending_users until a user ID appears.

        Checks every _POLL_INTERVAL seconds for up to _POLL_ITERATIONS cycles.
        Returns as soon as at least one user ID is present. If the timeout is
        reached with no user, the task completes with pending_users empty, and
        select_recipient will redirect back to add_recipient for a fresh poll.
        """
        for _ in range(_POLL_ITERATIONS):
            if self._get_pending_users():
                return
            await asyncio.sleep(_POLL_INTERVAL)

    async def async_step_select_recipient(self, user_input=None) -> FlowResult:
        """Pick a captured LINE account or group, name it, and optionally add another.

        Shows two name fields: recipient_name (ASCII only, used for entity ID slugification)
        and friendly_name (any characters including emoji and unicode, used as the HA
        display name). friendly_name defaults to the LINE display name. The dropdown
        includes all pending users and groups plus a "— Clear all pending —" sentinel.
        The recipient type (user or group) is detected from the LINE ID prefix (U vs C).

        Selecting CLEAR_PENDING wipes pending_users and goes back to the spinner.
        If pending_users is empty (spinner timed out), goes back to add_recipient.
        """
        errors = {}
        pending = self._get_pending_users()

        if user_input is not None:
            user_id = user_input[CONF_USER_ID]

            if user_id == CLEAR_PENDING:
                pending.clear()
                self._poll_task = None
                return await self.async_step_add_recipient()

            name = user_input.get(CONF_RECIPIENT_NAME, "").strip()
            friendly_name = user_input.get(CONF_FRIENDLY_NAME, "").strip()
            add_another = user_input.get(CONF_ADD_ANOTHER, False)

            if not name:
                errors[CONF_RECIPIENT_NAME] = "name_required"
            elif not _is_valid_name(name):
                errors[CONF_RECIPIENT_NAME] = "invalid_name"
            elif name in self._recipients or _name_slug_conflicts(name, self._recipients):
                errors[CONF_RECIPIENT_NAME] = "duplicate_name"
            elif any(r["user_id"] == user_id for r in self._recipients.values()):
                errors["base"] = "duplicate_user_id"
            else:
                line_display_name = pending.get(user_id, user_id)
                self._recipients[name] = {
                    "user_id": user_id,
                    "display_name": line_display_name,
                    "friendly_name": friendly_name or line_display_name,
                    "type": "group" if user_id.startswith("C") else "user",
                }
                pending.pop(user_id, None)
                self._persist()
                if add_another:
                    self._poll_task = None
                    return await self.async_step_add_recipient()
                return self._save()

        if not pending:
            self._poll_task = None
            return await self.async_step_add_recipient()

        options = [
            SelectOptionDict(value=uid, label=display)
            for uid, display in pending.items()
        ]
        options.append(SelectOptionDict(value=CLEAR_PENDING, label=CLEAR_PENDING_LABEL))
        first_uid = next(iter(pending))
        first_line_name = pending[first_uid]
        first_entity_name = _sanitize_name(first_line_name)
        return self.async_show_form(
            step_id="select_recipient",
            data_schema=vol.Schema({
                vol.Required(CONF_USER_ID): SelectSelector(
                    SelectSelectorConfig(options=options)
                ),
                vol.Optional(CONF_FRIENDLY_NAME, default=first_line_name): str,
                vol.Optional(CONF_RECIPIENT_NAME, default=first_entity_name): str,
                vol.Optional(CONF_ADD_ANOTHER, default=False): bool,
            }),
            errors=errors,
        )

    async def async_step_remove_recipient(self, user_input=None) -> FlowResult:
        """Show a dropdown of current recipients and remove the selected one."""
        if not self._recipients:
            return self.async_abort(reason="no_recipients")

        errors = {}
        if user_input is not None:
            name = user_input[CONF_RECIPIENT_NAME]
            self._recipients.pop(name, None)
            return self._save()

        remove_options = {
            name: r.get("friendly_name", name)
            for name, r in self._recipients.items()
        }
        return self.async_show_form(
            step_id="remove_recipient",
            data_schema=vol.Schema({
                vol.Required(CONF_RECIPIENT_NAME): vol.In(remove_options),
            }),
            errors=errors,
        )

    async def async_step_rotate_token(self, user_input=None) -> FlowResult:
        """Replace the Channel Access Token and/or Channel Secret.

        Verifies the new token before saving. Both fields are pre-filled with
        current values so the user only needs to change what has rotated.
        """
        errors = {}
        if user_input is not None:
            token = user_input[CONF_CHANNEL_ACCESS_TOKEN].strip()
            secret = user_input[CONF_CHANNEL_SECRET].strip()
            if not secret:
                errors[CONF_CHANNEL_SECRET] = "invalid_secret"
            else:
                error = await _verify_token(self.hass, token)
                if error:
                    errors["base"] = error
                else:
                    self._token = token
                    self._secret = secret
                    return self._save()

        return self.async_show_form(
            step_id="rotate_token",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({
                    vol.Required(CONF_CHANNEL_ACCESS_TOKEN): str,
                    vol.Required(CONF_CHANNEL_SECRET): str,
                }),
                {
                    CONF_CHANNEL_ACCESS_TOKEN: self._token,
                    CONF_CHANNEL_SECRET: self._secret,
                },
            ),
            errors=errors,
        )

    def _get_pending_users(self) -> dict:
        """Return the in-memory pending_users dict for the current config entry.

        Looks up the live dict in hass.data, which is the authoritative source
        during normal operation. The dict is pre-populated from config entry data
        at startup so captures survive HA restarts.
        """
        entry_data = self.hass.data.get(DOMAIN, {})
        for key, val in entry_data.items():
            if isinstance(val, dict) and PENDING_USERS_KEY in val:
                return val[PENDING_USERS_KEY]
        return {}

    def _persist(self) -> None:
        """Write current recipients and credentials to the config entry without closing the flow.

        Called after each recipient is confirmed so partial progress is not lost
        if the user cancels the flow before finishing. Also writes the current
        in-memory pending_users back to the config entry so the update listener
        can detect that only pending_users changed and skip the reload.
        """
        pending = self._get_pending_users()
        new_data = {
            CONF_CHANNEL_ACCESS_TOKEN: self._token,
            CONF_CHANNEL_SECRET: self._secret,
            RECIPIENTS_KEY: self._recipients,
            PENDING_USERS_KEY: dict(pending),
        }
        self.hass.config_entries.async_update_entry(
            self._config_entry, data=new_data
        )

    def _save(self) -> FlowResult:
        """Persist and close the options flow.

        Calls _persist() to write data, then returns async_create_entry to
        signal completion. The update listener in __init__.py triggers a reload
        so notify entities are refreshed.
        """
        self._persist()
        return self.async_create_entry(title="", data={})