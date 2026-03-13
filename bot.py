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

app = Flask(__name__)

# --- 2. 常數與環境變數 ---
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
    """建立 SaaS 系統完整資料庫結構，並強制修復缺失資料"""
    db = None
    cursor = None
    try:
        logger.info("開始初始化資料庫...")
        db = get_db_connection()
        cursor = db.cursor()
        
        # 建立帳號表、排程表、歷史表 (保留既有結構)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS threads_accounts (
                id INT AUTO_INCREMENT PRIMARY KEY, accountName VARCHAR(128) NOT NULL,
                accessToken TEXT NOT NULL, isActive BOOLEAN DEFAULT TRUE, createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INT AUTO_INCREMENT PRIMARY KEY, accountId INT NOT NULL, content TEXT NOT NULL,
                imageUrl TEXT, scheduledAt TIMESTAMP NOT NULL, status VARCHAR(20) DEFAULT 'pending',
                postId INT, errorMessage TEXT, createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INT AUTO_INCREMENT PRIMARY KEY, accountId INT NOT NULL, content TEXT NOT NULL,
                imageUrl TEXT, threadsPostId VARCHAR(128), status VARCHAR(20),
                errorMessage TEXT, publishedAt TIMESTAMP, createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 建立文案表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trending_templates (
                id INT AUTO_INCREMENT PRIMARY KEY, title VARCHAR(256) NOT NULL UNIQUE, content TEXT NOT NULL,
                category VARCHAR(64) NOT NULL, usageCount INT DEFAULT 0, createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 為已存在的表添加唯一索引（如果尚未存在）
        try:
            cursor.execute("ALTER TABLE trending_templates ADD UNIQUE INDEX idx_title (title)")
        except mysql.connector.errors.ProgrammingError:
            pass  # 索引已存在，忽略錯誤

        # 【新增】使用者表與計費設定表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(64) NOT NULL,
                role ENUM('user', 'admin') DEFAULT 'user', createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS billing_settings (
                id INT AUTO_INCREMENT PRIMARY KEY, pricePerPost DECIMAL(10,2) DEFAULT 0.5,
                freeQuota INT DEFAULT 100, updatedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_usage (
                id INT AUTO_INCREMENT PRIMARY KEY, userId INT DEFAULT 1, month VARCHAR(7) NOT NULL,
                postCount INT DEFAULT 0, totalCost DECIMAL(10,2) DEFAULT 0, UNIQUE(userId, month)
            )
        """)

        # --- 預設資料寫入與強制修復 ---
        
        # 1. 確保有預設管理員與計費設定
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO users (username, role) VALUES ('Admin', 'admin'), ('TestUser', 'user')")
        
        cursor.execute("SELECT COUNT(*) FROM billing_settings")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO billing_settings (pricePerPost, freeQuota) VALUES (0.5, 100)")

        # 2. 【強制修復文案庫】只要少於 12 筆，代表資料損毀，全部清空重寫！
        cursor.execute("SELECT COUNT(*) FROM trending_templates")
        template_count = cursor.fetchone()[0]
        logger.info(f"目前文案庫有 {template_count} 筆資料")
        if template_count < 12:
            logger.info("檢測到文案庫資料不完整，正在執行強制重置與寫入...")
            # 使用 INSERT IGNORE 避免多工作進程競爭時的重複插入錯誤
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
            for t in templates:
                cursor.execute("INSERT IGNORE INTO trending_templates (title, content, category) VALUES (%s, %s, %s)", t)
            logger.info("✅ 熱門文案模板已載入！")
            
        db.commit()
        logger.info("✅ 資料庫初始化完成")
    except Exception as e:
        logger.error(f"❌ 初始化資料庫失敗: {e}")
        if db:
            try:
                db.rollback()
            except Exception as re:
                logger.debug(f"rollback 失敗: {re}")
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
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT id, accountName, isActive FROM threads_accounts")
        accounts = cursor.fetchall()
        
        cursor.execute("SELECT id, title, content, category, usageCount FROM trending_templates ORDER BY id ASC")
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

        # 取得目前計費設定與個人用量
        cursor.execute("SELECT pricePerPost, freeQuota FROM billing_settings LIMIT 1")
        billing = cursor.fetchone() or {"pricePerPost": 0.5, "freeQuota": 100}
        
        current_month = datetime.now().strftime('%Y-%m')
        cursor.execute("SELECT postCount, totalCost FROM user_usage WHERE month = %s AND userId = 1", (current_month,))
        usage = cursor.fetchone() or {"postCount": 0, "totalCost": 0}
        
        return jsonify({
            "accounts": accounts, "templates": templates, "schedules": schedules,
            "history": history, "billing": billing, "usage": usage
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/dashboard', methods=['GET'])
def get_admin_data():
    """管理員專屬：獲取所有使用者統計與系統設定"""
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT id, username, role, createdAt FROM users")
        users = cursor.fetchall()
        
        # 加上每個使用者的本月用量
        current_month = datetime.now().strftime('%Y-%m')
        for u in users:
            cursor.execute("SELECT postCount FROM user_usage WHERE userId = %s AND month = %s", (u['id'], current_month))
            row = cursor.fetchone()
            u['currentUsage'] = row['postCount'] if row else 0
            
        return jsonify({"users": users}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/settings', methods=['POST'])
def update_billing_settings():
    """管理員專屬：更新計費設定"""
    data = request.json
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("UPDATE billing_settings SET pricePerPost = %s, freeQuota = %s", (data.get('pricePerPost'), data.get('freeQuota')))
        db.commit()
        return jsonify({"message": "計費設定已更新！"}), 200
    except Exception as e:
        return jsonify({"message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

# --- 既有 API (帳號、排程、上傳、AI) ---
@app.route('/api/accounts', methods=['POST'])
def add_account():
    data = request.json
    if not data.get('accountName') or not data.get('accessToken'): return jsonify({"message": "資料不完整"}), 400
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("INSERT INTO threads_accounts (accountName, accessToken) VALUES (%s, %s)", (data['accountName'], data['accessToken']))
        db.commit()
        return jsonify({"message": "帳號新增成功"}), 200
    finally:
        if db: db.close()

@app.route('/api/accounts/<int:acc_id>', methods=['DELETE'])
def delete_account(acc_id):
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
    time.sleep(1.5)
    return jsonify({"url": "https://images.unsplash.com/photo-1707343843437-caacff5cfa74?q=80&w=600&auto=format&fit=crop"}), 200

@app.route('/api/generate-ai', methods=['POST'])
def generate_ai():
    topic = request.json.get('topic', '隨機主題')
    time.sleep(1)
    return jsonify({"content": f"【AI智能生成】這是一段關於「{topic}」的優質 Threads 文案！不僅能吸引眼球，還能增加互動率喔！✨ #自動發文"}), 200

# ==========================================
# 背景發文機器人
# ==========================================
def post_to_threads(content, image_url, access_token):
    if 'mock' in access_token.lower() or '請之後' in access_token:
        return False, "無效的測試 Token"
    try:
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
    except Exception as e: return False, str(e)

def process_posts():
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        now = datetime.now()
        
        cursor.execute("""
            SELECT sp.id, sp.accountId, sp.content, sp.imageUrl, ta.accessToken 
            FROM scheduled_posts sp JOIN threads_accounts ta ON sp.accountId = ta.id
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
                
                # 計算費用並更新 user_usage (假設以 userId=1 為主)
                cursor.execute("SELECT pricePerPost, freeQuota FROM billing_settings LIMIT 1")
                billing = cursor.fetchone()
                price = billing[0] if billing else 0.5
                
                current_month = datetime.now().strftime('%Y-%m')
                cursor.execute("""
                    INSERT INTO user_usage (userId, month, postCount, totalCost) VALUES (1, %s, 1, 0)
                    ON DUPLICATE KEY UPDATE 
                    postCount = postCount + 1, 
                    totalCost = CASE WHEN postCount >= (SELECT freeQuota FROM billing_settings LIMIT 1) THEN totalCost + %s ELSE totalCost END
                """, (current_month, price))
                
                cursor.execute("UPDATE scheduled_posts SET status = 'published', postId = %s WHERE id = %s", (cursor.lastrowid, post_id))
            else:
                logger.error(f"發文失敗: {result}")
                cursor.execute("INSERT INTO posts (accountId, content, status, errorMessage, publishedAt) VALUES (%s, %s, 'failed', %s, NOW())", (post['accountId'], post['content'], result))
                cursor.execute("UPDATE scheduled_posts SET status = 'failed', errorMessage = %s WHERE id = %s", (result, post_id))
            db.commit()
    except Exception as e:
        logger.error(f"process_posts 執行失敗: {e}")
    finally:
        if cursor: cursor.close()
        if db and db.is_connected(): db.close()

def background_worker():
    while True:
        try: process_posts()
        except: pass
        time.sleep(CHECK_INTERVAL_SECONDS)

# 確保資料庫在任何部署方式下都會初始化
init_db()

if __name__ == "__main__":
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))