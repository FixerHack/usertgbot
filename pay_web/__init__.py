"""Public HTTPS surface for card payments (WayForPay).

Mounted under /pay on the same origin as the Mini App — see
connect_web/server.py. Separate package because it shares nothing with the
login flow but the port.
"""
