/*
 * SATVA Node — optional field sensing for wholesale markets.
 *
 * ESP32 + MQ-series gas sensor + DHT22, publishing to MQTT over Wi-Fi.
 * Target bill of materials: under Rs 900 (specification 3.6).
 *
 * WHAT THIS DEVICE IS
 * -------------------
 * An advisory sampler. It watches crate headspace and raises a flag when the
 * reducing-gas reading rises well above its own clean-air baseline, which is a
 * reason for somebody to go and perform a confirmatory strip test on that crate.
 *
 * WHAT THIS DEVICE IS NOT
 * -----------------------
 * It is NOT an acetylene detector, and nothing it reports is evidence.
 *
 * An MQ-series sensor is a heated tin-dioxide element whose resistance falls in
 * the presence of a broad class of reducing gases. It cannot distinguish
 * acetylene from ethylene, ethanol, LPG, or the ordinary volatiles of a fruit
 * market. Its baseline also drifts with temperature and humidity, which is why
 * the DHT22 reading is published alongside every sample rather than as a
 * separate stream.
 *
 * The backend records these readings with advisory_flag and refuses to promote
 * any scan on the strength of them. See app/api/v1/devices.py.
 *
 * CALIBRATION
 * -----------
 * R0 is the sensor's resistance in clean air and must be measured per unit,
 * after at least 24 hours of burn-in. An uncalibrated MQ sensor produces
 * numbers that look meaningful and are not. If R0 has not been set, this
 * firmware publishes the raw resistance and lets the backend record that no
 * baseline exists rather than inventing a ratio.
 *
 * WIRING (ESP32 DevKit v1)
 * ------------------------
 *   MQ-x analog out ---- GPIO 34  (ADC1_CH6, input only)
 *   MQ-x heater     ---- 5V via its own supply rail, common ground
 *   DHT22 data      ---- GPIO 4   (10k pull-up to 3V3)
 *   Status LED      ---- GPIO 2   (on-board)
 *   Calibrate button --- GPIO 0   (on-board BOOT, active low)
 *
 * The MQ heater draws ~150 mA. Powering it from the ESP32's regulator will
 * brown out the radio mid-transmission; give it its own rail.
 *
 * BUILD
 * -----
 *   Board:    ESP32 Dev Module
 *   Libraries: PubSubClient (Nick O'Leary), DHT sensor library (Adafruit),
 *              Adafruit Unified Sensor, ArduinoJson
 *
 * Copy secrets_example.h to secrets.h and fill it in. secrets.h is gitignored.
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ArduinoJson.h>
#include <Preferences.h>

#include "secrets.h"

// ---------------------------------------------------------------- pins ----
static const int PIN_MQ_ANALOG = 34;
static const int PIN_DHT = 4;
static const int PIN_LED = 2;
static const int PIN_CALIBRATE = 0;

#define DHT_TYPE DHT22

// ------------------------------------------------------------ constants ----
// Load resistor on the MQ breakout, in ohms. Check your board: 1k, 4.7k and
// 10k are all common, and getting this wrong scales every reading.
static const float MQ_LOAD_RESISTANCE = 10000.0f;

static const float ADC_MAX = 4095.0f;   // ESP32 12-bit ADC
static const float ADC_REF_VOLTAGE = 3.3f;

// Sampling. Ten samples smooth the ADC noise without making the loop sluggish.
static const int SAMPLES_PER_READING = 10;
static const int SAMPLE_DELAY_MS = 40;

static const unsigned long PUBLISH_INTERVAL_MS = 60000UL;  // one sample a minute
static const unsigned long WIFI_RETRY_MS = 10000UL;
static const unsigned long MQTT_RETRY_MS = 5000UL;

// Calibration: 60 samples over ~30 s in clean air.
static const int CALIBRATION_SAMPLES = 60;
static const int CALIBRATION_DELAY_MS = 500;

// Advisory threshold on Rs/R0. Above this the reading is unusual enough to be
// worth a strip test. An engineering threshold from datasheet response curves,
// NOT a validated detection limit for any specific gas.
static const float ADVISORY_RS_R0_RATIO = 2.5f;

// ---------------------------------------------------------------- state ----
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
DHT dht(PIN_DHT, DHT_TYPE);
Preferences preferences;

float r0Ohms = 0.0f;          // 0 means "not calibrated"
uint32_t sampleCounter = 0;
unsigned long lastPublish = 0;
unsigned long lastWifiAttempt = 0;
unsigned long lastMqttAttempt = 0;
char nodeUid[32];

// ------------------------------------------------------------- helpers ----

/* Derive a stable node id from the MAC address.
 * Hard-coding an id per unit is a deployment mistake waiting to happen; the
 * MAC is unique, permanent, and already printed on most boards. */
void buildNodeUid() {
  uint8_t mac[6];
  WiFi.macAddress(mac);
  snprintf(nodeUid, sizeof(nodeUid), "node-%02x%02x%02x%02x%02x%02x",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

/* Average the ADC over several samples and convert to sensor resistance.
 *
 * The MQ breakout is a divider: Vout = Vcc * RL / (Rs + RL), so
 * Rs = RL * (Vcc - Vout) / Vout. */
float readSensorResistance(int *rawAdcOut) {
  long total = 0;
  for (int i = 0; i < SAMPLES_PER_READING; i++) {
    total += analogRead(PIN_MQ_ANALOG);
    delay(SAMPLE_DELAY_MS);
  }
  const int raw = (int)(total / SAMPLES_PER_READING);
  if (rawAdcOut != nullptr) *rawAdcOut = raw;

  if (raw <= 0) return -1.0f;  // open circuit or disconnected sensor

  const float voltage = (raw / ADC_MAX) * ADC_REF_VOLTAGE;
  if (voltage <= 0.01f) return -1.0f;

  return MQ_LOAD_RESISTANCE * (ADC_REF_VOLTAGE - voltage) / voltage;
}

/* Measure R0 in clean air. Run this outdoors, away from fruit, after the
 * heater has been powered for at least 24 hours. */
void calibrateBaseline() {
  Serial.println(F("[satva] calibrating R0 — keep the sensor in clean air"));
  digitalWrite(PIN_LED, HIGH);

  float total = 0.0f;
  int valid = 0;
  for (int i = 0; i < CALIBRATION_SAMPLES; i++) {
    const float rs = readSensorResistance(nullptr);
    if (rs > 0) {
      total += rs;
      valid++;
    }
    delay(CALIBRATION_DELAY_MS);
    digitalWrite(PIN_LED, (i % 2) ? HIGH : LOW);
  }

  digitalWrite(PIN_LED, LOW);

  if (valid < CALIBRATION_SAMPLES / 2) {
    Serial.println(F("[satva] calibration FAILED — check the sensor wiring"));
    return;
  }

  r0Ohms = total / valid;
  preferences.begin("satva", false);
  preferences.putFloat("r0", r0Ohms);
  preferences.end();

  Serial.printf("[satva] R0 = %.1f ohms (from %d samples)\n", r0Ohms, valid);
}

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiAttempt < WIFI_RETRY_MS) return;
  lastWifiAttempt = millis();

  Serial.printf("[satva] connecting to Wi-Fi %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void connectMqtt() {
  if (mqtt.connected()) return;
  if (millis() - lastMqttAttempt < MQTT_RETRY_MS) return;
  lastMqttAttempt = millis();

  Serial.printf("[satva] connecting to MQTT %s:%d\n", MQTT_HOST, MQTT_PORT);
  const bool ok = (strlen(MQTT_USERNAME) > 0)
      ? mqtt.connect(nodeUid, MQTT_USERNAME, MQTT_PASSWORD)
      : mqtt.connect(nodeUid);

  if (ok) {
    Serial.println(F("[satva] MQTT connected"));
  } else {
    Serial.printf("[satva] MQTT failed, state=%d\n", mqtt.state());
  }
}

/* Publish one telemetry sample.
 *
 * The payload matches hardware/docs/mqtt_payload_schema.json and the
 * NodeTelemetryIn schema on the server. sample_uid makes ingestion idempotent:
 * MQTT is at-least-once, so a redelivered message must not double-count. */
void publishSample() {
  int rawAdc = 0;
  const float rs = readSensorResistance(&rawAdc);

  const float temperature = dht.readTemperature();
  const float humidity = dht.readHumidity();

  JsonDocument doc;
  doc["node_uid"] = nodeUid;
  doc["sample_uid"] = String(nodeUid) + "-" + String(sampleCounter++);
  doc["firmware_version"] = FIRMWARE_VERSION;

  // NaN is what the DHT library returns on a failed read. Publishing null is
  // honest; publishing 0.0 would look like a plausible measurement.
  if (!isnan(temperature)) doc["temperature_c"] = round(temperature * 10) / 10.0;
  if (!isnan(humidity)) doc["humidity_pct"] = round(humidity * 10) / 10.0;

  doc["mq_raw_adc"] = rawAdc;
  if (rs > 0) doc["mq_rs_ohms"] = round(rs * 10) / 10.0;

  // The ratio is computed on the server, which holds the per-device R0. It is
  // included here only when this unit has been calibrated, so an uncalibrated
  // node cannot silently emit a meaningless ratio.
  if (r0Ohms > 0 && rs > 0) {
    doc["mq_rs_r0_ratio"] = round((rs / r0Ohms) * 1000) / 1000.0;
    doc["advisory_hint"] = (rs / r0Ohms) >= ADVISORY_RS_R0_RATIO;
  } else {
    doc["calibrated"] = false;
  }

  char payload[384];
  const size_t length = serializeJson(doc, payload, sizeof(payload));

  char topic[96];
  snprintf(topic, sizeof(topic), "satva/node/%s/telemetry", nodeUid);

  if (mqtt.publish(topic, (const uint8_t *)payload, length, false)) {
    Serial.printf("[satva] published %s\n", payload);
    digitalWrite(PIN_LED, HIGH);
    delay(40);
    digitalWrite(PIN_LED, LOW);
  } else {
    Serial.println(F("[satva] publish failed"));
  }
}

// --------------------------------------------------------------- setup ----
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_CALIBRATE, INPUT_PULLUP);
  digitalWrite(PIN_LED, LOW);

  // 11 dB attenuation gives the full 0–3.3 V range on the ESP32 ADC; the
  // default 0 dB tops out around 1.1 V and would clip most MQ readings.
  analogSetPinAttenuation(PIN_MQ_ANALOG, ADC_11db);

  dht.begin();
  buildNodeUid();

  preferences.begin("satva", true);
  r0Ohms = preferences.getFloat("r0", 0.0f);
  preferences.end();

  Serial.println();
  Serial.println(F("SATVA Node"));
  Serial.printf("  node_uid : %s\n", nodeUid);
  Serial.printf("  firmware : %s\n", FIRMWARE_VERSION);
  if (r0Ohms > 0) {
    Serial.printf("  R0       : %.1f ohms\n", r0Ohms);
  } else {
    Serial.println(F("  R0       : NOT CALIBRATED — hold BOOT for 3 s in clean air"));
  }
  Serial.println(F("  NOTE: advisory sampler only. Not an acetylene detector,"));
  Serial.println(F("        and never treated as evidence by SATVA."));
  Serial.println();

  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
}

// ---------------------------------------------------------------- loop ----
void loop() {
  connectWifi();
  if (WiFi.status() == WL_CONNECTED) {
    connectMqtt();
    mqtt.loop();
  }

  // Hold BOOT for three seconds to re-run calibration.
  if (digitalRead(PIN_CALIBRATE) == LOW) {
    const unsigned long pressedAt = millis();
    while (digitalRead(PIN_CALIBRATE) == LOW) {
      if (millis() - pressedAt > 3000) {
        calibrateBaseline();
        break;
      }
      delay(50);
    }
  }

  if (millis() - lastPublish >= PUBLISH_INTERVAL_MS) {
    lastPublish = millis();
    if (mqtt.connected()) {
      publishSample();
    } else {
      // No buffering. A crate reading is only meaningful close to the time it
      // was taken, and replaying a stale batch after a reconnect would put
      // misleading timestamps into the record.
      Serial.println(F("[satva] offline; sample skipped"));
    }
  }

  delay(50);
}
