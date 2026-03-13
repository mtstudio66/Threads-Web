#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import threading
import requests
from datetime import datetime
import mysql.connector
from flask import Flask, request, jsonify, send_file

# --- 1. 設定日誌 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

# --- 2. 初始化 Flask 網頁伺服器 ---
app = Flask(__name__)

# --- 3. 常數與環境變數 ---
THREADS_API_URL = "https://graph.threads.net/v1.0/me/threads"
CHECK_INTERVAL_SECONDS = 60

DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_PORT = int(os.getenv("MYSQL_PORT", 3306))
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
DB_DATABASE = os.getenv("MYSQL_DATABASE", "zeabur")

def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_DATABASE
    )

def init_db():
    """自動建立所有 SaaS 系統需要的資料表與預設資料"""
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        # 建立帳號表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS threads_accounts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                accountName VARCHAR(128) NOT NULL,
                accessToken TEXT NOT NULL,
                isActive BOOLEAN DEFAULT TRUE,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 建立排程表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                userId INT DEFAULT 1,
                accountId INT NOT NULL,
                content TEXT NOT NULL,
                imageUrls JSON,
                scheduledAt TIMESTAMP NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                postId INT,
                errorMessage TEXT,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 建立發文歷史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                userId INT DEFAULT 1,
                accountId INT NOT NULL,
                content TEXT NOT NULL,
                threadsPostId VARCHAR(128),
                status VARCHAR(20),
                errorMessage TEXT,
                publishedAt TIMESTAMP,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 建立熱門文案表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trending_templates (
                id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(256) NOT NULL,
                content TEXT NOT NULL,
                category VARCHAR(64) NOT NULL,
                usageCount INT DEFAULT 0,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 預設測試帳號
        cursor.execute("SELECT COUNT(*) FROM threads_accounts")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO threads_accounts (accountName, accessToken) VALUES ('@預設帳號', 'mock_token')")
            
        # 預設 12 個熱門文案模板
        cursor.execute("SELECT COUNT(*) FROM trending_templates")
        if cursor.fetchone()[0] == 0:
            templates = [
                ("早安勵志", "早安！今天也是充滿希望的一天，持續往目標前進吧！✨", "勵志"),
                ("晚安語錄", "辛苦了一天，好好休息，明天我們繼續閃耀。🌙", "勵志"),
                ("美食分享", "今天解鎖了這家超讚的餐廳！這個味道真的讓人難以忘懷 🤤🍲", "美食"),
                ("咖啡日常", "用一杯拿鐵開啟美好的一天 ☕️ 大家的早晨都需要一點咖啡因！", "美食"),
                ("旅行風景", "暫時逃離城市的喧囂，這裡的風景真的太美了 ⛰️✈️", "旅行"),
                ("週末出遊", "週末就是要出門走走！大家這個週末有什麼計畫呢？🚗", "旅行"),
                ("AI 趨勢", "AI 發展真的太快了，未來的科技趨勢讓人期待又敬畏 🤖🚀", "科技"),
                ("程式日常", "解完了一個大 Bug！身為工程師的小確幸 💻🎉", "科技"),
                ("健身打卡", "汗水不會背叛你！今天的訓練順利完成 💪🏋️‍♀️", "生活"),
                ("閱讀心得", "最近讀完這本書，收穫滿滿，強烈推薦給大家！📚💡", "生活"),
                ("搞笑廢文", "我不是在上班，我是在為我的退休生活籌備資金 💸😂", "搞笑"),
                ("寵物日常", "看看我家這個小可愛，今天又在搗蛋了 🐶🐱❤️", "搞笑")
            ]
            cursor.executemany("INSERT INTO trending_templates (title, content, category) VALUES (%s, %s, %s)", templates)
            
        db.commit()
        logger.info("✅ 資料庫結構與預設資料初始化完成！")
        
    except Exception as e:
        logger.error(f"❌ 初始化資料庫失敗: {e}")
    finally:
        if cursor: cursor.close()
        if db and db.is_connected(): db.close()

# ==========================================
# 網頁與 API 路由設定
# ==========================================

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/api/dashboard', methods=['GET'])
def get_dashboard_data():
    """取得所有前端需要的資料：帳號、排程、歷史、文案"""
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT id, accountName FROM threads_accounts WHERE isActive = 1")
        accounts = cursor.fetchall()
        
        cursor.execute("SELECT id, title, content, category, usageCount FROM trending_templates ORDER BY usageCount DESC")
        templates = cursor.fetchall()
        
        cursor.execute("""
            SELECT sp.id, sp.content, sp.scheduledAt, sp.status, ta.accountName 
            FROM scheduled_posts sp
            JOIN threads_accounts ta ON sp.accountId = ta.id
            ORDER BY sp.scheduledAt DESC LIMIT 20
        """)
        schedules = cursor.fetchall()
        
        cursor.execute("""
            SELECT p.id, p.content, p.status, p.publishedAt, p.errorMessage, ta.accountName 
            FROM posts p
            JOIN threads_accounts ta ON p.accountId = ta.id
            ORDER BY p.publishedAt DESC LIMIT 20
        """)
        history = cursor.fetchall()
        
        return jsonify({
            "accounts": accounts,
            "templates": templates,
            "schedules": schedules,
            "history": history
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db and db.is_connected(): db.close()

@app.route('/api/schedule', methods=['POST'])
def save_schedule():
    """新增排程或立即發文"""
    data = request.json
    content = data.get('content')
    scheduled_at = data.get('scheduledAt')
    account_id = data.get('accountId', 1)
    
    if not content or not scheduled_at:
        return jsonify({"message": "資料不完整"}), 400
        
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        query = "INSERT INTO scheduled_posts (accountId, content, scheduledAt, status) VALUES (%s, %s, %s, 'pending')"
        cursor.execute(query, (account_id, content, scheduled_at))
        db.commit()
        return jsonify({"message": "✅ 排程設定成功！"}), 200
    except Exception as e:
        return jsonify({"message": f"❌ 儲存失敗: {e}"}), 500
    finally:
        if cursor: cursor.close()
        if db and db.is_connected(): db.close()

@app.route('/api/schedule/<int:post_id>', methods=['DELETE'])
def cancel_schedule(post_id):
    """取消排程"""
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("UPDATE scheduled_posts SET status = 'cancelled' WHERE id = %s", (post_id,))
        db.commit()
        return jsonify({"message": "排程已取消"}), 200
    except Exception as e:
        return jsonify({"message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db and db.is_connected(): db.close()

@app.route('/api/generate-ai', methods=['POST'])
def generate_ai():
    """模擬 AI 文案生成功能 (實際可串接 OpenAI)"""
    topic = request.json.get('topic', '隨機主題')
    mock_ai_text = f"【AI生成】這是一段關於「{topic}」的優質 Threads 文案！快來和我一起討論吧！✨ #AI寫作"
    time.sleep(1) # 模擬 AI 思考時間
    return jsonify({"content": mock_ai_text}), 200

# ==========================================
# 背景發文機器人
# ==========================================

def fetch_due_scheduled_posts(cursor):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        SELECT sp.id, sp.accountId, sp.content, ta.accessToken 
        FROM scheduled_posts sp
        JOIN threads_accounts ta ON sp.accountId = ta.id
        WHERE sp.status = 'pending' AND sp.scheduledAt <= %s AND ta.isActive = 1
    """, (now,))
    return cursor.fetchall()

def post_to_threads(content, access_token):
    # 此處保留 Threads API 呼叫邏輯。若 token 無效會返回 False 並記錄。
    if access_token == 'mock_token':
        return False, "測試帳號無法真實發文，請至資料庫更換真實 Token"
    
    try:
        create_payload = {"media_type": "TEXT", "text": content, "access_token": access_token}
        create_response = requests.post(THREADS_API_URL, data=create_payload, timeout=15).json()
        if 'id' not in create_response: return False, str(create_response)
        
        publish_payload = {"access_token": access_token}
        publish_url = f"https://graph.threads.net/v1.0/{create_response['id']}/publish"
        publish_response = requests.post(publish_url, data=publish_payload, timeout=15).json()
        if 'id' in publish_response: return True, publish_response['id']
        return False, str(publish_response)
    except Exception as e:
        return False, str(e)

def process_posts():
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        posts = fetch_due_scheduled_posts(cursor)
        
        for post in posts:
            post_id = post['id']
            cursor.execute("UPDATE scheduled_posts SET status = 'processing' WHERE id = %s", (post_id,))
            db.commit()
            
            success, result = post_to_threads(post['content'], post['accessToken'])
            
            if success:
                logger.info(f"發文成功！Post ID: {result}")
                cursor.execute("""
                    INSERT INTO posts (accountId, content, threadsPostId, status, publishedAt)
                    VALUES (%s, %s, %s, 'published', NOW())
                """, (post['accountId'], post['content'], result))
                cursor.execute("UPDATE scheduled_posts SET status = 'published', postId = %s WHERE id = %s", (cursor.lastrowid, post_id))
            else:
                logger.error(f"發文失敗: {result}")
                # 紀錄到歷史表，標記為失敗
                cursor.execute("""
                    INSERT INTO posts (accountId, content, status, errorMessage, publishedAt)
                    VALUES (%s, %s, 'failed', %s, NOW())
                """, (post['accountId'], post['content'], result))
                cursor.execute("UPDATE scheduled_posts SET status = 'failed', errorMessage = %s WHERE id = %s", (result, post_id))
            db.commit()
    except Exception as e:
        logger.error(f"背景執行錯誤: {e}")
    finally:
        if cursor: cursor.close()
        if db and db.is_connected(): db.close()

def background_worker():
    while True:
        try: process_posts()
        except Exception as e: logger.critical(f"背景保護: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))