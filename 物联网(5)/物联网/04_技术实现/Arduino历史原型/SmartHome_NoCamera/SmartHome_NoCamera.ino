#include <Adafruit_NeoPixel.h>
#include <DHT.h>
#include <Servo.h>
#include <U8g2lib.h>
#include <ctype.h>
#include <math.h>
#include <string.h>

// Fixed module pins from the existing test sketches.
const byte PIR_PIN = 2;
const byte DHT_PIN = 3;
const byte RGB_PIN = 4;
const byte FAN_INA = 5;
const byte FAN_INB = 6;
const byte OLED_SCK_PIN = 7;
const byte OLED_SDA_PIN = 8;
const byte OLED_RES_PIN = 9;
const byte OLED_DC_PIN = 10;
const byte SERVO_PIN = 11;
const byte LIGHT_PIN = A1;

#define DHTTYPE DHT11

const byte NUM_LEDS = 1;
const byte FAN_SPEED = 100;

// The existing light module reads larger values when the room is darker.
const int LIGHT_THRESHOLD = 800;

const float FAN_ON_TEMP_C = 30.0;
const float FAN_OFF_TEMP_C = 28.5;

const int DOOR_CLOSED_ANGLE = 0;
const int DOOR_OPEN_ANGLE = 90;

const unsigned long LIGHT_READ_INTERVAL_MS = 100UL;
const unsigned long DHT_READ_INTERVAL_MS = 2000UL;
const unsigned long STATUS_INTERVAL_MS = 3000UL;
const unsigned long NO_MOTION_DELAY_MS = 30000UL;
const unsigned long DOOR_OPEN_TIME_MS = 5000UL;
const unsigned long SERVO_HOLD_TIME_MS = 700UL;
const unsigned long DHT_ERROR_PRINT_INTERVAL_MS = 10000UL;
const unsigned long SERIAL_IDLE_COMMAND_MS = 800UL;
const unsigned long OLED_UPDATE_INTERVAL_MS = 5000UL;

const char ACCESS_CODE[] = "1234";

DHT dht(DHT_PIN, DHTTYPE);
Adafruit_NeoPixel strip(NUM_LEDS, RGB_PIN, NEO_GRB + NEO_KHZ800);
Servo doorServo;

// 7-pin SPI OLED, CS is tied to GND, so the software-SPI driver does not use CS.
U8G2_SSD1306_128X64_NONAME_1_4W_SW_SPI oled(
  U8G2_R0,
  OLED_SCK_PIN,
  OLED_SDA_PIN,
  U8X8_PIN_NONE,
  OLED_DC_PIN,
  OLED_RES_PIN
);

enum FanMode {
  FAN_AUTO,
  FAN_FORCE_ON,
  FAN_FORCE_OFF
};

FanMode fanMode = FAN_AUTO;

bool fanState = false;
bool lampState = false;
bool doorIsOpen = false;
bool dhtOk = false;

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

void setup() {
  Serial.begin(9600);

  pinMode(PIR_PIN, INPUT);
  pinMode(FAN_INA, OUTPUT);
  pinMode(FAN_INB, OUTPUT);

  fanOff();

  dht.begin();
  setupOled();

  strip.begin();
  strip.setBrightness(80);
  lampOff();

  closeDoor(millis());

  Serial.println(F("Smart furniture controller started."));
  Serial.println(F("Type 1234 or PIN 1234 to unlock the door."));
  Serial.println(F("Commands: STATUS, OPEN, CLOSE, FAN AUTO, FAN ON, FAN OFF, HELP"));
}

void loop() {
  const unsigned long now = millis();

  handleSerialInput(now);
  updateLightAndLamp(now);
  updateTemperature(now);
  updateFan();
  updateDoor(now);
  updateOled(now);
  printStatusIfNeeded(now);
}

void updateLightAndLamp(unsigned long now) {
  if (now - lastLightReadAt < LIGHT_READ_INTERVAL_MS) {
    return;
  }

  lastLightReadAt = now;
  lightValue = readLightAverage();
  isDark = lightValue > LIGHT_THRESHOLD;

  pirRawValue = digitalRead(PIR_PIN);
  if (pirRawValue == HIGH) {
    lastMotionAt = now;
  }

  hasRecentMotion = lastMotionAt > 0 && now - lastMotionAt <= NO_MOTION_DELAY_MS;

  if (isDark && hasRecentMotion) {
    lampOn();
  } else {
    lampOff();
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
      Serial.println(F("DHT11 read failed. Check wiring on D3."));
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
    oled.print(F("Door:"));
    oled.print(doorIsOpen ? F("OPEN 90deg") : F("CLOSED 0deg"));
  } while (oled.nextPage());
}

void moveDoorServoTo(int targetAngle, unsigned long now) {
  if (!doorServo.attached()) {
    doorServo.attach(SERVO_PIN);
    delay(20);
  }

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
}

void closeDoor(unsigned long now) {
  moveDoorServoTo(DOOR_CLOSED_ANGLE, now);
  doorIsOpen = false;
  Serial.println(F("Door closed."));
}

void lampOn() {
  if (lampState) {
    return;
  }

  strip.setPixelColor(0, strip.Color(255, 150, 40));
  strip.show();
  lampState = true;
}

void lampOff() {
  if (!lampState) {
    strip.clear();
    strip.show();
    return;
  }

  strip.clear();
  strip.show();
  lampState = false;
}

void fanOn(byte speedValue) {
  if (fanState) {
    return;
  }

  analogWrite(FAN_INA, speedValue);
  digitalWrite(FAN_INB, LOW);
  fanState = true;
  Serial.println(F("Fan on."));
}

void fanOff() {
  if (!fanState) {
    digitalWrite(FAN_INA, LOW);
    digitalWrite(FAN_INB, LOW);
    return;
  }

  digitalWrite(FAN_INA, LOW);
  digitalWrite(FAN_INB, LOW);
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
  } else if (equalsIgnoreCase(command, "HELP")) {
    Serial.println(F("Type 1234 or PIN 1234 to unlock the door."));
    Serial.println(F("Commands: STATUS, OPEN, CLOSE, FAN AUTO, FAN ON, FAN OFF, HELP"));
  } else {
    Serial.println(F("Unknown command. Type HELP."));
  }
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
