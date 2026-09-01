"""Mini App backend for the browser-based account login.

MTProto auth happens in the user's browser (GramJS) so the login originates
from their own IP, not the server's — see shared/webapp_auth.py and
shared/session_convert.py for why and how. This package only serves the page
and completes the handoff once the browser has a working session.
"""
