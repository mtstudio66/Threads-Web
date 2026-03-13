#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
import logging
from datetime import datetime
import mysql.connector

# --- 1. 設定專業的日誌輸出 (讓你在 Zeabur 後台能看清楚每一步) ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- 2. Threads API 常數設定 ---
THREADS_API_URL = "https://graph.threads.net/v1.0/me/threads"
CHECK_INTERVAL_SECONDS = 60 # 每 60 秒檢查一次資料庫

# --- 3. 自動讀取 Zeabur 環境變數 ---
# 這樣寫就不會寫死密碼，而且能在 Zeabur 上自動連線
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

def fetch_due_scheduled_posts(cursor):
    """從 scheduled_posts 表中抓取狀態為 pending 且時間已到的發文"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    query = """
        SELECT sp.id, sp.accountId, sp.content, sp.imageUrls, ta.accessToken 
        FROM scheduled_posts sp
        JOIN threads_accounts ta ON sp.accountId = ta.id
        WHERE sp.status = 'pending' AND sp.scheduledAt <= %s AND ta.isActive = 1
    """
    cursor.execute(query, (now,))
    return cursor.fetchall()

def post_to_threads(content, access_token, image_urls=None):
    """呼叫 Threads 官方 API 發送貼文"""
    try:
        # 1. 創建 Media Container (這裡示範純文字)
        create_payload = {
            "media_type": "TEXT",
            "text": content,
            "access_token": access_token
        }
        
        # 加入 timeout 防止網路卡住導致程式死當
        create_response = requests.post(THREADS_API_URL, data=create_payload, timeout=15)
        create_data = create_response.json()
        
        if 'id' not in create_data:
            error_msg = create_data.get('error', {}).get('message', '未知錯誤')
            return False, f"創建 Container 失敗: {error_msg}"
            
        creation_id = create_data['id']
        
        # 2. 發布 Container
        publish_url = f"https://graph.threads.net/v1.0/{creation_id}/publish"
        publish_payload = {"access_token": access_token}
        publish_response = requests.post(publish_url, data=publish_payload, timeout=15)
        publish_data = publish_response.json()
        
        if 'id' in publish_data:
            return True, publish_data['id'] # 成功，回傳 Threads Post ID
        else:
            error_msg = publish_data.get('error', {}).get('message', '未知錯誤')
            return False, f"發布失敗: {error_msg}"
            
    except requests.exceptions.RequestException as e:
        return False, f"API 網路連線錯誤: {str(e)}"
    except Exception as e:
        return False, f"發生未預期錯誤: {str(e)}"

def process_posts():
    """處理發文的主要邏輯，包含完整的防崩潰機制"""
    db = None
    cursor = None
    
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        posts = fetch_due_scheduled_posts(cursor)
        
        if not posts:
            logger.debug("目前沒有需要執行的排程。")
            
        for post in posts:
            post_id = post['id']
            logger.info(f"正在處理排程發文 ID: {post_id}")
            
            # 將狀態改為 processing
            cursor.execute("UPDATE scheduled_posts SET status = 'processing' WHERE id = %s", (post_id,))
            db.commit()
            
            # 呼叫 Threads API
            success, result = post_to_threads(post['content'], post['accessToken'])
            
            if success:
                logger.info(f"發文成功！Threads Post ID: {result}")
                # 1. 在 posts 表新增紀錄
                cursor.execute("""
                    INSERT INTO posts (userId, accountId, content, threadsPostId, status, publishedAt)
                    SELECT userId, accountId, content, %s, 'published', NOW()
                    FROM scheduled_posts WHERE id = %s
                """, (result, post_id))
                new_post_id = cursor.lastrowid
                
                # 2. 更新 scheduled_posts 狀態
                cursor.execute("""
                    UPDATE scheduled_posts 
                    SET status = 'published', postId = %s 
                    WHERE id = %s
                """, (new_post_id, post_id))
                
            else:
                logger.error(f"發文失敗: {result}")
                cursor.execute("""
                    UPDATE scheduled_posts 
                    SET status = 'failed', errorMessage = %s 
                    WHERE id = %s
                """, (result, post_id))
                
            db.commit()
            
    except mysql.connector.Error as db_err:
        logger.error(f"資料庫連線或操作失敗: {db_err}")
    except Exception as e:
        logger.error(f"執行 process_posts 期間發生錯誤: {e}")
        
    finally:
        # 【最關鍵的防崩潰修復】：確保 db 和 cursor 真的有被建立起來才去關閉它
        if cursor is not None:
            try:
                cursor.close()
            except:
                pass
        if db is not None and db.is_connected():
            try:
                db.close()
            except:
                pass

if __name__ == "__main__":
    logger.info("==========================================")
    logger.info("啟動 Threads 自動發文排程器 (bot.py)...")
    logger.info(f"目標資料庫主機: {DB_HOST}:{DB_PORT}")
    logger.info("==========================================")
    
    while True:
        try:
            process_posts()
        except KeyboardInterrupt:
            logger.info("程式被手動中止。")
            break
        except Exception as e:
            logger.critical(f"主迴圈發生嚴重錯誤，正在防止崩潰: {e}")
            
        # 休息 60 秒後再次檢查
        time.sleep(CHECK_INTERVAL_SECONDS)
