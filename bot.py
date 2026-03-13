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

# --- 設定日誌 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- 常數與環境變數 ---
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
    """建立 SaaS 系統完整資料庫結構"""
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        # 1. 帳號表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS threads_accounts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                accountName VARCHAR(128) NOT NULL,
                accessToken TEXT NOT NULL,
                isActive BOOLEAN DEFAULT TRUE,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 2. 排程表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                accountId INT NOT NULL,
                content TEXT NOT NULL,
                imageUrl TEXT,
                scheduledAt TIMESTAMP NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                postId INT,
                errorMessage TEXT,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 3. 歷史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                accountId INT NOT NULL,
                content TEXT NOT NULL,
                imageUrl TEXT,
                threadsPostId VARCHAR(128),
                status VARCHAR(20),
                errorMessage TEXT,
                publishedAt TIMESTAMP,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 4. 熱門文案表
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

        # 5. 計費用量表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_usage (
                id INT AUTO_INCREMENT PRIMARY KEY,
                month VARCHAR(7) NOT NULL,
                postCount INT DEFAULT 0,
                totalCost DECIMAL(10,4) DEFAULT 0,
                updatedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        
        # --- 預設資料寫入 (防呆修復機制) ---
        # 如果文案庫是空的，強制寫入 12 筆資料
        cursor.execute("SELECT COUNT(*) FROM trending_templates")
        if cursor.fetchone()[0] == 0:
            templates = [
                ("早安勵志", "早安！今天也是充滿希望的一天，持續往目標前進吧！✨", "勵志"),
                ("晚安語錄", "辛苦了一天，好好休息，明天我們繼續閃耀。🌙", "勵志"),
                ("突破自我", "不要害怕失敗，每一次跌倒都是為了跳得更高！💪", "勵志"),
                ("美食分享", "今天解鎖了這家超讚的餐廳！這個味道真的讓人難以忘懷 🤤🍲", "美食"),
                ("咖啡日常", "用一杯拿鐵開啟美好的一天 ☕️ 大家的早晨都需要一點咖啡因！", "美食"),
                ("深夜食堂", "宵夜時間到！這碗泡麵加蛋簡直是人間美味 🍜🔥", "美食"),
                ("旅行風景", "暫時逃離城市的喧囂，這裡的風景真的太美了 ⛰️✈️", "旅行"),
                ("週末出遊", "週末就是要出門走走！大家這個週末有什麼計畫呢？🚗", "旅行"),
                ("說走就走", "機票買了就出發！有時候旅行就是需要一股衝動 🎫🌍", "旅行"),
                ("AI 趨勢", "AI 發展真的太快了，未來的科技趨勢讓人期待又敬畏 🤖🚀", "科技"),
                ("程式日常", "解完了一個大 Bug！身為工程師的小確幸 💻🎉", "科技"),
                ("搞笑廢文", "我不是在上班，我是在為我的退休生活籌備資金 💸😂", "搞笑")
            ]
            cursor.executemany("INSERT INTO trending_templates (title, content, category) VALUES (%s, %s, %s)", templates)
            logger.info("✅ 已自動載入 12 筆熱門文案模板！")
            
        db.commit()
    except Exception as e:
        logger.error(f"❌ 初始化資料庫失敗: {e}")
    finally:
        if cursor: cursor.close()
        if db and db.is_connected(): db.close()

# ==========================================
# API 路由設定 (Frontend UI 專用)
# ==========================================

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/api/dashboard', methods=['GET'])
def get_dashboard_data():
    """取得所有前端需要的資料"""
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT id, accountName, isActive FROM threads_accounts")
        accounts = cursor.fetchall()
        
        cursor.execute("SELECT id, title, content, category, usageCount FROM trending_templates ORDER BY usageCount DESC")
        templates = cursor.fetchall()
        
        cursor.execute("""
            SELECT sp.id, sp.content, sp.imageUrl, sp.scheduledAt, sp.status, ta.accountName 
            FROM scheduled_posts sp LEFT JOIN threads_accounts ta ON sp.accountId = ta.id
            WHERE sp.status = 'pending' ORDER BY sp.scheduledAt ASC LIMIT 50
        """)
        schedules = cursor.fetchall()
        
        cursor.execute("""
            SELECT p.id, p.content, p.status, p.publishedAt, p.errorMessage, ta.accountName 
            FROM posts p LEFT JOIN threads_accounts ta ON p.accountId = ta.id
            ORDER BY p.publishedAt DESC LIMIT 50
        """)
        history = cursor.fetchall()

        # 簡單統計當月用量
        current_month = datetime.now().strftime('%Y-%m')
        cursor.execute("SELECT postCount FROM user_usage WHERE month = %s", (current_month,))
        usage_row = cursor.fetchone()
        usage_count = usage_row['postCount'] if usage_row else 0
        
        return jsonify({
            "accounts": accounts,
            "templates": templates,
            "schedules": schedules,
            "history": history,
            "usage": usage_count
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/accounts', methods=['POST'])
def add_account():
    """新增 Threads 帳號"""
    data = request.json
    name = data.get('accountName')
    token = data.get('accessToken')
    if not name or not token: return jsonify({"message": "資料不完整"}), 400
    
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("INSERT INTO threads_accounts (accountName, accessToken) VALUES (%s, %s)", (name, token))
        db.commit()
        return jsonify({"message": "帳號新增成功"}), 200
    finally:
        if db: db.close()

@app.route('/api/accounts/<int:acc_id>', methods=['DELETE'])
def delete_account(acc_id):
    """刪除帳號"""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM threads_accounts WHERE id = %s", (acc_id,))
        db.commit()
        return jsonify({"message": "帳號已刪除"}), 200
    finally:
        if db: db.close()

@app.route('/api/schedule', methods=['POST'])
def save_schedule():
    """新增排程發文"""
    data = request.json
    try:
        db = get_db_connection()
        cursor = db.cursor()
        query = "INSERT INTO scheduled_posts (accountId, content, imageUrl, scheduledAt, status) VALUES (%s, %s, %s, %s, 'pending')"
        cursor.execute(query, (data.get('accountId'), data.get('content'), data.get('imageUrl'), data.get('scheduledAt')))
        db.commit()
        return jsonify({"message": "排程設定成功！"}), 200
    finally:
        if db: db.close()

@app.route('/api/schedule/<int:post_id>', methods=['DELETE'])
def cancel_schedule(post_id):
    """取消排程"""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("UPDATE scheduled_posts SET status = 'cancelled' WHERE id = %s", (post_id,))
        db.commit()
        return jsonify({"message": "排程已取消"}), 200
    finally:
        if db: db.close()

@app.route('/api/upload', methods=['POST'])
def mock_s3_upload():
    """模擬 S3 圖片上傳 (回傳佔位圖片 URL)"""
    time.sleep(1.5) # 模擬上傳延遲
    return jsonify({"url": "https://images.unsplash.com/photo-1707343843437-caacff5cfa74?q=80&w=600&auto=format&fit=crop"}), 200

@app.route('/api/generate-ai', methods=['POST'])
def generate_ai():
    topic = request.json.get('topic', '隨機主題')
    mock_ai_text = f"【AI智能生成】這是一段關於「{topic}」的優質 Threads 文案！不僅能吸引眼球，還能增加互動率喔！✨ #AI寫作"
    time.sleep(1.5)
    return jsonify({"content": mock_ai_text}), 200

# ==========================================
# 背景發文機器人
# ==========================================

def post_to_threads(content, image_url, access_token):
    if 'mock' in access_token.lower() or '請之後' in access_token:
        return False, "無效的測試 Token，請至「帳號管理」綁定真實 Token"
    
    try:
        # 判斷是純文字還是圖片發文
        media_type = "IMAGE" if image_url else "TEXT"
        create_payload = {"media_type": media_type, "text": content, "access_token": access_token}
        if image_url: create_payload["image_url"] = image_url
            
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
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute("""
            SELECT sp.id, sp.accountId, sp.content, sp.imageUrl, ta.accessToken 
            FROM scheduled_posts sp
            JOIN threads_accounts ta ON sp.accountId = ta.id
            WHERE sp.status = 'pending' AND sp.scheduledAt <= %s AND ta.isActive = 1
        """, (now,))
        posts = cursor.fetchall()
        
        for post in posts:
            post_id = post['id']
            cursor.execute("UPDATE scheduled_posts SET status = 'processing' WHERE id = %s", (post_id,))
            db.commit()
            
            success, result = post_to_threads(post['content'], post['imageUrl'], post['accessToken'])
            
            if success:
                logger.info(f"發文成功！Post ID: {result}")
                cursor.execute("""
                    INSERT INTO posts (accountId, content, imageUrl, threadsPostId, status, publishedAt)
                    VALUES (%s, %s, %s, %s, 'published', NOW())
                """, (post['accountId'], post['content'], post['imageUrl'], result))
                
                # 更新計費用量表
                current_month = datetime.now().strftime('%Y-%m')
                cursor.execute("""
                    INSERT INTO user_usage (month, postCount) VALUES (%s, 1)
                    ON DUPLICATE KEY UPDATE postCount = postCount + 1, totalCost = totalCost + 0.5
                """, (current_month,))
                
                cursor.execute("UPDATE scheduled_posts SET status = 'published', postId = %s WHERE id = %s", (cursor.lastrowid, post_id))
            else:
                logger.error(f"發文失敗: {result}")
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
