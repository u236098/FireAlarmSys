#include "contiki.h"
#include "dev/leds.h"
#include "dev/serial-line.h"
#include "net/ipv6/simple-udp.h"
#include "net/netstack.h"
#include "net/routing/routing.h"
#include "sys/log.h"

#include <stdio.h>
#include <string.h>

#define LOG_MODULE "dashboard"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_CLIENT_PORT 8765
#define UDP_SERVER_PORT 5678

static struct simple_udp_connection udp_conn;
static uip_ipaddr_t last_sender_addr;
static uint8_t has_sender_addr;

/* This firmware runs on the receiver/root Zolertia, the one connected to the
 * dashboard computer. It receives wireless packets from the sender and prints
 * them to USB serial so the Python dashboard can read them.
 */
PROCESS(udp_dashboard_receiver_process, "UDP dashboard receiver");
AUTOSTART_PROCESSES(&udp_dashboard_receiver_process);

static void
udp_rx_callback(struct simple_udp_connection *c,
                const uip_ipaddr_t *sender_addr,
                uint16_t sender_port,
                const uip_ipaddr_t *receiver_addr,
                uint16_t receiver_port,
                const uint8_t *data,
                uint16_t datalen)
{
  /* A sensor packet arrived wirelessly from the sender Zolertia. */
  LOG_INFO("RX from ");
  LOG_INFO_6ADDR(sender_addr);
  LOG_INFO_(": %.*s\n", datalen, (const char *)data);

  uip_ipaddr_copy(&last_sender_addr, sender_addr);
  has_sender_addr = 1;

  /* The dashboard searches for this exact marker. Anything after it is the
   * Arduino JSON payload.
   */
  printf("DASHBOARD_JSON:%.*s\n", datalen, (const char *)data);

  /* Send a small ACK so the sender knows the root received the packet. */
  simple_udp_sendto(&udp_conn, "ok", 2, sender_addr);
  leds_toggle(LEDS_GREEN);
}

PROCESS_THREAD(udp_dashboard_receiver_process, ev, data)
{
  PROCESS_BEGIN();

  serial_line_init();
  NETSTACK_ROUTING.root_start();

  /* The receiver is the RPL root. The sender joins this network and sends
   * UDP packets to this port.
   */
  simple_udp_register(&udp_conn, UDP_SERVER_PORT, NULL,
                      UDP_CLIENT_PORT, udp_rx_callback);

  LOG_INFO("RPL root started; connect this mote to the dashboard PC\n");
  LOG_INFO("Dashboard commands: DASHBOARD_CMD:BUZZER_ON, DASHBOARD_CMD:BUZZER_OFF, DASHBOARD_CMD:BUZZER_AUTO\n");

  while(1) {
    PROCESS_WAIT_EVENT();

    if(ev == serial_line_event_message && data != NULL) {
      /* These serial lines come from the Python dashboard. They are used for
       * the reverse path, for example to turn the Arduino buzzer on or off.
       */
      const char *line = (const char *)data;

      if(strncmp(line, "DASHBOARD_CMD:", 14) == 0) {
        const char *command = line + 14;
        char payload[40];

        if(!has_sender_addr) {
          /* Before sending a command back, we need to know the sender address.
           * We learn it from the first sensor packet that arrives.
           */
          LOG_INFO("No sender address yet; command ignored: %s\n", command);
          continue;
        }

        snprintf(payload, sizeof(payload), "CMD:%s", command);
        LOG_INFO("Sending command to sender: %s\n", payload);
        /* This sends the dashboard command back through the wireless link. */
        simple_udp_sendto(&udp_conn, payload, strlen(payload), &last_sender_addr);
        leds_toggle(LEDS_RED);
      }
    }
  }

  PROCESS_END();
}
