"""Personal manager bot — the owner's private notification feed.

One shared Bot-API bot that DMs each owner: deleted/edited messages, messages
the autoresponder replied to, .info / .check results, and one-time (view-once)
media the userbot captured. The sending side lives in shared.notify; this
package only runs the /start side so owners can receive DMs.
"""
