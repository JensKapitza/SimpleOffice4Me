# Copy as node_settings.py outside version control; credentials stay on your board.
ROLE = "node"  # "node" or "gateway"
NODE_ID = "room_a"  # second node: room_b
SENSOR = "demo"  # "rp2_chip_temperature" only on an RP2040 Pico
DEMO_VALUE = 21.5
PORT = 8080
BIND_IP = ""  # blank for Pico W WLAN, an existing private IPv4 for Linux/Ethernet
WIFI_SSID = ""
WIFI_PASSWORD = ""
ALLOW_FROM_IP = "192.168.1.30"  # on nodes: gateway IP; on gateway: SimpleOffice IP
SOURCES = (("room_a", "192.168.1.31", 8080), ("room_b", "192.168.1.32", 8080))
