/*
 * Copy to secrets.h and fill in. secrets.h is gitignored — never commit
 * credentials for a device that will sit on someone else's network.
 */
#pragma once

#define FIRMWARE_VERSION "0.1.0"

#define WIFI_SSID       "your-wifi-ssid"
#define WIFI_PASSWORD   "your-wifi-password"

#define MQTT_HOST       "192.168.1.10"   // the machine running the SATVA stack
#define MQTT_PORT       1883

/* Leave empty for an anonymous broker (the compose stack's default, which is
 * only reachable inside the compose network). Set both before exposing a
 * broker to real field hardware. */
#define MQTT_USERNAME   ""
#define MQTT_PASSWORD   ""
