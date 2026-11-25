# Three-station USB reaction course (#1305)

In SimpleOffice4Me, sign in and open **Kids → Reaction course** (German: **Kinder → Reaktionsparcours**). The default is virtual and needs no equipment. The numbered target also shows the expected key; focus the stations area and press **1**, **2** or **3**, or tap the matching on-screen button. The game does not listen for keys elsewhere. Holding a key does not add presses. The result is session-only and has no service, upload, telemetry or scoreboard endpoint.

- **Practice** has no time limit. An incorrect station is reported and the same target stays active.
- **Two-player duel** gives both players the same six targets and seven seconds per target. Player 2 starts explicitly. Ranking is most hits, then fewest wrong presses plus missed targets, then shortest total target time. A missed target contributes its full time window. The total excludes the turn change and setup; individual result times remain visible for both players.
- **Sequence** shows the required order of three stations. Four levels use 8 / 6.5 / 5 / 4 seconds per target; a wrong press or timeout restarts the same level when chosen. There is no level faster than four seconds. A solved level/round shows a star on screen only.

## Adult setup: one ESP32, three stations

This is an **untested physical reference** for an [Espressif ESP32-DevKitC V4 with ESP32-WROOM-32E](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html) on a desktop PC. Neither a Raspberry Pi nor Wi-Fi is used. Use only the board's USB power connection. Disconnect USB while building, enclose the board and connections before use, and keep fixed cables away from children and walkways. Walk, do not run. No bare wires, batteries, relays, locks or other actuators.

| Station | Single low-current LED output | Normally-open momentary button input |
| --- | --- | --- |
| 1 | GPIO25 | GPIO32 |
| 2 | GPIO26 | GPIO33 |
| 3 | GPIO27 | GPIO23 |

Each GPIO output connects through a **330–470 Ω series resistor** to the LED anode; LED cathode goes to GND. Each input goes to one contact of its switch; the other goes to GND. Inputs use Arduino-ESP32's [internal pull-ups](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/gpio.html), so pressing reads low. Do not connect 5 V to any signal pin or provide external power. Use ordinary single LEDs, not a mains light, an LED strip or a motor.

With [Arduino CLI](https://arduino.github.io/arduino-cli/latest/) installed on the adult PC:

```bash
index='https://espressif.github.io/arduino-esp32/package_esp32_index.json'
sketch=hardware/kids-reaction-esp32/kids_reaction_esp32
arduino-cli core update-index --additional-urls "$index"
arduino-cli core install esp32:esp32 --additional-urls "$index"
arduino-cli compile --fqbn esp32:esp32:esp32 "$sketch"
arduino-cli board list
# Replace <port> with only the matching USB board's own port:
arduino-cli upload --port '<port>' --fqbn esp32:esp32:esp32 "$sketch"
```

Close the serial monitor before use. On the same desktop, open SimpleOffice4Me directly at `localhost` or `127.0.0.1`, in a browser supporting [Web Serial](https://developer.chrome.com/docs/capabilities/serial/), e.g. desktop Chrome. Open **ESP32 USB setup with an adult → Choose USB board** in the game and choose that board. The chooser is restricted to common supported USB–UART bridges (CP210x, CH340, FT232), then the board must identify the firmware protocol. LAN/proxied pages, blocked permissions and unsupported browsers keep virtual mode; the web server has no serial capability. Choosing USB does not start a round. Disconnection aborts the round; the user must reselect USB or deliberately start a new virtual round.

## What times mean

In USB mode the firmware timestamps LED activation immediately after the GPIO write and an accepted debounced falling switch edge using its own monotonic millisecond counter. The displayed difference **includes debounce** (25 ms) but not the travel time of serial events or JavaScript processing. Virtual mode uses browser time and is labelled separately. Do not compare virtual measurements with hardware measurements.

The board drives only LEDs and accepts bounded ASCII lines at 115200 baud. It requires a fresh 16-hex-digit session epoch (`HELLO`/ `READY ... 1`) and an increasing heartbeat (`KEEP` / `PONG`). Targets carry increasing tickets and station IDs: `ARM epoch ticket station duration_ms`, `TARGET epoch ticket station duration_ms`. It returns `HIT` or `WRONG` with epoch, ticket, station, elapsed milliseconds and increasing press sequence, or `MISS` with epoch, ticket and station. The browser rejects results before the matching target acknowledgement, as well as old, duplicate and malformed data. The board disarms on `STOP`, serial output congestion, or no fresh heartbeat for 2 seconds. Hardware must be treated as unvalidated until measurements are confirmed on a physical board.

## Reproducible checks

From the repository root:

```bash
python -m pip install -e '.[test]'
python -m unittest -v tests.test_kids_reaction tests.test_template_accessibility tests.test_frontend_route_contract tests.test_template_shell
node --test tests/kids_reaction.test.cjs
g++ -std=c++11 -Wall -Wextra -Werror tests/kids_reaction_firmware.cpp -o /tmp/kids-reaction-core-test
/tmp/kids-reaction-core-test
```

The first command group checks the actual authenticated Flask route and template rules. Node exercises the actual game model, keyboard boot in a synthetic DOM, and serial adapter with a simulated port. The C++ executable compiles the same state engine included by the sketch against simulated buttons, LEDs and device time: bounce, a button held before/during arming, wrong station, repeated frames, timeout, watchdog and counter rollover. It **does not compile the Arduino-ESP32 toolchain or verify a board/physical setup**.

After the actual Arduino build, an adult should separately verify USB enumeration and a complete physical game: all three labelled targets, a wrong button, an already held button and a target timeout; then unplug USB. With USB still powering the board, also hide the game tab and verify every station goes dark and no score appears on reconnection. The physical measurement, USB driver, circuit and time accuracy are not asserted by the automated checks. Multi-device LAN stations and shared scores are deferred, not part of this local USB reference.
