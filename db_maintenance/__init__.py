"""Standalone DB maintenance service.

Depends only on db/ (models, session). Must never import from
management_bot/ or userbot/. Runs as its own process, manually or via cron.
"""
