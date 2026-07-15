/*
 * ESP32-C3 + AS7341 -> Wi-Fi/TCP bridge input
 * Wiring: SDA=GPIO5, SCL=GPIO6, VIN=5V, GND=GND
 * Copy secrets.example.h to secrets.h and fill in the local Wi-Fi credentials.
 */

#include <Wire.h>
#include <WiFi.h>
#include "secrets.h"

const uint16_t TCP_PORT = 3333;

#define I2C_SDA 5
#define I2C_SCL 6
#define AS7341_ADDR 0x39
#define REG_ENABLE 0x80
#define REG_ATIME 0x81
#define REG_CFG0 0xA9
#define REG_CFG1 0xAA
#define REG_CFG6 0xAF
#define REG_ASTEP_L 0xCA
#define REG_ASTEP_H 0xCB
#define REG_STATUS2 0xA3
#define REG_CH_DATA_L 0x95

const uint8_t SENSOR_ATIME = 29;
const uint16_t SENSOR_ASTEP = 599;
const uint8_t DEFAULT_GAIN_CODE = 7;  // 64x; reduces strong-light saturation.
const uint8_t STATUS2_SATURATION_MASK = 0x18;
const uint32_t CALCULATED_FULL_SCALE =
  (uint32_t)(SENSOR_ATIME + 1) * (uint32_t)(SENSOR_ASTEP + 1);
const uint16_t ADC_FULL_SCALE =
  CALCULATED_FULL_SCALE > 65535UL ? 65535U : (uint16_t)CALCULATED_FULL_SCALE;

uint8_t r8(uint8_t reg) {
  Wire.beginTransmission(AS7341_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom((int)AS7341_ADDR, 1);
  return Wire.read();
}

void w8(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(AS7341_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

uint16_t r16(uint8_t reg) {
  Wire.beginTransmission(AS7341_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom((int)AS7341_ADDR, 2);
  uint16_t lo = Wire.read();
  uint16_t hi = Wire.read();
  return (hi << 8) | lo;
}

void enableSP(bool on) {
  uint8_t enabled = r8(REG_ENABLE);
  if (on) enabled |= 0x02;
  else enabled &= ~0x02;
  w8(REG_ENABLE, enabled);
}

void enableSMUX() {
  uint8_t enabled = r8(REG_ENABLE);
  w8(REG_ENABLE, enabled | 0x10);
  uint32_t started = millis();
  while ((r8(REG_ENABLE) & 0x10) && millis() - started < 1000) delay(1);
}

bool waitAVALID(uint32_t timeoutMs = 2000) {
  uint32_t started = millis();
  while (!(r8(REG_STATUS2) & 0x40)) {
    if (millis() - started > timeoutMs) return false;
    delay(2);
  }
  return true;
}

void smux_F1F4_ClearNIR() {
  uint8_t values[20] = {
    0x30,0x01,0x00,0x00,0x00,0x42,0x00,0x00,0x50,0x00,
    0x00,0x00,0x20,0x04,0x00,0x30,0x01,0x50,0x00,0x06
  };
  for (uint8_t i = 0; i < 20; i++) w8(i, values[i]);
}

void smux_F5F8_ClearNIR() {
  uint8_t values[20] = {
    0x00,0x00,0x00,0x40,0x02,0x00,0x10,0x03,0x50,0x10,
    0x03,0x00,0x00,0x00,0x24,0x00,0x00,0x50,0x00,0x06
  };
  for (uint8_t i = 0; i < 20; i++) w8(i, values[i]);
}

bool measureBatch(void (*smuxFn)(), uint16_t output[6], uint8_t &status2) {
  enableSP(false);
  w8(REG_CFG6, 0x10);
  smuxFn();
  enableSMUX();
  enableSP(true);
  if (!waitAVALID()) {
    status2 = r8(REG_STATUS2);
    enableSP(false);
    return false;
  }
  status2 = r8(REG_STATUS2);
  for (uint8_t i = 0; i < 6; i++) output[i] = r16(REG_CH_DATA_L + i * 2);
  return true;
}

void setupAS7341() {
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);
  delay(50);
  w8(REG_CFG0, 0x00);
  w8(REG_ENABLE, 0x01);
  delay(10);
  uint8_t id = r8(0x92);
  Serial.printf("AS7341 ID=0x%02X %s\n", id, ((id >> 2) == 0b001001) ? "OK" : "BAD!");
  w8(REG_ATIME, SENSOR_ATIME);
  w8(REG_ASTEP_L, SENSOR_ASTEP & 0xFF);
  w8(REG_ASTEP_H, SENSOR_ASTEP >> 8);
  w8(REG_CFG1, DEFAULT_GAIN_CODE);
}

WiFiServer server(TCP_PORT);
WiFiClient client;
uint32_t sampleIntervalMs = 1000;
bool triggerOnce = false;
uint32_t seqCounter = 0;
unsigned long lastSample = 0;
uint8_t currentGainCode = DEFAULT_GAIN_CODE;

void handleCommand(const String& command) {
  if (command == "t") {
    triggerOnce = true;
    client.println("{\"ack\":\"trigger\"}");
  } else if (command == "ping") {
    client.println("{\"ack\":\"pong\"}");
  } else if (command.startsWith("g")) {
    int gain = constrain(command.substring(1).toInt(), 0, 10);
    w8(REG_CFG1, gain);
    currentGainCode = (uint8_t)gain;
    client.printf("{\"ack\":\"gain\",\"value\":%d}\n", gain);
  } else if (command.startsWith("i")) {
    int interval = command.substring(1).toInt();
    if (interval < 100) interval = 100;
    sampleIntervalMs = interval;
    client.printf("{\"ack\":\"interval\",\"ms\":%d}\n", interval);
  } else {
    client.printf("{\"ack\":\"unknown\",\"got\":\"%s\"}\n", command.c_str());
  }
}

void sampleAndSend() {
  uint16_t low[6], high[6];
  uint8_t lowStatus = 0, highStatus = 0;
  bool lowOK = measureBatch(smux_F1F4_ClearNIR, low, lowStatus);
  bool highOK = measureBatch(smux_F5F8_ClearNIR, high, highStatus);
  if (!lowOK || !highOK) {
    Serial.printf(
      "{\"error\":\"measurement_timeout\",\"low_ok\":%s,\"high_ok\":%s}\n",
      lowOK ? "true" : "false", highOK ? "true" : "false"
    );
    return;
  }

  uint16_t F1=low[0], F2=low[1], F3=low[2], F4=low[3];
  uint16_t F5=high[0], F6=high[1], F7=high[2], F8=high[3];
  uint16_t clear=(low[4]+high[4])/2, nir=(low[5]+high[5])/2;
  uint32_t sequence = ++seqCounter;

  bool saturated = ((lowStatus | highStatus) & STATUS2_SATURATION_MASK) != 0;
  const uint16_t channels[10] = {F1,F2,F3,F4,F5,F6,F7,F8,clear,nir};
  for (uint8_t i = 0; i < 10; i++) {
    if (channels[i] >= ADC_FULL_SCALE) {
      saturated = true;
      break;
    }
  }

  char buffer[384];
  int length = snprintf(
    buffer, sizeof(buffer),
    "{\"seq\":%lu,\"F1\":%u,\"F2\":%u,\"F3\":%u,\"F4\":%u,"
    "\"F5\":%u,\"F6\":%u,\"F7\":%u,\"F8\":%u,\"Clear\":%u,\"NIR\":%u,"
    "\"gain_code\":%u,\"atime\":%u,\"astep\":%u,\"full_scale\":%u,"
    "\"saturated\":%s}\n",
    (unsigned long)sequence, F1,F2,F3,F4,F5,F6,F7,F8,clear,nir,
    currentGainCode,SENSOR_ATIME,SENSOR_ASTEP,ADC_FULL_SCALE,
    saturated ? "true" : "false"
  );
  if (length <= 0 || length >= (int)sizeof(buffer)) {
    Serial.println("{\"error\":\"json_buffer_overflow\"}");
    return;
  }
  if (client && client.connected()) client.write((const uint8_t*)buffer, length);
  Serial.write(buffer, length);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== C3 AS7341 -> WiFi/TCP ===");
  setupAS7341();

  Serial.printf("Connecting Wi-Fi: %s ...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 30000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi connection failed; check secrets.h and restart");
    while (1) delay(1000);
  }
  Serial.printf(
    "Wi-Fi connected, IP=%s, RSSI=%d dBm\n",
    WiFi.localIP().toString().c_str(), WiFi.RSSI()
  );
  server.begin();
  server.setNoDelay(true);
  Serial.printf("TCP server listening on %u\n", TCP_PORT);
}

void loop() {
  if (server.hasClient()) {
    if (client && client.connected()) client.stop();
    client = server.accept();
    client.setNoDelay(true);
    Serial.printf("PC connected: %s\n", client.remoteIP().toString().c_str());
    client.println("{\"hello\":\"C3-AS7341\",\"port\":3333}");
  }
  if (client && client.connected() && client.available()) {
    String command = client.readStringUntil('\n');
    command.trim();
    if (command.length()) handleCommand(command);
  }
  unsigned long now = millis();
  if (triggerOnce || now - lastSample >= sampleIntervalMs) {
    triggerOnce = false;
    lastSample = now;
    sampleAndSend();
  }
}
