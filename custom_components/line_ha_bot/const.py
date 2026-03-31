"""Constants for the LINE Bot integration."""

# Integration domain. Must match the folder name under custom_components/
DOMAIN = "line_ha_bot"

# Config entry data keys - stored in the HA config entry after setup
CONF_CHANNEL_ACCESS_TOKEN = "channel_access_token"  # LINE Messaging API long-lived token
CONF_CHANNEL_SECRET = "channel_secret"              # Used for webhook signature verification
CONF_RECIPIENT_NAME = "recipient_name"              # ASCII entity name chosen by the user
CONF_FRIENDLY_NAME = "friendly_name"                # Free-form display name (supports emoji, unicode)
CONF_USER_ID = "user_id"                            # LINE internal user ID (U + 32 hex chars)

# LINE Messaging API endpoints
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_TOKEN_VERIFY_URL = "https://api.line.me/v2/oauth/verify"
LINE_PROFILE_URL = "https://api.line.me/v2/bot/profile/{user_id}"
LINE_GROUP_SUMMARY_URL = "https://api.line.me/v2/bot/group/{group_id}/summary"

# Path at which HA exposes the permanent webhook to LINE
LINE_WEBHOOK_PATH = "/api/line_ha_bot/webhook"

# LINE Bot info endpoint
LINE_BOT_INFO_URL = "https://api.line.me/v2/bot/info"

# hass.data[DOMAIN] keys
RECIPIENTS_KEY = "recipients"        # Dict mapping HA name -> LINE user ID, stored in config entry
PENDING_USERS_KEY = "pending_users"  # Temporary dict of user IDs captured by the webhook, not yet confirmed
BOT_USER_ID_KEY = "bot_user_id"       # Cached bot user ID fetched from LINE at startup

# notify.send_message service data attributes (optional, passed under the 'data' key)
ATTR_IMAGE_URL = "image_url"                # URL of an image to send after the text message
ATTR_STICKER_PACKAGE_ID = "sticker_package_id"  # LINE sticker package ID
ATTR_STICKER_ID = "sticker_id"              # LINE sticker ID within the package
ATTR_REPLY_TOKEN = "reply_token"             # LINE reply token from an incoming webhook event
ATTR_FLEX_MESSAGE = "flex_message"            # Raw LINE flex message JSON object
ATTR_FLEX_ALT_TEXT = "flex_alt_text"          # Fallback text for flex messages
ATTR_LOCATION_TITLE = "location_title"        # Location name shown in LINE
ATTR_LOCATION_ADDRESS = "location_address"    # Street address of the location
ATTR_LOCATION_LATITUDE = "location_latitude"  # Latitude of the location
ATTR_LOCATION_LONGITUDE = "location_longitude" # Longitude of the location
ATTR_TEMPLATE_TYPE = "template_type"            # Template type: "buttons" or "confirm"
ATTR_TEMPLATE_TITLE = "template_title"          # Title shown at top of buttons template
ATTR_TEMPLATE_DEFAULT_URL = "template_default_url" # URI opened when user taps the card body
ATTR_BUTTONS = "buttons"                        # List of button dicts for template messages
ATTR_QUICK_REPLIES = "quick_replies"             # List of quick reply dicts shown above keyboard
ATTR_AUDIO_URL = "audio_url"                    # URL of an M4A audio file to send
ATTR_AUDIO_DURATION = "audio_duration"          # Duration of audio in milliseconds
ATTR_VIDEO_URL = "video_url"                    # URL of a video file to send
ATTR_VIDEO_PREVIEW_URL = "video_preview_url"    # URL of a preview image for the video

# HA bus event names
EVENT_MESSAGE_RECEIVED = "line_bot_message_received"  # Fired when a known recipient sends a message
EVENT_SEND_FAILED = "line_bot_send_failed"            # Fired when a send fails

# Sentinel value for the options flow select_recipient dropdown
CLEAR_PENDING = "__clear__"          # Sentinel: discard all captured pending users
CLEAR_PENDING_LABEL = "Clear all pending"  # Display label for the CLEAR_PENDING sentinel option

# Custom service name
SERVICE_SEND_MESSAGE = "send_message"

# hass.data[DOMAIN] runtime keys (not stored in config entry)
KEY_VIEW_REGISTERED = "view_registered"   # Prevents double-registering the webhook view
KEY_CONFIG_SNAPSHOT = "config_snapshot"   # Snapshot of reload-relevant config keys

# Default strings used in message building
DEFAULT_FLEX_ALT_TEXT = "LINE message"    # Fallback text for flex/template messages when none provided
DEFAULT_LOCATION_TITLE = "Location"       # Default title for location messages
DEFAULT_ACTION_LABEL = "Open"             # Default label for template defaultAction URI buttons

# LINE quota and content API endpoints
LINE_QUOTA_URL = "https://api.line.me/v2/bot/message/quota"
LINE_QUOTA_CONSUMPTION_URL = "https://api.line.me/v2/bot/message/quota/consumption"
LINE_CONTENT_URL = "https://api-data.line.me/v2/bot/message/{message_id}/content"