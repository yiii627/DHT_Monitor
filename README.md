# 嵌入式温湿度监控系统

> 一个基于 **ESP8266 + DHT11 + MQTT + SQLite + Flask** 的轻量级物联网监控系统，支持多设备数据采集、实时状态更新、历史记录存储、阈值报警推送（微信）与 Web API 查询。

------

## 📦 项目组成

| 文件       | 作用                                                         |
| ---------- | ------------------------------------------------------------ |
| `client.c` | ESP8266 客户端固件：读取 DHT11 并通过 MQTT 上报数据          |
| `server.c` | C 后台服务：订阅 MQTT 消息，写入 SQLite，并触发 Server 酱微信报警 |
| `web.py`   | Flask Web 服务：提供 RESTful API，用于前端或移动端获取设备数据 |

------

## 🌐 系统架构

```
[ESP8266 + DHT11] 
        │ (WiFi)
        ↓
   [MQTT Broker] ←→ [server.c 后台服务] → [Server 酱 → 微信通知]
        │                │
        │                ↓
        │           [SQLite: dht_data.db]
        │                │
        └────→ [Flask Web API (web.py)] ←→ [Web 前端 / 移动端]
```

------

## ✨ 核心功能

- **多设备支持**
  每个 ESP8266 使用唯一 `client_id`（如 `location_1`）上报数据，自动创建独立历史表。
- **实时状态面板**
  所有设备最新数据实时写入 `latest_data` 表，无冷却延迟，确保前端显示最新状态。
- **高效历史存储**
  每设备每 **3 分钟** 写入一次历史记录（可配置），避免数据库膨胀。
- **智能报警机制**  
  - 温度 > **45.0°C** → 高温警报  
  - 湿度 > **75.0%** → 高湿警报  
  - 湿度 < **35.0%** → 低湿警报  
  - 报警冷却时间：**3 分钟**（防止重复推送）
- **微信实时通知**
  通过 [Server 酱（SCT）](https://sct.ftqq.com/) 将报警推送到微信。
- **完整 Web API**
  支持设备发现、最新数据查询、历史趋势（按周/季/年筛选）等。

------

## 🚀 快速开始

### 1. 硬件连接

- **ESP8266**（如 NodeMCU）
- **DHT11** 数据引脚接 **GPIO2**（即 D4）

### 2. 配置客户端 (`client.c`)

```c
const char* ssid = "你的WiFi名称";
const char* password = "你的WiFi密码";
const char* mqtt_server = "192.168.1.69"; // 运行 server.c 的服务器 IP
const char* client_id = "location_1";     // 每个设备必须唯一！
```

> 使用 PlatformIO 编译上传（需安装 `PubSubClient` 和 `DHT` 库）。

### 3. 部署 MQTT Broker

在服务器（如树莓派）上安装 Mosquitto：

```bash
sudo apt install mosquitto
sudo systemctl start mosquitto
```

### 4. 编译并运行服务端 (`server.c`)

依赖：`libmosquitto-dev`, `libsqlite3-dev`, `libcurl4-openssl-dev`

```bash
gcc server.c -o server \
  -lmosquitto -lsqlite3 -lcurl \
  -Wall -O2

./server
```

> ⚠️ **重要**：编辑 `server.c`，将 `SERVER_CHAN_KEY` 替换为你自己的 [Server 酱 SCKEY](https://sct.ftqq.com/)：
>
> ```c
> #define SERVER_CHAN_KEY ""
> ```

### 5. 启动 Web API (`web.py`)

依赖：`flask`, `flask-cors`

```bash
pip install flask flask-cors
python3 web.py
```

访问 `http://<服务器IP>:5000` 查看简易前端，或直接调用 API：

- 获取所有设备：`GET /api/devices`
- 获取某设备最新数据：`GET /api/latest/location_1`
- 获取最近一周历史：`GET /api/data/location_1?range=week`

------

## 📡 通信协议

- **MQTT 主题**：`sensor/dht11`
- **Payload 格式**：`client_id,temperature,humidity`
- **示例**：`location_1,23.0,55.0`

------

## 🔒 安全设计

- 设备 ID 严格校验（仅允许 `[a-zA-Z0-9_-]`）
- SQLite 表名使用 `sqlite3_mprintf("%Q")` 自动转义，防止 SQL 注入
- Server 酱密钥硬编码，请勿提交到公开仓库！

------

## 📈 Web API 接口

| 端点                                   | 功能                                          |
| -------------------------------------- | --------------------------------------------- |
| `GET /api/devices`                     | 获取所有已注册设备 ID                         |
| `GET /api/latest_all`                  | 获取所有设备最新状态                          |
| `GET /api/latest/<device_id>`          | 获取指定设备最新数据                          |
| `GET /api/data/<device_id>?range=week` | 获取历史数据（`all`/`year`/`quarter`/`week`） |
| `GET /api/thresholds`                  | 返回当前报警阈值                              |

------

## 🛠️ 可配置参数（`server.c` 中）

```c
#define CACHE_INTERVAL_SEC 180    // 历史数据写入间隔（秒）
#define ALARM_COOLDOWN_SEC 180    // 报警冷却时间（秒）
#define TEMP_THRESHOLD_HIGH 45.0  // 温度上限
#define HUM_THRESHOLD_HIGH 75.0   // 湿度上限
#define HUM_THRESHOLD_LOW 35.0    // 湿度下限
```

------

## 📝 未来改进

-  支持动态阈值配置（通过 API）
-  增加 HTTPS 与用户认证
-  支持更多传感器（DHT22、BME280）

------

## 📄 许可证

MIT License

------

> 💡 **适用场景**：家庭环境监控、机房温湿度预警、农业大棚、实验室等。低成本、易部署、高可靠性。
