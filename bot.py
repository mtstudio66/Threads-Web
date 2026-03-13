#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import requests
import json
from datetime import datetime
# 假設使用 SQLAlchemy 或 mysql.connector 連接資料庫
# 這裡使用擬似碼來代表與你的 Drizzle MySQL 資料庫互動
import mysql.connector 

# Threads API 常數設定
THREADS_API_URL = "https://graph.threads.net/v1.0/me/threads"
CHECK_INTERVAL_SECONDS = 60 # 每 60 秒檢查一次資料庫

def get_db_connection():
    # 請替換為你的實際環境變數
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="password",
        database="threads_poster"
    )

def fetch_due_scheduled_posts(cursor):
    """
    從 scheduled_posts 表中抓取狀態為 pending 且時間已到的發文
    """
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
    """
    呼叫 Threads 官方 API 發送貼文
    注意：實際 Threads API 發送流程分為 Create Container -> Publish Container
    """
    try:
        # 1. 創建 Media Container (這裡示範純文字)
        create_payload = {
            "media_type": "TEXT",
            "text": content,
            "access_token": access_token
        }
        
        # 如果有圖片，流程會稍有不同 (media_type="IMAGE", image_url=...)
        
        create_response = requests.post(THREADS_API_URL, data=create_payload)
        create_data = create_response.json()
        
        if 'id' not in create_data:
            raise Exception(f"創建 Container 失敗: {create_data}")
            
        creation_id = create_data['id']
        
        # 2. 發布 Container
        publish_url = f"https://graph.threads.net/v1.0/{creation_id}/publish"
        publish_payload = {"access_token": access_token}
        publish_response = requests.post(publish_url, data=publish_payload)
        publish_data = publish_response.json()
        
        if 'id' in publish_data:
            return True, publish_data['id'] # 成功，回傳 Threads Post ID
        else:
            raise Exception(f"發布失敗: {publish_data}")
            
    except Exception as e:
        return False, str(e)

def process_posts():
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    
    try:
        posts = fetch_due_scheduled_posts(cursor)
        
        for post in posts:
            print(f"[{datetime.now()}] 正在處理排程發文 ID: {post['id']}")
            
            # 將狀態改為 processing
            cursor.execute("UPDATE scheduled_posts SET status = 'processing' WHERE id = %s", (post['id'],))
            db.commit()
            
            # 呼叫 Threads API
            success, result = post_to_threads(post['content'], post['accessToken'])
            
            if success:
                print(f"發文成功！Threads Post ID: {result}")
                # 1. 在 posts 表新增紀錄
                cursor.execute("""
                    INSERT INTO posts (userId, accountId, content, threadsPostId, status, publishedAt)
                    SELECT userId, accountId, content, %s, 'published', NOW()
                    FROM scheduled_posts WHERE id = %s
                """, (result, post['id']))
                new_post_id = cursor.lastrowid
                
                # 2. 更新 scheduled_posts 狀態
                cursor.execute("""
                    UPDATE scheduled_posts 
                    SET status = 'published', postId = %s 
                    WHERE id = %s
                """, (new_post_id, post['id']))
                
            else:
                print(f"發文失敗: {result}")
                cursor.execute("""
                    UPDATE scheduled_posts 
                    SET status = 'failed', errorMessage = %s 
                    WHERE id = %s
                """, (result, post['id']))
                
            db.commit()
            
    finally:
        cursor.close()
        db.close()

if __name__ == "__main__":
    print("啟動 Threads 自動發文排程器 (bot.py)...")
    while True:
        try:
            process_posts()
        except Exception as e:
            print(f"執行期間發生錯誤: {e}")
            
        # 休息 60 秒後再次檢查
        time.sleep(CHECK_INTERVAL_SECONDS)
