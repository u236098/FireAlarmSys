#include <SoftwareSerial.h>
#include <Wire.h>
#include "rgb_lcd.h"
#include <DHT.h>

// -------- CONFIG DHT11 --------
#define DHTPIN 4
#define DHTTYPE DHT11

// -------- CONFIG VENTILADOR --------
const int FANpin = 9;

// -------- CONFIG BUZZER --------
#define BUZZER_PIN 7

// -------- CONFIG SENSOR GAS --------
#define GAS_AOUT A3

// -------- LCD RGB --------
rgb_lcd lcd;

// -------- COLORES LCD --------
const int COLOR_RED[3]   = {255, 0, 0};
const int COLOR_GREEN[3] = {0, 255, 0};
const int COLOR_BLUE[3]  = {0, 0, 255};

// -------- DHT --------
DHT dht(DHTPIN, DHTTYPE);

// -------- UART to Zolertia --------
// This is the cable link between the Arduino and the sender Zolertia.
// Zolertia TX -> Arduino pin 10, Arduino pin 11 -> Zolertia RX.
// In our prototype pin 11 was connected directly. For a safer final version,
// this Arduino 5V TX signal should be reduced before entering the 3.3V Zolertia RX.
SoftwareSerial zolertia(10, 11);

// -------- Variables --------
static unsigned long lastSend = 0;
static unsigned int count = 0;
static bool lastAlert = false;
static bool buzzerManual = false;
static bool buzzerManualState = false;
static bool buzzerState = false;

// -------- FUNCION COLOR LCD --------
void setLCDColor(const int color[3]) {
  lcd.setRGB(color[0], color[1], color[2]);
}

void applyActuators(bool alert) {
  // The fan always follows the local fire/gas alarm logic.
  digitalWrite(FANpin, alert ? HIGH : LOW);

  // The buzzer can either follow the alarm automatically or be manually
  // controlled from the dashboard through the two Zolertia boards.
  bool nextBuzzerState = buzzerManual ? buzzerManualState : alert;
  digitalWrite(BUZZER_PIN, nextBuzzerState ? HIGH : LOW);
  buzzerState = nextBuzzerState;
}

void handleZolertiaCommand(String command) {
  // Commands arrive from the dashboard in this direction:
  // dashboard -> receiver Zolertia -> wireless -> sender Zolertia -> Arduino.
  command.trim();
  if (command.startsWith("CMD:")) {
    command = command.substring(4);
    command.trim();
  }

  if (command == "BUZZER_ON") {
    // Manual mode keeps the buzzer ON even if the sensor values are normal.
    buzzerManual = true;
    buzzerManualState = true;
    applyActuators(lastAlert);
    Serial.println("[Command] Buzzer manual ON");
  } else if (command == "BUZZER_OFF") {
    // Manual OFF is useful to silence the buzzer from the dashboard.
    buzzerManual = true;
    buzzerManualState = false;
    applyActuators(lastAlert);
    Serial.println("[Command] Buzzer manual OFF");
  } else if (command == "BUZZER_AUTO") {
    // Auto gives control back to the fire alarm thresholds.
    buzzerManual = false;
    applyActuators(lastAlert);
    Serial.println("[Command] Buzzer AUTO");
  }
}

void setup() {
  Serial.begin(115200);  // USB Serial for debugging
  zolertia.begin(9600);  // UART to Zolertia

  // Wait for sensors to stabilize
  delay(1000);

  // Inicializar DHT11
  dht.begin();

  // DHT11 needs time to warm up
  Serial.println("Waiting for DHT11 to stabilize...");
  delay(2000);

  // Inicializar LCD
  lcd.begin(16, 2);

  // Configurar pines
  pinMode(FANpin, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  // Apagar ventilador y buzzer al inicio
  digitalWrite(FANpin, LOW);
  digitalWrite(BUZZER_PIN, LOW);

  delay(500);
  zolertia.flush();

  Serial.println("Fire Alarm System Ready");
  Serial.println("Arduino UART -> Zolertia link established");

  // -------- PRUEBA LCD --------
  setLCDColor(COLOR_RED);
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Fire Alarm Init");
  delay(1000);

  setLCDColor(COLOR_GREEN);
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("System Ready");
  delay(1000);

  lcd.clear();
}

void loop() {
  // First we check if the sender Zolertia forwarded a dashboard command.
  if (zolertia.available()) {
    String fromZolertia = zolertia.readStringUntil('\n');
    Serial.print("[Zolertia] ");
    Serial.println(fromZolertia);
    handleZolertiaCommand(fromZolertia);
  }

  // Then every 2 seconds we read the sensors and send one JSON packet.
  if (millis() - lastSend > 2000) {
    count++;

    // -------- LEER DHT11 --------
    float temp = dht.readTemperature();
    float hum = dht.readHumidity();

    // -------- LEER SENSOR GAS --------
    int gasLevel = analogRead(GAS_AOUT);

    // -------- COMPROBAR ERROR DHT --------
    if (isnan(temp) || isnan(hum)) {
      Serial.println("Error leyendo DHT11");
      Serial.println("Check: 1) DATA wire on Pin 4, 2) VCC to 5V, 3) GND connected");
      Serial.println("For 3-pin DHT11: Add 10kΩ pull-up resistor from DATA to VCC");

      setLCDColor(COLOR_BLUE);
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("DHT11 Error");
      lcd.setCursor(0, 1);
      lcd.print("Check Wiring");

      lastAlert = false;
      applyActuators(lastAlert);

      lastSend = millis();
      return;
    }

    // -------- DETERMINAR ESTADO DE ALERTA --------
    // This is the local decision. Arduino reacts immediately even if the
    // wireless network or dashboard is not available.
    bool alert = false;
    bool tempAlert = (temp > 30);
    bool gasAlert = (gasLevel > 500);

    if (tempAlert || gasAlert) {
      alert = true;
      setLCDColor(COLOR_RED);
    } else {
      setLCDColor(COLOR_GREEN);
    }
    lastAlert = alert;
    applyActuators(alert);

    // -------- MOSTRAR EN LCD --------
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("T:");
    lcd.print(temp, 1);
    lcd.print("C ");
    lcd.print("H:");
    lcd.print(hum, 0);
    lcd.print("%");

    lcd.setCursor(0, 1);
    lcd.print("Gas:");
    lcd.print(gasLevel);
    if (alert) {
      lcd.print(" !");
    }

    // -------- ENVIAR JSON A ZOLERTIA --------
    // This JSON is what travels through the wireless Zolertia network and is
    // finally shown by the dashboard.
    // Format: {"id":N,"temp":XX.X,"hum":XX,"gas":XXX,"alert":0/1,"temp_alert":0/1,"gas_alert":0/1,"buzzer":0/1,"buzzer_manual":0/1}
    // Arduino snprintf does not reliably support %f, so temperature is
    // converted to an integer first and formatted manually.
    int tempInt = (int)(temp * 10);  // 24.5 -> 245 (will divide by 10 in dashboard)
    int humInt = (int)hum;

    char payload[170];
    snprintf(payload, sizeof(payload),
             "{\"id\":%u,\"temp\":%d.%d,\"hum\":%d,\"gas\":%d,\"alert\":%d,\"temp_alert\":%d,\"gas_alert\":%d,\"buzzer\":%d,\"buzzer_manual\":%d}",
             count, tempInt/10, tempInt%10, humInt, gasLevel, alert ? 1 : 0, tempAlert ? 1 : 0, gasAlert ? 1 : 0, buzzerState ? 1 : 0, buzzerManual ? 1 : 0);

    zolertia.print(payload);
    zolertia.print('\n');

    // -------- MOSTRAR EN SERIAL --------
    Serial.print("[Arduino -> Zolertia] ");
    Serial.println(payload);
    Serial.print("Status: ");
    Serial.println(alert ? "ALERT!" : "NORMAL");

    lastSend = millis();
  }
}
