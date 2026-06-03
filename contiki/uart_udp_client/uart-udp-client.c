#include "contiki.h"
#include "dev/leds.h"
#include "dev/serial-line.h"
#include "dev/uart.h"
#include "net/ipv6/simple-udp.h"
#include "net/netstack.h"
#include "net/routing/routing.h"
#include "sys/log.h"

#include <stdio.h>
#include <string.h>

#define LOG_MODULE "uart-client"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_CLIENT_PORT 8765
#define UDP_SERVER_PORT 5678

static struct simple_udp_connection udp_conn;

/* This firmware runs on the sender Zolertia, the one wired to the Arduino.
 * Its main job is to work as a bridge:
 * Arduino UART lines -> wireless UDP packets to the root,
 * and wireless commands from the root -> UART commands back to Arduino.
 */
PROCESS(uart_udp_client_process, "UART to UDP client");
AUTOSTART_PROCESSES(&uart_udp_client_process);

static unsigned int
uart1_send_bytes(const unsigned char *s, unsigned int len)
{
  /* We use UART1 for the Arduino link. This helper writes a full command
   * or acknowledgement one byte at a time to the Arduino.
   */
  unsigned int i;

  for(i = 0; s != NULL && i < len && s[i] != '\0'; i++) {
    uart_write_byte(1, s[i]);
  }

  return i;
}

static void
udp_rx_callback(struct simple_udp_connection *c,
                const uip_ipaddr_t *sender_addr,
                uint16_t sender_port,
                const uip_ipaddr_t *receiver_addr,
                uint16_t receiver_port,
                const uint8_t *data,
                uint16_t datalen)
{
  /* Packets coming from the root can be simple ACKs or real dashboard
   * commands. Commands start with CMD: and must be forwarded to Arduino.
   */
  if(datalen >= 4 && strncmp((const char *)data, "CMD:", 4) == 0) {
    LOG_INFO("Command from root: %.*s\n", datalen, (const char *)data);
    uart1_send_bytes(data, datalen);
    uart1_send_bytes((const unsigned char *)"\n", 1);
    leds_toggle(LEDS_BLUE);
  } else {
    LOG_INFO("ACK from root: %.*s\n", datalen, (const char *)data);
    leds_toggle(LEDS_GREEN);
  }
}

PROCESS_THREAD(uart_udp_client_process, ev, data)
{
  static struct etimer heartbeat_timer;
  uip_ipaddr_t root_ipaddr;

  PROCESS_BEGIN();

  serial_line_init();
  uart_init(1);
  uart_set_input(1, serial_line_input_byte);

  /* Local UDP port 8765 talks to the root UDP port 5678. */
  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL,
                      UDP_SERVER_PORT, udp_rx_callback);

  LOG_INFO("UART1 ready at 9600 baud; waiting for Arduino lines\n");
  etimer_set(&heartbeat_timer, 5 * CLOCK_SECOND);

  while(1) {
    PROCESS_WAIT_EVENT();

    if(ev == PROCESS_EVENT_TIMER && data == &heartbeat_timer) {
      /* This tells the Arduino that the sender Zolertia is alive. It is also
       * useful when checking the Arduino serial monitor during debugging.
       */
      uart1_send_bytes((const unsigned char *)"ZOLERTIA_READY\n", 15);
      /* Test data disabled - only sending real Arduino data */
      etimer_reset(&heartbeat_timer);
    }

    if(ev == serial_line_event_message && data != NULL) {
      /* A complete line has arrived from Arduino. In our project this line is
       * the JSON sensor packet created by the Arduino sketch.
       */
      const char *line = (const char *)data;

      if(strlen(line) == 0) {
        continue;
      }

      LOG_INFO("Arduino line: %s\n", line);
      uart1_send_bytes((const unsigned char *)"UART_RX_OK\n", 11);

      if(NETSTACK_ROUTING.node_is_reachable() &&
         NETSTACK_ROUTING.get_root_ipaddr(&root_ipaddr)) {
        /* This is the actual wireless transmission between the Zolertias. */
        simple_udp_sendto(&udp_conn, line, strlen(line), &root_ipaddr);
        leds_toggle(LEDS_RED);
        LOG_INFO("Sent UDP payload to RPL root\n");
      } else {
        LOG_INFO("RPL root not reachable yet; payload not sent\n");
      }
    }
  }

  PROCESS_END();
}
