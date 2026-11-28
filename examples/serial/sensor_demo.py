"""MicroPython smoke fixture for ESP32 or Pico: demo counter, not a physical measurement."""

import time


def main():
    value = 0
    while True:
        print("SO4M," + str(value))
        value += 1
        time.sleep_ms(1000)


if __name__ == "__main__":
    main()
