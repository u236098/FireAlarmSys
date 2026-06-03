ARDUINO_CLI ?= /home/kali/Desktop/digispark/squashfs-root/resources/app/lib/backend/resources/arduino-cli

ARDUINO_PORT ?= /dev/serial/by-id/usb-Arduino__www.arduino.cc__0043_7413633393235130B1D2-if00
RECEIVER_PORT ?= /dev/serial/by-id/usb-Silicon_Labs_Zolertia_RE-Mote_platform_ZOL-RM01-A000704-if00-port0
SENDER_PORT ?= /dev/serial/by-id/usb-Silicon_Labs_Zolertia_RE-Mote_platform_ZOL-RM01-A000729-if00-port0

TARGET ?= zoul
BOARD ?= remote-reva
ARDUINO_FQBN ?= arduino:avr:uno

.PHONY: ports compile-arduino upload-arduino flash-receiver flash-sender flash-all dashboard dashboard-demo api buzzer-on buzzer-off buzzer-auto clean-generated
.NOTPARALLEL: flash-all

ports:
	ls -l /dev/serial/by-id

compile-arduino:
	"$(ARDUINO_CLI)" compile \
	  --fqbn "$(ARDUINO_FQBN)" \
	  --build-path "$(CURDIR)/arduino/fire_alarm/build" \
	  "$(CURDIR)/arduino/fire_alarm"

upload-arduino: compile-arduino
	"$(ARDUINO_CLI)" upload \
	  -p "$(ARDUINO_PORT)" \
	  --fqbn "$(ARDUINO_FQBN)" \
	  --input-dir "$(CURDIR)/arduino/fire_alarm/build" \
	  "$(CURDIR)/arduino/fire_alarm"

flash-receiver:
	$(MAKE) -C contiki/udp_dashboard_receiver \
	  TARGET="$(TARGET)" BOARD="$(BOARD)" PORT="$(RECEIVER_PORT)" \
	  udp-dashboard-receiver.upload

flash-sender:
	$(MAKE) -C contiki/uart_udp_client \
	  TARGET="$(TARGET)" BOARD="$(BOARD)" PORT="$(SENDER_PORT)" \
	  uart-udp-client.upload

flash-all:
	$(MAKE) flash-receiver
	$(MAKE) flash-sender
	$(MAKE) upload-arduino

dashboard:
	python3 dashboard/fire_alarm_dashboard.py "$(RECEIVER_PORT)"

dashboard-demo:
	python3 dashboard/fire_alarm_dashboard.py --demo

api:
	curl -s http://127.0.0.1:8080/api/latest

buzzer-on:
	curl -s -X POST -d state=on http://127.0.0.1:8080/api/buzzer

buzzer-off:
	curl -s -X POST -d state=off http://127.0.0.1:8080/api/buzzer

buzzer-auto:
	curl -s -X POST -d state=auto http://127.0.0.1:8080/api/buzzer

clean-generated:
	rm -rf arduino/fire_alarm/build \
	  contiki/uart_udp_client/build \
	  contiki/udp_dashboard_receiver/build \
	  dashboard/__pycache__ \
	  dashboard.log \
	  data/fire_alarm_*.csv
