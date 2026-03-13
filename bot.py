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
CHECK_INTERVAL_SECONDS = 60 # 背景機器人每 60 秒檢查一次

DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_PORT = int(os.getenv("MYSQL_PORT", 3306))
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
DB_DATABASE = os.getenv("MYSQL_DATABASE", "zeabur")

def get_db_connection():
    """建立資料庫連線"""
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_DATABASE
    )

# ==========================================
# Flask 網頁與 API 路由設定 (處理前端 UI 請求)
# ==========================================

@app.route('/')
def index():
    """當使用者訪問網址時，顯示 index.html 網頁"""
    try:
        return send_file('index.html')
    except Exception as e:
        return f"找不到 index.html 檔案，請確認是否已上傳至 GitHub。錯誤: {e}"

@app.route('/api/schedule', methods=['POST'])
def save_schedule():
    """接收來自前端網頁的排程資料，並存入 MySQL"""
    data = request.json
    content = data.get('content')
    scheduled_at = data.get('scheduledAt')
    
    if not content or not scheduled_at:
        return jsonify({"message": "資料不完整"}), 400
        
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        # 這裡為了測試，預設綁定 userId=1, accountId=1 (需確保你資料庫有這筆帳號)
        query = """
            INSERT INTO scheduled_posts (userId, accountId, content, scheduledAt, status) 
            VALUES (1, 1, %s, %s, 'pending')
        """
        cursor.execute(query, (content, scheduled_at))
        db.commit()
        
        return jsonify({"message": "排程儲存成功！機器人將在指定時間發布。"}), 200
    except Exception as e:
        logger.error(f"儲存排程失敗: {e}")
        return jsonify({"message": f"儲存失敗: {e}"}), 500
    finally:
        if cursor is not None:
            try: cursor.close()
            except: pass
        if db is not None and db.is_connected():
            try: db.close()
            except: pass


# ==========================================
# 背景發文機器人邏輯 (每分鐘自動檢查並發文)
# ==========================================

def fetch_due_scheduled_posts(cursor):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    query = """
        SELECT sp.id, sp.accountId, sp.content, sp.imageUrls, ta.accessToken 
        FROM scheduled_posts sp
        JOIN threads_accounts ta ON sp.accountId = ta.id
        WHERE sp.status = 'pending' AND sp.scheduledAt <= %s AND ta.isActive = 1
    """
    cursor.execute(query, (now,))
    return cursor.fetchall()

def post_to_threads(content, access_token):
    try:
        # 1. 創建 Media Container
        create_payload = {"media_type": "TEXT", "text": content, "access_token": access_token}
        create_response = requests.post(THREADS_API_URL, data=create_payload, timeout=15).json()
        
        if 'id' not in create_response:
            return False, f"創建 Container 失敗: {create_response}"
            
        creation_id = create_response['id']
        
        # 2. 發布 Container
        publish_payload = {"access_token": access_token}
        publish_url = f"https://graph.threads.net/v1.0/{creation_id}/publish"
        publish_response = requests.post(publish_url, data=publish_payload, timeout=15).json()
        
        if 'id' in publish_response:
            return True, publish_response['id']
        else:
            return False, f"發布失敗: {publish_response}"
            
    except Exception as e:
        return False, f"發生錯誤: {str(e)}"

def process_posts():
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        posts = fetch_due_scheduled_posts(cursor)
        
        for post in posts:
            post_id = post['id']
            logger.info(f"機器人開始處理排程發文 ID: {post_id}")
            
            cursor.execute("UPDATE scheduled_posts SET status = 'processing' WHERE id = %s", (post_id,))
            db.commit()
            
            success, result = post_to_threads(post['content'], post['accessToken'])
            
            if success:
                logger.info(f"發文成功！Threads Post ID: {result}")
                cursor.execute("""
                    INSERT INTO posts (userId, accountId, content, threadsPostId, status, publishedAt)
                    SELECT userId, accountId, content, %s, 'published', NOW()
                    FROM scheduled_posts WHERE id = %s
                """, (result, post_id))
                
                new_post_id = cursor.lastrowid
                cursor.execute("UPDATE scheduled_posts SET status = 'published', postId = %s WHERE id = %s", (new_post_id, post_id))
            else:
                logger.error(f"發文失敗: {result}")
                cursor.execute("UPDATE scheduled_posts SET status = 'failed', errorMessage = %s WHERE id = %s", (result, post_id))
                
            db.commit()
            
    except Exception as e:
        logger.error(f"背景處理排程時發生錯誤: {e}")
    finally:
        if cursor is not None:
            try: cursor.close()
            except: pass
        if db is not None and db.is_connected():
            try: db.close()
            except: pass

def background_worker():
    """這是背景執行的無限迴圈"""
    logger.info("後台發文機器人已啟動，每 60 秒檢查一次資料庫...")
    while True:
        try:
            process_posts()
        except Exception as e:
            logger.critical(f"背景機器人發生防崩潰保護: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


# ==========================================
# 程式啟動入口
# ==========================================
if __name__ == "__main__":
    logger.info("==========================================")
    logger.info("系統啟動中...")
    logger.info(f"資料庫連線: {DB_HOST}:{DB_PORT}")
    logger.info("==========================================")
    
    # 1. 啟動背景發文機器人 (使用多執行緒，這樣才不會卡住網頁伺服器)
    worker_thread = threading.Thread(target=background_worker, daemon=True)
    worker_thread.start()
    
    # 2. 啟動 Flask 網頁伺服器 (Zeabur 會自動分配 PORT，預設 8080)
    port = int(os.getenv("PORT", 8080))
    app.run(host='0.0.0.0', port=port)