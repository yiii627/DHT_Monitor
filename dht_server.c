#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sqlite3.h>
#include <mosquitto.h>
#include <time.h>
#include <curl/curl.h> // 需要引入 libcurl 库进行 HTTP 请求

#define DB_FILE "dht_data.db"
#define MQTT_TOPIC "sensor/dht11"    // 所有设备都发到这个主题
#define MQTT_HOST "127.0.0.1"
#define MQTT_PORT 1883

// --- 报警与缓存设置 ---
#define CACHE_INTERVAL_SEC 180       // 3分钟 = 180秒 (数据写入冷却)
#define ALARM_COOLDOWN_SEC 180       // 3分钟 = 180秒 (报警推送冷却)
#define TEMP_THRESHOLD_HIGH 45.0     // 温度上限报警阈值
#define HUM_THRESHOLD_HIGH 75.0      // 湿度上限报警阈值
#define HUM_THRESHOLD_LOW 35.0       // 湿度下限报警阈值

// --- Server酱 配置 ---
// !!! 请将 YOUR_SERVER_CHAN_KEY 替换为你自己的 Key !!!
#define SERVER_CHAN_KEY ""
#define SERVER_CHAN_API_BASE "https://sctapi.ftqq.com/"

sqlite3 *db;

// 缓存结构：记录每个设备最后写入时间和最后报警时间
typedef struct {
    char client_id[32];
    time_t last_write;
    time_t last_alarm; // 新增：记录最后一次报警时间
} device_cache_t;

#define MAX_DEVICES 20
device_cache_t cache[MAX_DEVICES];
int cache_count = 0;

// ******************************************************
// ** HTTP 通知功能 (使用 libcurl) **
// ******************************************************

// 辅助函数：URL 编码
static char* url_encode(CURL *curl, const char *str) {
    if (!str) return NULL;
    return curl_easy_escape(curl, str, strlen(str));
}

// 发送 Server酱 通知（使用 POST 方法）
void send_server_chan_notification(const char* client_id, const char* title, const char* message) {
    CURL *curl;
    CURLcode res;
    char url[256];
    
    // 检查 Key 是否为默认占位符
    if (strcmp(SERVER_CHAN_KEY, "YOUR_SERVER_CHAN_KEY") == 0) {
        fprintf(stderr, "⚠️ Server Chan Key is placeholder. Notification skipped.\n");
        return;
    }
    curl_global_init(CURL_GLOBAL_DEFAULT);
    curl = curl_easy_init();
    
    if (curl) {
        snprintf(url, sizeof(url), "%s%s.send", SERVER_CHAN_API_BASE, SERVER_CHAN_KEY);
        // 使用 curl_mime 构建 multipart/form-data
        curl_mime *mime = curl_mime_init(curl);
        curl_mimepart *part;
        
        part = curl_mime_addpart(mime);
        curl_mime_name(part, "title");
        curl_mime_data(part, title, CURL_ZERO_TERMINATED);
        
        part = curl_mime_addpart(mime);
        curl_mime_name(part, "desp");
        curl_mime_data(part, message, CURL_ZERO_TERMINATED);
        
        curl_easy_setopt(curl, CURLOPT_URL, url);
        curl_easy_setopt(curl, CURLOPT_MIMEPOST, mime);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
        
        printf("🔔 Sending alarm notification via POST (curl_mime)...\n");
        res = curl_easy_perform(curl);
        
        if (res != CURLE_OK) {
            fprintf(stderr, "❌ Server Chan push failed: %s\n", curl_easy_strerror(res));
        } else {
            printf("✅ Server Chan push successful.\n");
        }
        
        curl_mime_free(mime);
        curl_easy_cleanup(curl);
    } else {
        fprintf(stderr, "❌ Failed to initialize libcurl.\n");
    }
    curl_global_cleanup();
}

// ******************************************************
// ** SQLite & Cache 逻辑 **
// ******************************************************

// 创建设备专属历史表
int create_device_table(const char* client_id) {
    char sql[256];
    char *table_name_safe = sqlite3_mprintf("`%s`", client_id);
    if (!table_name_safe) return -1;
    
    snprintf(sql, sizeof(sql),
        "CREATE TABLE IF NOT EXISTS %s ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "timestamp TEXT NOT NULL,"
        "temperature REAL NOT NULL,"
        "humidity REAL NOT NULL);",
        table_name_safe);
    
    char *err_msg = NULL;
    int rc = sqlite3_exec(db, sql, NULL, NULL, &err_msg);
    if (rc != SQLITE_OK) {
        fprintf(stderr, "❌ Failed to create table '%s': %s\n", client_id, err_msg);
        sqlite3_free(err_msg);
        sqlite3_free(table_name_safe);
        return -1;
    }
    sqlite3_free(table_name_safe);
    return 0;
}

// 获取设备在缓存中的索引，若不存在则添加
int get_or_add_device(const char* client_id) {
    for (int i = 0; i < cache_count; i++) {
        if (strcmp(cache[i].client_id, client_id) == 0) {
            return i;
        }
    }
    if (cache_count >= MAX_DEVICES) {
        printf("⚠️ Device cache full! Ignoring new device: %s\n", client_id);
        return -1;
    }
    strncpy(cache[cache_count].client_id, client_id, sizeof(cache[0].client_id) - 1);
    cache[cache_count].client_id[sizeof(cache[0].client_id) - 1] = '\0';
    cache[cache_count].last_write = 0;
    cache[cache_count].last_alarm = 0;
    return cache_count++;
}

// ******************************************************
// ** MQTT 消息回调 **
// ******************************************************

void on_message(struct mosquitto *mosq, void *userdata, const struct mosquitto_message *message) {
    if (!message || !message->payload || message->payloadlen <= 0) {
        printf("⚠️ Empty MQTT message\n");
        return;
    }
    
    char raw_payload[128];
    int len = (message->payloadlen < (int)sizeof(raw_payload) - 1) ? message->payloadlen : (int)sizeof(raw_payload) - 1;
    memcpy(raw_payload, message->payload, len);
    raw_payload[len] = '\0';
    
    printf("\n📥 Raw payload: '%s'\n", raw_payload);
    
    char client_id[32];
    float temp, hum;
    if (sscanf(raw_payload, "%31[^,],%f,%f", client_id, &temp, &hum) != 3) {
        printf("❌ Invalid format. Expected: client_id,temp,hum\n");
        return;
    }

    // >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
    // ✅ 立即更新 latest_data 表（无冷却，实时覆盖）
    // 使用 sqlite3_mprintf("%Q") 安全转义 client_id
    char *client_id_escaped = sqlite3_mprintf("%Q", client_id);
    if (!client_id_escaped) {
        fprintf(stderr, "❌ Failed to escape client_id for latest_data\n");
        return;
    }
    
    char *sql_latest = sqlite3_mprintf(
        "INSERT OR REPLACE INTO latest_data (client_id, timestamp, temperature, humidity) "
        "VALUES (%s, datetime('now', 'localtime'), %.1f, %.1f);",
        client_id_escaped, temp, hum
    );
    sqlite3_free(client_id_escaped);
    
    if (sql_latest) {
        char *err = NULL;
        int rc_latest = sqlite3_exec(db, sql_latest, NULL, NULL, &err);
        if (rc_latest != SQLITE_OK) {
            fprintf(stderr, "❌ Failed to update latest_data: %s\n", err);
            sqlite3_free(err);
        }
        sqlite3_free(sql_latest);
        printf("⚡ Updated latest_data for '%s': %.1f°C, %.1f%%\n", client_id, temp, hum);
    } else {
        fprintf(stderr, "❌ Failed to format SQL for latest_data\n");
    }
    // <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

    // 检查设备缓存（用于历史写入和报警冷却）
    int idx = get_or_add_device(client_id);
    if (idx < 0) return;
    
    time_t now = time(NULL);
    int wrote_to_db = 0;

    // --- 1. 历史数据写入逻辑（3分钟冷却） ---
    if (now - cache[idx].last_write >= CACHE_INTERVAL_SEC) {
        if (create_device_table(client_id) != 0) {
            return;
        }
        
        char *table_name_safe = sqlite3_mprintf("`%s`", client_id);
        if (!table_name_safe) return;
        
        char sql_hist[256];
        snprintf(sql_hist, sizeof(sql_hist),
            "INSERT INTO %s (timestamp, temperature, humidity) "
            "VALUES (datetime('now', 'localtime'), %.1f, %.1f);",
            table_name_safe, temp, hum);
        sqlite3_free(table_name_safe);
        
        char *err_msg = NULL;
        int rc = sqlite3_exec(db, sql_hist, NULL, NULL, &err_msg);
        if (rc != SQLITE_OK) {
            fprintf(stderr, "❌ SQL error for '%s': %s\n", client_id, err_msg);
            sqlite3_free(err_msg);
        } else {
            cache[idx].last_write = now;
            wrote_to_db = 1;
            printf("✅ Saved to history table '%s': %.1f°C, %.1f%%\n", client_id, temp, hum);
        }
    } else {
        printf("🕒 Skipping DB write for '%s' (last written %ld sec ago)\n",
               client_id, (long)(now - cache[idx].last_write));
    }

    // --- 2. 报警推送逻辑（3分钟冷却） ---
    if (now - cache[idx].last_alarm < ALARM_COOLDOWN_SEC) {
        printf("💤 Alarm cooldown active for '%s'. Skipping alarm check.\n", client_id);
        return; 
    }

    char alarm_title[64];
    char alarm_message[256];
    int alarm_triggered = 0;
    
    if (temp > TEMP_THRESHOLD_HIGH) {
        snprintf(alarm_title, sizeof(alarm_title), "[紧急] 高温警报！(%s)", client_id);
        snprintf(alarm_message, sizeof(alarm_message), 
                 "区域: %s\n温度: %.1f°C (超过阈值 %.1f°C)\n湿度: %.1f%%", 
                 client_id, temp, TEMP_THRESHOLD_HIGH, hum);
        alarm_triggered = 1;
    } else if (hum > HUM_THRESHOLD_HIGH) {
        snprintf(alarm_title, sizeof(alarm_title), "[警报] 高湿警报！(%s)", client_id);
        snprintf(alarm_message, sizeof(alarm_message), 
                 "区域: %s\n温度: %.1f°C\n湿度: %.1f%% (超过阈值 %.1f%%)", 
                 client_id, temp, hum, HUM_THRESHOLD_HIGH);
        alarm_triggered = 1;
    } else if (hum < HUM_THRESHOLD_LOW) {
        snprintf(alarm_title, sizeof(alarm_title), "[警报] 低湿警报！(%s)", client_id);
        snprintf(alarm_message, sizeof(alarm_message), 
                 "区域: %s\n温度: %.1f°C\n湿度: %.1f%% (低于阈值 %.1f%%)", 
                 client_id, temp, hum, HUM_THRESHOLD_LOW);
        alarm_triggered = 1;
    }

    if (alarm_triggered) {
        printf("🚨 ALARM TRIGGERED for %s: Temp=%.1f, Hum=%.1f\n", client_id, temp, hum);
        send_server_chan_notification(client_id, alarm_title, alarm_message);
        cache[idx].last_alarm = now;
    }
}

// ******************************************************
// ** 主函数 **
// ******************************************************

int main() {
    // 初始化 SQLite
    if (sqlite3_open(DB_FILE, &db) != SQLITE_OK) {
        fprintf(stderr, "❌ Cannot open database: %s\n", sqlite3_errmsg(db));
        return 1;
    }
    printf("✅ Connected to database: %s\n", DB_FILE);

    // 创建 latest_data 全局缓存表
    char *err_msg = NULL;
    const char *create_latest_sql =
        "CREATE TABLE IF NOT EXISTS latest_data ("
        "client_id TEXT PRIMARY KEY,"
        "timestamp TEXT NOT NULL,"
        "temperature REAL NOT NULL,"
        "humidity REAL NOT NULL);";
    int rc = sqlite3_exec(db, create_latest_sql, NULL, NULL, &err_msg);
    if (rc != SQLITE_OK) {
        fprintf(stderr, "❌ Failed to create latest_data table: %s\n", err_msg);
        sqlite3_free(err_msg);
        sqlite3_close(db);
        return 1;
    }
    printf("✅ Created/verified table: latest_data\n");

    // 初始化 Mosquitto
    mosquitto_lib_init();
    struct mosquitto *mosq = mosquitto_new("multi_alarm_subscriber", true, NULL);
    if (!mosq) {
        fprintf(stderr, "❌ Cannot create Mosquitto client.\n");
        sqlite3_close(db);
        return 1;
    }
    mosquitto_message_callback_set(mosq, on_message);
    
    rc = mosquitto_connect(mosq, MQTT_HOST, MQTT_PORT, 60);
    if (rc != MOSQ_ERR_SUCCESS) {
        fprintf(stderr, "❌ MQTT connect failed (error %d)\n", rc);
        mosquitto_destroy(mosq);
        sqlite3_close(db);
        return 1;
    }
    mosquitto_subscribe(mosq, NULL, MQTT_TOPIC, 0);
    
    printf("📡 Subscribed to: %s\n", MQTT_TOPIC);
    printf("💡 Payload format: client_id,temp,hum (e.g., esp8266_1,23.0,55.0)\n");
    printf("⏱ Data write cooldown: %d seconds.\n", CACHE_INTERVAL_SEC);
    printf("🚨 Alarm cooldown: %d seconds.\n", ALARM_COOLDOWN_SEC);
    printf("🌡 Alarm Thresholds: T > %.1f°C, H > %.1f%%, H < %.1f%%\n", 
           TEMP_THRESHOLD_HIGH, HUM_THRESHOLD_HIGH, HUM_THRESHOLD_LOW);
    
    if (strcmp(SERVER_CHAN_KEY, "YOUR_SERVER_CHAN_KEY") == 0) {
         printf("\n🚨🚨🚨 WARNING: Server Chan Key is placeholder. Alarms will NOT be sent. 🚨🚨🚨\n");
    }

    while (1) {
        mosquitto_loop(mosq, -1, 1);
    }

    // 清理资源（理论上不会执行到）
    mosquitto_destroy(mosq);
    mosquitto_lib_cleanup();
    sqlite3_close(db);
    return 0;
}
