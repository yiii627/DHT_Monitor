import sqlite3
import re
from flask import Flask, jsonify, request, render_template_string
from flask_cors import CORS
from datetime import datetime, timedelta
import math

app = Flask(__name__)
CORS(app)
DB_PATH = "dht_data.db"

def is_valid_device_id(device_id):
    if not isinstance(device_id, str):
        return False
    if len(device_id) == 0 or len(device_id) > 64:
        return False
    return bool(re.fullmatch(r'[a-zA-Z0-9_-]+', device_id))

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_time_range_start(range_type):
    now = datetime.now()
    if range_type == 'week':
        return now - timedelta(weeks=1)
    elif range_type == 'quarter':
        return now - timedelta(days=90)
    elif range_type == 'year':
        return now - timedelta(days=365)
    else:  # 'all'
        return None

@app.route('/api/data/<device_id>', methods=['GET'])
def get_device_data(device_id):
    if not is_valid_device_id(device_id):
        return jsonify({"error": "无效的设备ID"}), 400

    range_type = request.args.get('range', 'all')
    if range_type not in ['all', 'year', 'quarter', 'week']:
        range_type = 'all'

    start_dt = get_time_range_start(range_type)
    start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S') if start_dt else None

    conn = get_db_connection()
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (device_id,)
    ).fetchone()
    if not exists:
        conn.close()
        return jsonify({"error": "设备历史表不存在"}), 404

    query = f"SELECT timestamp, temperature, humidity FROM `{device_id}`"
    params = []
    if start_str:
        query += " WHERE timestamp >= ?"
        params = [start_str]
    query += " ORDER BY timestamp ASC"

    data = conn.execute(query, params).fetchall()
    conn.close()

    result = [
        {
            "timestamp": row['timestamp'],
            "temperature": row['temperature'],
            "humidity": row['humidity']
        } for row in data
    ]
    return jsonify({
        "device_id": device_id,
        "range": range_type,
        "data": result
    })

@app.route('/api/devices', methods=['GET'])
def get_devices():
    conn = get_db_connection()
    rows = conn.execute("SELECT DISTINCT client_id FROM latest_data").fetchall()
    conn.close()
    devices = [row['client_id'] for row in rows if is_valid_device_id(row['client_id'])]
    return jsonify({"devices": devices})

@app.route('/api/latest_all', methods=['GET'])
def get_latest_all():
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT client_id, timestamp, temperature, humidity 
        FROM latest_data 
        ORDER BY client_id
    """).fetchall()
    conn.close()

    all_latest = []
    for row in rows:
        client_id = row['client_id']
        if not is_valid_device_id(client_id):
            continue
        all_latest.append({
            "device_id": client_id,
            "timestamp": row['timestamp'],
            "temperature": row['temperature'],
            "humidity": row['humidity']
        })
    return jsonify({"devices": all_latest})

@app.route('/api/latest/<device_id>', methods=['GET'])
def get_latest_data(device_id):
    if not is_valid_device_id(device_id):
        return jsonify({"error": "无效的设备ID"}), 400

    conn = get_db_connection()
    row = conn.execute(
        "SELECT timestamp, temperature, humidity FROM latest_data WHERE client_id = ?",
        (device_id,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "设备不存在或无数据"}), 404

    return jsonify({
        "device_id": device_id,
        "latest": {
            "timestamp": row['timestamp'],
            "temperature": row['temperature'],
            "humidity": row['humidity']
        }
    })

@app.route('/api/thresholds', methods=['GET'])
def get_thresholds():
    return jsonify({
        "temp_high": 45.0,
        "hum_high": 75.0,
        "hum_low": 35.0
    })

@app.route('/')
def index():
    html_content = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>嵌入式温湿度监控系统</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css" rel="stylesheet">
    <style>
        .device-card { transition: transform 0.2s ease; }
        .device-card:hover { transform: translateY(-3px); box-shadow: 0 6px 12px rgba(0,0,0,0.12); }
        .alarm { border-left: 4px solid #ef4444; background-color: #fef3c7; }
        .loading { color: #94a3b8; font-style: italic; }
        .time-range-btn.active { background-color: #3b82f6; color: white; }
        .chart-container { height: 300px; margin-bottom: 1rem; }
    </style>
</head>
<body class="bg-gray-100">
    <div class="container mx-auto p-4 max-w-6xl">
        <h1 class="text-3xl font-bold text-center my-6 text-gray-800">嵌入式温湿度监控系统</h1>

        <div class="text-center mb-6">
            <button id="refreshAllBtn" class="bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded flex items-center gap-2 mx-auto">
                <i class="fa fa-refresh"></i> 手动刷新
            </button>
        </div>

        <div id="allDevices" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
            <p class="col-span-full text-center loading">加载中...</p>
        </div>

        <!-- 历史图表弹窗 -->
        <div id="chartSection" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 hidden">
            <div class="bg-white rounded-lg shadow-xl w-full max-w-4xl max-h-[90vh] overflow-auto p-6">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-xl font-bold text-gray-800">历史数据趋势图 - <span id="chartDeviceName"></span></h3>
                    <button id="closeChartBtn" class="text-gray-500 hover:text-gray-700 text-2xl">&times;</button>
                </div>

                <!-- 时间范围选择 -->
                <div class="flex flex-wrap gap-2 mb-4">
                    <button class="time-range-btn px-3 py-1 rounded border" data-range="all">全部</button>
                    <button class="time-range-btn px-3 py-1 rounded border" data-range="year">最近一年</button>
                    <button class="time-range-btn px-3 py-1 rounded border" data-range="quarter">最近三个月</button>
                    <button class="time-range-btn px-3 py-1 rounded border" data-range="week">最近一周</button>
                </div>

                <!-- 温度图表 -->
                <div class="chart-container">
                    <h4 class="font-semibold text-red-600 mb-2">温度 (°C)</h4>
                    <canvas id="temperatureChart"></canvas>
                </div>

                <!-- 湿度图表 -->
                <div class="chart-container">
                    <h4 class="font-semibold text-blue-600 mb-2">湿度 (%)</h4>
                    <canvas id="humidityChart"></canvas>
                </div>
            </div>
        </div>
    </div>

    <script>
        const API_BASE = '/api';
        let thresholds = {};
        let tempChart = null;
        let humChart = null;
        let currentData = {};

        window.onload = async () => {
            await fetchThresholds();
            await loadAllDevices();
            document.getElementById('refreshAllBtn').addEventListener('click', loadAllDevices);
            setInterval(loadAllDevices, 3000);

            // 事件委托监听“查看历史”按钮
            document.getElementById('allDevices').addEventListener('click', (e) => {
                if (e.target.classList.contains('view-history')) {
                    const deviceId = e.target.dataset.device;
                    showHistoryChart(deviceId, 'all');
                }
            });
        };

        async function fetchThresholds() {
            try {
                const res = await fetch(`${API_BASE}/thresholds`);
                thresholds = await res.json();
            } catch (err) {
                console.error('获取阈值失败:', err);
            }
        }

        async function loadAllDevices() {
            const container = document.getElementById('allDevices');
            try {
                const res = await fetch(`${API_BASE}/latest_all`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();

                if (!data.devices || data.devices.length === 0) {
                    if (!container.querySelector('.loading')) {
                        container.innerHTML = '<p class="col-span-full text-center text-gray-500">暂无设备数据</p>';
                    }
                    return;
                }

                const newData = {};
                data.devices.forEach(d => newData[d.device_id] = d);

                if (Object.keys(currentData).length === 0) {
                    renderAllDevices(data.devices);
                } else {
                    updateDevicesIncrementally(newData);
                }

                currentData = newData;

            } catch (err) {
                console.error('加载设备数据失败:', err);
                if (container.innerHTML.trim() === '' || container.querySelector('.loading')) {
                    container.innerHTML = '<p class="col-span-full text-center text-red-500">加载失败，请检查后端服务</p>';
                }
            }
        }

        function renderAllDevices(devices) {
            const container = document.getElementById('allDevices');
            container.innerHTML = '';
            devices.forEach(device => {
                container.appendChild(createDeviceCard(device));
            });
        }

        function updateDevicesIncrementally(newData) {
            for (const [id, newDev] of Object.entries(newData)) {
                const oldDev = currentData[id];
                if (!oldDev || 
                    oldDev.timestamp !== newDev.timestamp ||
                    oldDev.temperature !== newDev.temperature ||
                    oldDev.humidity !== newDev.humidity) {
                    
                    const existing = document.querySelector(`[data-device-id="${id}"]`);
                    if (existing) {
                        existing.outerHTML = createDeviceCard(newDev).outerHTML;
                    } else {
                        document.getElementById('allDevices').appendChild(createDeviceCard(newDev));
                    }
                }
            }

            for (const id of Object.keys(currentData)) {
                if (!newData[id]) {
                    const el = document.querySelector(`[data-device-id="${id}"]`);
                    if (el) el.remove();
                }
            }
        }

        function createDeviceCard(device) {
            const { device_id, timestamp, temperature, humidity } = device;
            const isAlarm = checkAlarm(temperature, humidity);
            const tempColor = temperature > thresholds.temp_high ? 'text-red-600' : 'text-red-500';
            const humColor = (humidity > thresholds.hum_high || humidity < thresholds.hum_low) ? 'text-blue-600' : 'text-blue-500';

            const card = document.createElement('div');
            card.setAttribute('data-device-id', device_id);
            card.className = `device-card bg-white p-4 rounded-lg shadow-md ${isAlarm ? 'alarm' : ''}`;
            card.innerHTML = `
                <div class="font-bold text-lg text-gray-800 mb-2">设备 ${device_id}</div>
                <div class="text-sm text-gray-500 mb-2">${timestamp}</div>
                <div class="space-y-1">
                    <div><span class="text-gray-600">温度:</span> <span class="${tempColor} font-semibold">${temperature.toFixed(2)}°C</span></div>
                    <div><span class="text-gray-600">湿度:</span> <span class="${humColor} font-semibold">${humidity.toFixed(2)}%</span></div>
                </div>
                ${isAlarm ? `<div class="mt-2 text-red-600 text-sm"><i class="fa fa-exclamation-triangle"></i> ${getAlarmMessage(temperature, humidity)}</div>` : ''}
                <button class="mt-3 text-sm bg-gray-200 hover:bg-gray-300 px-3 py-1 rounded view-history" data-device="${device_id}">
                    查看历史
                </button>
            `;
            return card;
        }

        function checkAlarm(temp, hum) {
            return temp > thresholds.temp_high || hum > thresholds.hum_high || hum < thresholds.hum_low;
        }

        function getAlarmMessage(temp, hum) {
            if (temp > thresholds.temp_high) return `温度超标！(${temp.toFixed(2)}°C)`;
            if (hum > thresholds.hum_high) return `湿度过高！(${hum.toFixed(2)}%)`;
            if (hum < thresholds.hum_low) return `湿度过低！(${hum.toFixed(2)}%)`;
            return '';
        }

        // ===== 显示分开的温湿度图表 =====
        async function showHistoryChart(deviceId, range = 'all') {
            try {
                const res = await fetch(`${API_BASE}/data/${deviceId}?range=${range}`);
                const result = await res.json();
                const history = result.data;

                if (history.length === 0) {
                    alert('该时间段无数据');
                    return;
                }

                const timestamps = history.map(item => item.timestamp);
                const temps = history.map(item => item.temperature);
                const hums = history.map(item => item.humidity);

                // 计算5等分索引
                const total = timestamps.length;
                const indices = [0];
                if (total > 1) {
                    const step = (total - 1) / 4;
                    for (let i = 1; i < 4; i++) {
                        indices.push(Math.round(i * step));
                    }
                    indices.push(total - 1);
                }
                const xLabels = indices.map(i => timestamps[i]);

                // 构建带空字符串的标签数组（仅5个位置有值）
                const chartLabels = new Array(total).fill('');
                indices.forEach((idx, pos) => {
                    chartLabels[idx] = xLabels[pos];
                });

                // 销毁旧图表
                if (tempChart) tempChart.destroy();
                if (humChart) humChart.destroy();

                // 温度图
                const tempCtx = document.getElementById('temperatureChart').getContext('2d');
                tempChart = new Chart(tempCtx, {
                    type: 'line',
                    data: {
                        labels: chartLabels,
                        datasets: [{
                            label: '温度 (°C)',
                            data: temps,
                            borderColor: 'rgb(239, 68, 68)',
                            backgroundColor: 'rgba(239, 68, 68, 0.1)',
                            tension: 0.3,
                            fill: true
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {
                            y: {
                                min: -30,
                                max: 120,
                                title: { display: true, text: '温度 (°C)' },
                                ticks: { stepSize: 10 }
                            },
                            x: {
                                title: { display: true, text: '采集时间' },
                                ticks: {
                                    autoSkip: false,
                                    maxRotation: 0,
                                    callback: function(value) {
                                        return this.getLabelForValue(value) || '';
                                    }
                                }
                            }
                        }
                    }
                });

                // 湿度图
                const humCtx = document.getElementById('humidityChart').getContext('2d');
                humChart = new Chart(humCtx, {
                    type: 'line',
                    data: {
                        labels: chartLabels,
                        datasets: [{
                            label: '湿度 (%)',
                            data: hums,
                            borderColor: 'rgb(59, 130, 246)',
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            tension: 0.3,
                            fill: true
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {
                            y: {
                                min: 0,
                                max: 100,
                                title: { display: true, text: '湿度 (%)' },
                                ticks: { stepSize: 10 }
                            },
                            x: {
                                title: { display: true, text: '采集时间' },
                                ticks: {
                                    autoSkip: false,
                                    maxRotation: 0,
                                    callback: function(value) {
                                        return this.getLabelForValue(value) || '';
                                    }
                                }
                            }
                        }
                    }
                });

                document.getElementById('chartDeviceName').textContent = deviceId;
                document.getElementById('chartSection').classList.remove('hidden');

                // 关闭弹窗
                document.getElementById('closeChartBtn').onclick = () => {
                    document.getElementById('chartSection').classList.add('hidden');
                    if (tempChart) tempChart.destroy();
                    if (humChart) humChart.destroy();
                    tempChart = null;
                    humChart = null;
                };

                // 时间范围按钮
                document.querySelectorAll('.time-range-btn').forEach(btn => {
                    btn.classList.remove('active');
                    if (btn.dataset.range === range) {
                        btn.classList.add('active');
                    }
                    btn.onclick = () => {
                        showHistoryChart(deviceId, btn.dataset.range);
                    };
                });

            } catch (err) {
                console.error('加载历史数据失败:', err);
            }
        }
    </script>
</body>
</html>
    '''
    return render_template_string(html_content)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
