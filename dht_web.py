import sqlite3
import re
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from datetime import datetime, timedelta

app = Flask(__name__, template_folder='templates')
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
    return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
