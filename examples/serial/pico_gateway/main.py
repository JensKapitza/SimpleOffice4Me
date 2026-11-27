"""Copy with sensor_link.py and an individually configured node_settings.py."""
import time

import node_settings as cfg
from sensor_link import elapsed_ms, gateway_handler, now_ms, sensor_handler, serve


def lan_ip():
    if cfg.BIND_IP:
        # E.g. a Raspberry Pi with preconfigured Ethernet.
        return cfg.BIND_IP
    import network

    station_if = getattr(network.WLAN, "IF_STA", None)
    if station_if is None:
        station_if = network.STA_IF
    wlan = network.WLAN(station_if)
    wlan.active(True)
    wlan.connect(cfg.WIFI_SSID, cfg.WIFI_PASSWORD)
    started = now_ms()
    while not wlan.isconnected():
        if elapsed_ms(started) >= 20000:
            raise RuntimeError("No WLAN: check SSID/password and the 2.4 GHz network")
        time.sleep(0.25)
    return wlan.ifconfig()[0]


def measuring_function():
    if cfg.SENSOR == "rp2_chip_temperature":
        from machine import ADC

        adc = ADC(4)

        def measure():
            # RP2040 internal chip temperature, a rough demo, not room temperature.
            volts = adc.read_u16() * 3.3 / 65535
            return round(27 - (volts - 0.706) / 0.001721, 2)

        return measure
    if cfg.SENSOR == "demo":
        return lambda: cfg.DEMO_VALUE
    raise ValueError("Choose a supported sensor or add a local measuring function")


def main():
    if cfg.ROLE == "node":
        handler = sensor_handler(cfg.NODE_ID, measuring_function())
    elif cfg.ROLE == "gateway":
        handler = gateway_handler(cfg.SOURCES, getattr(cfg, "GATEWAY_CACHE_MS", 1000))
    else:
        raise ValueError("Set ROLE to node or gateway")
    ip = lan_ip()
    print("Sensor", cfg.ROLE, ip, cfg.PORT)
    serve(ip, cfg.PORT, cfg.ALLOW_FROM_IP, handler)


if __name__ == "__main__":
    main()
