#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <PubSubClient.h>  // 新增 MQTT 库
#include <DHT.h>

// ====== WiFi 配置 ======
const char* ssid     = "";
const char* password = "";
const char* hostname = "esp8266_1";

// ====== MQTT 配置 ======
const char* mqtt_server = "192.168.1.69";  // ← 树莓派 IP（运行 mosquitto）
const int   mqtt_port   = 1883;
const char* mqtt_topic  = "sensor/dht11";
const char* client_id   = "location_1";

// ====== DHT 配置 ======
#define DHTPIN  2
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

WiFiClient espClient;
PubSubClient mqttClient(espClient);

void setup() {
  delay(100);
  Serial.begin(115200);

  WiFi.mode(WIFI_STA); 
  WiFi.hostname(hostname); 

  // 连接 WiFi
  Serial.print("Connecting to ");
  Serial.println(ssid);
  WiFi.begin(ssid, password);

  int timeout = 0;
  while (WiFi.status() != WL_CONNECTED && timeout < 30) {
    delay(1000);
    Serial.print(".");
    timeout++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected!");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
    Serial.print("Hostname: ");
    Serial.println(WiFi.hostname());
  } else {
    Serial.println("\nFailed to connect to WiFi!");
    return; // 无法连 WiFi，不继续初始化 MQTT
  }

  // 初始化 DHT
  dht.begin();
  Serial.println("DHT11 Sensor Ready");

  // 初始化 MQTT
  mqttClient.setServer(mqtt_server, mqtt_port);
}

// MQTT 重连函数
void reconnectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("Attempting MQTT connection...");
    if (mqttClient.connect(client_id)) { // 客户端 ID
      Serial.println("connected");
    } else {
      Serial.print("failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" try again in 3 seconds");
      delay(3000);
    }
  }
}

void loop() {
  // 确保 MQTT 连接
  if (!mqttClient.connected()) {
    reconnectMQTT();
  }
  mqttClient.loop(); // 处理 MQTT 后台任务

  float humidity = dht.readHumidity();
  float temperature = dht.readTemperature();

  if (isnan(humidity) || isnan(temperature)) {
    Serial.println("Failed to read from DHT sensor!");
  } else {
    Serial.print("Temperature: ");
    Serial.print(temperature, 1);
    Serial.print(" C\t");
    Serial.print("Humidity: ");
    Serial.print(humidity, 1);
    Serial.println(" %");

    // 发送数据到 MQTT
    char payload[64];
    snprintf(payload, sizeof(payload), "%s,%.1f,%.1f", client_id, temperature, humidity);
    mqttClient.publish(mqtt_topic, payload);
    Serial.print("Published to MQTT: ");
    Serial.println(payload);

    // 每 10 秒打印设备信息
    static unsigned long lastPrint = 0;
    if (millis() - lastPrint > 10000) {
      lastPrint = millis();
      Serial.print("[Device] Hostname: ");
      Serial.print(WiFi.hostname());
      Serial.print(", IP: ");
      Serial.println(WiFi.localIP());
    }
  }

  delay(2000);
}