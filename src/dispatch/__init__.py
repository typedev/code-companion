"""Local dispatch: attach to a live desktop Claude session from a laptop on the LAN.

App-native (no ssh). The desktop PM runs a broker that advertises presence via
zeroconf and, for a paired/authorised device, bridges a chosen tmux session's
PTY over a raw framed TCP channel and reverse-proxies its MCP endpoint. The
laptop opens a lightweight remote window whose terminal relays that PTY.
"""
