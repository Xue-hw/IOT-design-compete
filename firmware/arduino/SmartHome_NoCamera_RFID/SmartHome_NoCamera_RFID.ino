#include <Adafruit_NeoPixel.h>
#include <DHT.h>
#include <MFRC522.h>
#include <Servo.h>
#include <U8g2lib.h>
#include <SPI.h>
#include <ctype.h>
#include <math.h>
#include <string.h>

// Optimized Arduino UNO pin map.
// D0/D1 stay free for USB serial.
const byte PIR_PIN = 2;
const byte RFID_RST_PIN = 3;
const byte DHT_PIN = A3;
const byte RGB_PIN = 4;
const byte FAN_PWM_PIN = 5;
const byte RFID_SS_PIN = 6;
const byte OLED_CS_PIN = 9;
const byte SPI_SS_PIN = 10;
const byte SERVO_PIN = A0;
const byte LIGHT_PIN = A1;
const byte OLED_DC_PIN = A2;

#define DHTTYPE DHT11

const byte NUM_LEDS = 8;
const byte FAN_SPEED = 100;
const byte DEFAULT_LAMP_BRIGHTNESS_PERCENT = 30;

// The existing light module reads larger values when the room is darker.
const int LIGHT_DIM_THRESHOLD = 500;
const int LIGHT_DARK_THRESHOLD = 800;
const byte AUTO_LAMP_BRIGHT_BRIGHTNESS = 0;
const byte AUTO_LAMP_DIM_BRIGHTNESS = 20;
const byte AUTO_LAMP_DARK_BRIGHTNESS = 60;

const float FAN_ON_TEMP_C = 30.0;
const float FAN_OFF_TEMP_C = 28.5;

const int DOOR_CLOSED_ANGLE = 0;
const int DOOR_OPEN_ANGLE = 90;

const unsigned long LIGHT_READ_INTERVAL_MS = 100UL;
const unsigned long DHT_READ_INTERVAL_MS = 2000UL;
const unsigned long STATUS_INTERVAL_MS = 3000UL;
const unsigned long NO_MOTION_DELAY_MS = 30000UL;
const unsigned long DOOR_OPEN_TIME_MS = 5000UL;
const unsigned long SERVO_HOLD_TIME_MS = 1200UL;
const unsigned long DHT_ERROR_PRINT_INTERVAL_MS = 10000UL;
const unsigned long SERIAL_IDLE_COMMAND_MS = 800UL;
const unsigned long OLED_UPDATE_INTERVAL_MS = 5000UL;
const unsigned long RFID_UNLOCK_COOLDOWN_MS = 7000UL;

const char ACCESS_CODE[] = "1234";

DHT dht(DHT_PIN, DHTTYPE);
MFRC522 rfid(RFID_SS_PIN, RFID_RST_PIN);
Adafruit_NeoPixel strip(NUM_LEDS, RGB_PIN, NEO_GRB + NEO_KHZ800);
Servo doorServo;

// OLED uses UNO hardware SPI to save IO:
// SCK -> D13, SDA/DIN -> D11, DC -> A2, RES -> RESET, CS -> D9.
U8G2_SSD1306_128X64_NONAME_1_4W_HW_SPI oled(
  U8G2_R0,
  OLED_CS_PIN,
  OLED_DC_PIN,
  U8X8_PIN_NONE
);

enum FanMode {
  FAN_AUTO,
  FAN_FORCE_ON,
  FAN_FORCE_OFF
};

enum LampMode {
  LAMP_AUTO,
  LAMP_MANUAL
};

FanMode fanMode = FAN_AUTO;
LampMode lampMode = LAMP_AUTO;

bool fanState = false;
bool lampState = false;
bool doorIsOpen = false;
bool dhtOk = false;
byte lampBrightnessPercent = DEFAULT_LAMP_BRIGHTNESS_PERCENT;
byte manualLampBrightnessPercent = DEFAULT_LAMP_BRIGHTNESS_PERCENT;
bool manualLampOn = false;

float temperatureC = NAN;
float humidityPercent = NAN;

int lightValue = 0;
int pirRawValue = LOW;
bool isDark = false;
bool hasRecentMotion = false;

unsigned long lastLightReadAt = 0;
unsigned long lastDhtReadAt = 0;
unsigned long lastStatusPrintAt = 0;
unsigned long lastDhtErrorPrintAt = 0;
unsigned long lastOledUpdateAt = 0;
unsigned long lastMotionAt = 0;
unsigned long doorOpenedAt = 0;
unsigned long servoDetachAt = 0;
unsigned long lastRfidUnlockAt = 0;

void setup() {
  Serial.begin(9600);

  pinMode(PIR_PIN, INPUT);
  pinMode(FAN_PWM_PIN, OUTPUT);
  pinMode(OLED_CS_PIN, OUTPUT);
  pinMode(RFID_SS_PIN, OUTPUT);
  pinMode(SPI_SS_PIN, OUTPUT);
  digitalWrite(OLED_CS_PIN, HIGH);
  digitalWrite(RFID_SS_PIN, HIGH);
  digitalWrite(SPI_SS_PIN, HIGH);
  SPI.begin();

  fanOff();

  dht.begin();
  setupOled();
  setupRfid();

  strip.begin();
  applyLampBrightness();
  setEffectiveLampBrightness(0);

  closeDoor(millis());

  Serial.println(F("Smart furniture controller started."));
  Serial.println(F("Type 1234 or PIN 1234 to unlock the door."));
  printHelp();
  printLampBrightness();
}

void loop() {
  const unsigned long now = millis();

  handleSerialInput(now);
  updateLightAndLamp(now);
  updateTemperature(now);
  updateFan();
  updateDoor(now);
  handleRfidAccess(now);
  updateOled(now);
  printStatusIfNeeded(now);
}

void updateLightAndLamp(unsigned long now) {
  if (now - lastLightReadAt < LIGHT_READ_INTERVAL_MS) {
    return;
  }

  lastLightReadAt = now;
  lightValue = readLightAverage();
  isDark = lightValue > LIGHT_DARK_THRESHOLD;

  pirRawValue = digitalRead(PIR_PIN);
  if (pirRawValue == HIGH) {
    lastMotionAt = now;
  }

  hasRecentMotion = lastMotionAt > 0 && now - lastMotionAt <= NO_MOTION_DELAY_MS;

  if (lampMode == LAMP_AUTO) {
    setEffectiveLampBrightness(calculateAutoLampBrightness());
  }
}

int readLightAverage() {
  long sum = 0;

  for (byte i = 0; i < 10; i++) {
    sum += analogRead(LIGHT_PIN);
    delay(2);
  }

  return sum / 10;
}

byte calculateAutoLampBrightness() {
  if (!hasRecentMotion) {
    return 0;
  }

  if (lightValue > LIGHT_DARK_THRESHOLD) {
    return AUTO_LAMP_DARK_BRIGHTNESS;
  }

  if (lightValue > LIGHT_DIM_THRESHOLD) {
    return AUTO_LAMP_DIM_BRIGHTNESS;
  }

  return AUTO_LAMP_BRIGHT_BRIGHTNESS;
}

void updateTemperature(unsigned long now) {
  if (now - lastDhtReadAt < DHT_READ_INTERVAL_MS) {
    return;
  }

  lastDhtReadAt = now;

  const float newHumidity = dht.readHumidity();
  const float newTemperature = dht.readTemperature();

  if (isnan(newHumidity) || isnan(newTemperature)) {
    dhtOk = false;

    if (now - lastDhtErrorPrintAt >= DHT_ERROR_PRINT_INTERVAL_MS) {
      lastDhtErrorPrintAt = now;
      Serial.println(F("DHT11 read failed. Check wiring on A3."));
    }

    return;
  }

  humidityPercent = newHumidity;
  temperatureC = newTemperature;
  dhtOk = true;
}

void updateFan() {
  if (fanMode == FAN_FORCE_ON) {
    fanOn(FAN_SPEED);
    return;
  }

  if (fanMode == FAN_FORCE_OFF) {
    fanOff();
    return;
  }

  if (!dhtOk) {
    fanOff();
    return;
  }

  if (!fanState && temperatureC >= FAN_ON_TEMP_C) {
    fanOn(FAN_SPEED);
  } else if (fanState && temperatureC <= FAN_OFF_TEMP_C) {
    fanOff();
  }
}

void updateDoor(unsigned long now) {
  if (doorIsOpen && now - doorOpenedAt >= DOOR_OPEN_TIME_MS) {
    closeDoor(now);
  }

  releaseDoorServoIfReady(now);
}

void setupRfid() {
  rfid.PCD_Init();
  byte version = rfid.PCD_ReadRegister(rfid.VersionReg);
  Serial.print(F("RC522 ready. SS=D6 RST=D3 Version=0x"));
  Serial.println(version, HEX);

  if (version == 0x00 || version == 0xFF) {
    Serial.println(F("RC522 not responding. Check 3.3V, GND, D3/D6/D11/D12/D13."));
  }
}

void handleRfidAccess(unsigned long now) {
  if (now - lastRfidUnlockAt < RFID_UNLOCK_COOLDOWN_MS) {
    return;
  }

  if (!rfid.PICC_IsNewCardPresent()) {
    return;
  }

  if (!rfid.PICC_ReadCardSerial()) {
    return;
  }

  Serial.print(F("RFID card UID: "));
  printRfidUid();
  Serial.println();
  Serial.println(F("RFID access granted."));

  authorizeDoor(now);
  lastRfidUnlockAt = now;

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}

void printRfidUid() {
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) {
      Serial.print('0');
    }

    Serial.print(rfid.uid.uidByte[i], HEX);

    if (i + 1 < rfid.uid.size) {
      Serial.print(':');
    }
  }
}

void setupOled() {
  oled.begin();
  oled.setContrast(180);
  drawOledScreen(F("Starting"));
}

void updateOled(unsigned long now) {
  if (now - lastOledUpdateAt < OLED_UPDATE_INTERVAL_MS) {
    return;
  }

  lastOledUpdateAt = now;
  drawOledScreen(dhtOk ? F("DHT OK") : F("DHT N/A"));
}

void drawOledScreen(const __FlashStringHelper *sensorStatus) {
  oled.firstPage();

  do {
    oled.setFont(u8g2_font_6x10_tf);
    oled.drawStr(0, 10, "Smart Home");

    oled.setCursor(82, 10);
    oled.print(sensorStatus);

    oled.setFont(u8g2_font_logisoso24_tf);
    oled.setCursor(0, 43);

    if (dhtOk) {
      oled.print(temperatureC, 1);
      oled.print(F(" C"));
    } else {
      oled.print(F("--.- C"));
    }

    oled.setFont(u8g2_font_6x10_tf);
    oled.setCursor(0, 54);
    oled.print(F("Hum: "));

    if (dhtOk) {
      oled.print(humidityPercent, 0);
      oled.print(F("%"));
    } else {
      oled.print(F("--%"));
    }

    oled.setCursor(70, 54);
    oled.print(F("Fan:"));
    oled.print(fanState ? F("ON") : F("OFF"));

    oled.setCursor(0, 63);
    oled.print(F("Now:"));
    oled.print(lampBrightnessPercent);
    oled.print(lampMode == LAMP_AUTO ? F("A%") : F("M%"));

    oled.setCursor(66, 63);
    oled.print(F("D:"));
    oled.print(doorIsOpen ? F("OPEN90") : F("CLOSE0"));
  } while (oled.nextPage());
}

void moveDoorServoTo(int targetAngle, unsigned long now) {
  if (!doorServo.attached()) {
    doorServo.attach(SERVO_PIN);
    delay(50);
  }

  doorServo.write(targetAngle);
  delay(20);
  doorServo.write(targetAngle);
  servoDetachAt = now + SERVO_HOLD_TIME_MS;
}

void releaseDoorServoIfReady(unsigned long now) {
  if (doorServo.attached() && servoDetachAt > 0 && (long)(now - servoDetachAt) >= 0) {
    doorServo.detach();
    servoDetachAt = 0;
  }
}

void authorizeDoor(unsigned long now) {
  moveDoorServoTo(DOOR_OPEN_ANGLE, now);
  doorIsOpen = true;
  doorOpenedAt = now;
  Serial.println(F("Access granted. Door opened."));
  drawOledScreen(dhtOk ? F("DHT OK") : F("DHT N/A"));
}

void closeDoor(unsigned long now) {
  moveDoorServoTo(DOOR_CLOSED_ANGLE, now);
  doorIsOpen = false;
  Serial.println(F("Door closed."));
  drawOledScreen(dhtOk ? F("DHT OK") : F("DHT N/A"));
}

void lampOn() {
  setManualLampPower(true);
}

void lampOff() {
  setManualLampPower(false);
}

void setLampBrightnessPercent(byte percent) {
  if (percent > 100) {
    percent = 100;
  }

  lampMode = LAMP_MANUAL;
  manualLampBrightnessPercent = percent;
  manualLampOn = percent > 0;
  setEffectiveLampBrightness(manualLampOn ? manualLampBrightnessPercent : 0);
}

void setManualLampPower(bool on) {
  lampMode = LAMP_MANUAL;
  manualLampOn = on;

  if (manualLampOn && manualLampBrightnessPercent == 0) {
    manualLampBrightnessPercent = DEFAULT_LAMP_BRIGHTNESS_PERCENT;
  }

  setEffectiveLampBrightness(manualLampOn ? manualLampBrightnessPercent : 0);
}

void setLampAutoMode() {
  lampMode = LAMP_AUTO;
  setEffectiveLampBrightness(calculateAutoLampBrightness());
  drawOledScreen(dhtOk ? F("DHT OK") : F("DHT N/A"));
}

void setEffectiveLampBrightness(byte percent) {
  if (percent > 100) {
    percent = 100;
  }

  if (lampBrightnessPercent == percent && lampState == (percent > 0)) {
    return;
  }

  lampBrightnessPercent = percent;
  lampState = percent > 0;
  applyLampBrightness();
  renderLamp();
  drawOledScreen(dhtOk ? F("DHT OK") : F("DHT N/A"));
}

void renderLamp() {
  strip.clear();

  if (lampState) {
    strip.setPixelColor(0, strip.Color(255, 150, 40));
  }

  strip.show();
}

void applyLampBrightness() {
  const byte brightnessValue = map(lampBrightnessPercent, 0, 100, 0, 255);
  strip.setBrightness(brightnessValue);
}

void fanOn(byte speedValue) {
  if (fanState) {
    return;
  }

  analogWrite(FAN_PWM_PIN, speedValue);
  fanState = true;
  Serial.println(F("Fan on."));
}

void fanOff() {
  if (!fanState) {
    digitalWrite(FAN_PWM_PIN, LOW);
    return;
  }

  digitalWrite(FAN_PWM_PIN, LOW);
  fanState = false;
  Serial.println(F("Fan off."));
}

void handleSerialInput(unsigned long now) {
  static char commandBuffer[32];
  static byte commandLength = 0;
  static unsigned long lastCommandCharAt = 0;

  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      commandBuffer[commandLength] = '\0';
      processCommand(commandBuffer, now);
      commandLength = 0;
      continue;
    }

    if (commandLength < sizeof(commandBuffer) - 1) {
      commandBuffer[commandLength++] = c;
      lastCommandCharAt = now;
    }
  }

  if (commandLength > 0 && now - lastCommandCharAt >= SERIAL_IDLE_COMMAND_MS) {
    commandBuffer[commandLength] = '\0';
    processCommand(commandBuffer, now);
    commandLength = 0;
  }
}

void processCommand(char *rawCommand, unsigned long now) {
  char *command = trimText(rawCommand);

  if (command[0] == '\0') {
    return;
  }

  if (matchesAccessCode(command)) {
    authorizeDoor(now);
  } else if (equalsIgnoreCase(command, "OPEN")) {
    authorizeDoor(now);
  } else if (equalsIgnoreCase(command, "CLOSE")) {
    closeDoor(now);
  } else if (equalsIgnoreCase(command, "STATUS")) {
    printStatus(true);
  } else if (equalsIgnoreCase(command, "FAN AUTO")) {
    fanMode = FAN_AUTO;
    Serial.println(F("Fan mode: AUTO."));
  } else if (equalsIgnoreCase(command, "FAN ON")) {
    fanMode = FAN_FORCE_ON;
    fanOn(FAN_SPEED);
    Serial.println(F("Fan mode: FORCE ON."));
  } else if (equalsIgnoreCase(command, "FAN OFF")) {
    fanMode = FAN_FORCE_OFF;
    fanOff();
    Serial.println(F("Fan mode: FORCE OFF."));
  } else if (equalsIgnoreCase(command, "LIGHT AUTO") || equalsIgnoreCase(command, "LAMP AUTO")) {
    setLampAutoMode();
    printLampBrightness();
  } else if (equalsIgnoreCase(command, "LIGHT ON") || equalsIgnoreCase(command, "LAMP ON")) {
    lampOn();
    printLampBrightness();
  } else if (equalsIgnoreCase(command, "LIGHT OFF") || equalsIgnoreCase(command, "LAMP OFF")) {
    lampOff();
    printLampBrightness();
  } else if (startsWithIgnoreCase(command, "BRIGHT ")) {
    setLampBrightnessFromCommand(command + 7);
  } else if (startsWithIgnoreCase(command, "LIGHT ")) {
    setLampBrightnessFromCommand(command + 6);
  } else if (startsWithIgnoreCase(command, "B ")) {
    setLampBrightnessFromCommand(command + 2);
  } else if (equalsIgnoreCase(command, "B+")) {
    changeLampBrightnessBy(10);
  } else if (equalsIgnoreCase(command, "B-")) {
    changeLampBrightnessBy(-10);
  } else if (equalsIgnoreCase(command, "B?") || equalsIgnoreCase(command, "BRIGHT?")) {
    printLampBrightness();
  } else if (equalsIgnoreCase(command, "HELP")) {
    printHelp();
  } else {
    Serial.println(F("Unknown command. Type HELP."));
  }
}

void setLampBrightnessFromCommand(char *valueText) {
  char *trimmedValue = trimText(valueText);
  int percent = atoi(trimmedValue);

  if (percent < 0) {
    percent = 0;
  } else if (percent > 100) {
    percent = 100;
  }

  setLampBrightnessPercent((byte)percent);
  printLampBrightness();
}

void changeLampBrightnessBy(int delta) {
  int percent = lampBrightnessPercent + delta;

  if (percent < 0) {
    percent = 0;
  } else if (percent > 100) {
    percent = 100;
  }

  setLampBrightnessPercent((byte)percent);
  printLampBrightness();
}

void printLampBrightness() {
  Serial.print(F("Lamp mode: "));
  Serial.print(lampMode == LAMP_AUTO ? F("AUTO") : F("MANUAL"));
  Serial.print(F(" | state: "));
  Serial.print(lampState ? F("ON") : F("OFF"));
  Serial.print(F(" | brightness: "));
  Serial.print(lampBrightnessPercent);
  Serial.println(F("%"));
}

void printHelp() {
  Serial.println(F("Type 1234 or PIN 1234 to unlock the door."));
  Serial.println(F("Commands: STATUS, OPEN, CLOSE, FAN AUTO, FAN ON, FAN OFF"));
  Serial.println(F("Lamp: LIGHT AUTO, LIGHT ON, LIGHT OFF, BRIGHT/LIGHT/B 0-100, B+, B-, B?"));
  Serial.println(F("Auto lamp brightness follows light sensor: 0%, 20%, 60%."));
}

bool matchesAccessCode(char *command) {
  char *code = command;

  if (startsWithIgnoreCase(command, "PIN ")) {
    code = trimText(command + 4);
  } else if (startsWithIgnoreCase(command, "CODE ")) {
    code = trimText(command + 5);
  }

  return strcmp(code, ACCESS_CODE) == 0;
}

char *trimText(char *text) {
  while (*text != '\0' && isspace((unsigned char)*text)) {
    text++;
  }

  char *end = text + strlen(text);
  while (end > text && isspace((unsigned char)*(end - 1))) {
    end--;
  }

  *end = '\0';
  return text;
}

bool equalsIgnoreCase(const char *left, const char *right) {
  while (*left != '\0' && *right != '\0') {
    if (toupper((unsigned char)*left) != toupper((unsigned char)*right)) {
      return false;
    }

    left++;
    right++;
  }

  return *left == '\0' && *right == '\0';
}

bool startsWithIgnoreCase(const char *text, const char *prefix) {
  while (*prefix != '\0') {
    if (*text == '\0' || toupper((unsigned char)*text) != toupper((unsigned char)*prefix)) {
      return false;
    }

    text++;
    prefix++;
  }

  return true;
}

void printStatusIfNeeded(unsigned long now) {
  if (now - lastStatusPrintAt < STATUS_INTERVAL_MS) {
    return;
  }

  lastStatusPrintAt = now;
  printStatus(false);
}

void printStatus(bool forceHeader) {
  if (forceHeader) {
    Serial.println(F("---- STATUS ----"));
  }

  Serial.print(F("Light="));
  Serial.print(lightValue);
  Serial.print(isDark ? F(" dark") : F(" bright"));

  Serial.print(F(" | PIR="));
  Serial.print(pirRawValue);
  Serial.print(hasRecentMotion ? F(" motion") : F(" no-motion"));

  Serial.print(F(" | Lamp="));
  Serial.print(lampState ? F("ON") : F("OFF"));
  Serial.print(F(" Mode="));
  Serial.print(lampMode == LAMP_AUTO ? F("AUTO") : F("MANUAL"));
  Serial.print(F(" Bri="));
  Serial.print(lampBrightnessPercent);
  Serial.print(F("%"));

  Serial.print(F(" | Temp="));
  if (dhtOk) {
    Serial.print(temperatureC, 1);
    Serial.print(F("C Hum="));
    Serial.print(humidityPercent, 1);
    Serial.print(F("%"));
  } else {
    Serial.print(F("N/A"));
  }

  Serial.print(F(" | Fan="));
  Serial.print(fanState ? F("ON") : F("OFF"));

  Serial.print(F(" | Door="));
  Serial.println(doorIsOpen ? F("OPEN") : F("CLOSED"));
}
