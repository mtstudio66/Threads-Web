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

        # 【新增】使用者表與計費設定表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL, email VARCHAR(128),
                role ENUM('user', 'admin') DEFAULT 'user', 
                planId INT DEFAULT NULL, isActive BOOLEAN DEFAULT TRUE,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        
        # 【新增】訂閱方案表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscription_plans (
                id INT AUTO_INCREMENT PRIMARY KEY,
                planName VARCHAR(64) NOT NULL UNIQUE,
                description TEXT,
                priceUsdt DECIMAL(10,2) NOT NULL DEFAULT 0,
                monthlyPostLimit INT DEFAULT 100,
                aiGenerationLimit INT DEFAULT 50,
                features JSON,
                isActive BOOLEAN DEFAULT TRUE,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updatedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        
        # 【新增】使用者權限表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_permissions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                userId INT NOT NULL,
                permission VARCHAR(64) NOT NULL,
                grantedBy INT,
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(userId, permission)
            )
        """)
        
        # 【新增】USDT付款設定表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usdt_settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                walletAddress VARCHAR(128) NOT NULL,
                networkType ENUM('TRC20', 'ERC20', 'BEP20') DEFAULT 'TRC20',
                minPaymentAmount DECIMAL(10,2) DEFAULT 10.00,
                isActive BOOLEAN DEFAULT TRUE,
                updatedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        
        # 【新增】付款記錄表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payment_records (
                id INT AUTO_INCREMENT PRIMARY KEY,
                userId INT NOT NULL,
                planId INT NOT NULL,
                amountUsdt DECIMAL(10,2) NOT NULL,
                txHash VARCHAR(128),
                status ENUM('pending', 'confirmed', 'failed', 'expired') DEFAULT 'pending',
                createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                confirmedAt TIMESTAMP NULL
            )
        """)

        # --- 預設資料寫入與強制修復 ---
        
        # 1. 確保有預設管理員與計費設定
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            # 預設密碼: admin123 (生產環境請更改)
            cursor.execute("INSERT INTO users (username, password, role) VALUES ('Admin', 'admin123', 'admin'), ('TestUser', 'test123', 'user')")
        
        cursor.execute("SELECT COUNT(*) FROM billing_settings")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO billing_settings (pricePerPost, freeQuota) VALUES (0.5, 100)")
        
        # 2. 確保有預設訂閱方案
        cursor.execute("SELECT COUNT(*) FROM subscription_plans")
        if cursor.fetchone()[0] == 0:
            default_plans = [
                ('免費方案', '基本免費方案，適合初次使用', 0, 100, 10, '["基本發文", "帳號管理"]'),
                ('標準方案', '適合個人創作者使用', 9.99, 500, 50, '["基本發文", "帳號管理", "AI文案生成", "排程發文"]'),
                ('專業方案', '適合企業與專業用戶', 29.99, 2000, 200, '["基本發文", "帳號管理", "AI文案生成", "排程發文", "進階數據分析", "優先客服"]'),
                ('企業方案', '無限制使用，適合大型企業', 99.99, -1, -1, '["無限發文", "帳號管理", "AI文案生成", "排程發文", "進階數據分析", "專屬客服", "API存取"]')
            ]
            cursor.executemany(
                "INSERT INTO subscription_plans (planName, description, priceUsdt, monthlyPostLimit, aiGenerationLimit, features) VALUES (%s, %s, %s, %s, %s, %s)",
                default_plans
            )
        
        # 3. 確保有預設USDT設定
        cursor.execute("SELECT COUNT(*) FROM usdt_settings")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO usdt_settings (walletAddress, networkType, minPaymentAmount) VALUES ('TRX_WALLET_ADDRESS_HERE', 'TRC20', 10.00)")

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
            cursor.executemany("INSERT IGNORE INTO trending_templates (title, content, category) VALUES (%s, %s, %s)", templates)
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

# ==========================================
# 管理員認證與帳號管理 API
# ==========================================

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """管理員登入"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"success": False, "message": "請輸入帳號密碼"}), 400
    
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, username, role FROM users WHERE username = %s AND password = %s AND role = 'admin'", (username, password))
        user = cursor.fetchone()
        
        if user:
            return jsonify({"success": True, "message": "登入成功", "user": user}), 200
        return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/users', methods=['GET'])
def get_all_users():
    """取得所有使用者列表"""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.role, u.planId, u.isActive, u.createdAt,
                   sp.planName 
            FROM users u 
            LEFT JOIN subscription_plans sp ON u.planId = sp.id
            ORDER BY u.id ASC
        """)
        users = cursor.fetchall()
        
        current_month = datetime.now().strftime('%Y-%m')
        for u in users:
            cursor.execute("SELECT postCount, totalCost FROM user_usage WHERE userId = %s AND month = %s", (u['id'], current_month))
            row = cursor.fetchone()
            u['currentUsage'] = row['postCount'] if row else 0
            u['totalCost'] = float(row['totalCost']) if row else 0
            
            # 取得權限
            cursor.execute("SELECT permission FROM user_permissions WHERE userId = %s", (u['id'],))
            u['permissions'] = [p['permission'] for p in cursor.fetchall()]
            
        return jsonify({"users": users}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/users', methods=['POST'])
def create_user():
    """新增使用者或管理員"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    email = data.get('email', '')
    role = data.get('role', 'user')
    planId = data.get('planId')
    
    if not username or not password:
        return jsonify({"success": False, "message": "帳號密碼為必填"}), 400
    
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO users (username, password, email, role, planId) VALUES (%s, %s, %s, %s, %s)",
            (username, password, email, role, planId)
        )
        db.commit()
        return jsonify({"success": True, "message": "使用者新增成功", "userId": cursor.lastrowid}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/users/<int:user_id>', methods=['PUT'])
def update_user(user_id):
    """更新使用者資料"""
    data = request.json
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        updates = []
        values = []
        if 'username' in data:
            updates.append("username = %s")
            values.append(data['username'])
        if 'password' in data and data['password']:
            updates.append("password = %s")
            values.append(data['password'])
        if 'email' in data:
            updates.append("email = %s")
            values.append(data['email'])
        if 'role' in data:
            updates.append("role = %s")
            values.append(data['role'])
        if 'planId' in data:
            updates.append("planId = %s")
            values.append(data['planId'])
        if 'isActive' in data:
            updates.append("isActive = %s")
            values.append(data['isActive'])
        
        if updates:
            values.append(user_id)
            cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", values)
            db.commit()
        
        return jsonify({"success": True, "message": "使用者資料已更新"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    """刪除使用者"""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM user_permissions WHERE userId = %s", (user_id,))
        cursor.execute("DELETE FROM user_usage WHERE userId = %s", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        db.commit()
        return jsonify({"success": True, "message": "使用者已刪除"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

# ==========================================
# 使用者權限管理 API
# ==========================================

@app.route('/api/admin/permissions/<int:user_id>', methods=['GET'])
def get_user_permissions(user_id):
    """取得使用者權限"""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT permission, createdAt FROM user_permissions WHERE userId = %s", (user_id,))
        permissions = cursor.fetchall()
        return jsonify({"permissions": permissions}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/permissions/<int:user_id>', methods=['POST'])
def grant_permission(user_id):
    """授予使用者權限"""
    data = request.json
    permission = data.get('permission')
    granted_by = data.get('grantedBy', 1)  # 預設管理員ID=1
    
    if not permission:
        return jsonify({"success": False, "message": "權限名稱為必填"}), 400
    
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT IGNORE INTO user_permissions (userId, permission, grantedBy) VALUES (%s, %s, %s)",
            (user_id, permission, granted_by)
        )
        db.commit()
        return jsonify({"success": True, "message": "權限已授予"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/permissions/<int:user_id>/<permission>', methods=['DELETE'])
def revoke_permission(user_id, permission):
    """撤銷使用者權限"""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM user_permissions WHERE userId = %s AND permission = %s", (user_id, permission))
        db.commit()
        return jsonify({"success": True, "message": "權限已撤銷"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

# ==========================================
# 訂閱方案管理 API
# ==========================================

@app.route('/api/admin/plans', methods=['GET'])
def get_subscription_plans():
    """取得所有訂閱方案"""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM subscription_plans ORDER BY priceUsdt ASC")
        plans = cursor.fetchall()
        return jsonify({"plans": plans}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/plans', methods=['POST'])
def create_plan():
    """新增訂閱方案"""
    data = request.json
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        import json as json_lib
        features = json_lib.dumps(data.get('features', [])) if isinstance(data.get('features'), list) else data.get('features', '[]')
        
        cursor.execute("""
            INSERT INTO subscription_plans (planName, description, priceUsdt, monthlyPostLimit, aiGenerationLimit, features, isActive)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            data.get('planName'),
            data.get('description', ''),
            data.get('priceUsdt', 0),
            data.get('monthlyPostLimit', 100),
            data.get('aiGenerationLimit', 50),
            features,
            data.get('isActive', True)
        ))
        db.commit()
        return jsonify({"success": True, "message": "方案新增成功", "planId": cursor.lastrowid}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/plans/<int:plan_id>', methods=['PUT'])
def update_plan(plan_id):
    """更新訂閱方案"""
    data = request.json
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        import json as json_lib
        features = json_lib.dumps(data.get('features', [])) if isinstance(data.get('features'), list) else data.get('features')
        
        cursor.execute("""
            UPDATE subscription_plans 
            SET planName = %s, description = %s, priceUsdt = %s, monthlyPostLimit = %s, 
                aiGenerationLimit = %s, features = %s, isActive = %s
            WHERE id = %s
        """, (
            data.get('planName'),
            data.get('description'),
            data.get('priceUsdt'),
            data.get('monthlyPostLimit'),
            data.get('aiGenerationLimit'),
            features,
            data.get('isActive', True),
            plan_id
        ))
        db.commit()
        return jsonify({"success": True, "message": "方案已更新"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/plans/<int:plan_id>', methods=['DELETE'])
def delete_plan(plan_id):
    """刪除訂閱方案"""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM subscription_plans WHERE id = %s", (plan_id,))
        db.commit()
        return jsonify({"success": True, "message": "方案已刪除"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

# ==========================================
# USDT付款設定 API
# ==========================================

@app.route('/api/admin/usdt-settings', methods=['GET'])
def get_usdt_settings():
    """取得USDT付款設定"""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM usdt_settings LIMIT 1")
        settings = cursor.fetchone() or {"walletAddress": "", "networkType": "TRC20", "minPaymentAmount": 10.00}
        return jsonify({"settings": settings}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/usdt-settings', methods=['POST'])
def update_usdt_settings():
    """更新USDT付款設定"""
    data = request.json
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM usdt_settings")
        count = cursor.fetchone()[0]
        
        if count == 0:
            cursor.execute("""
                INSERT INTO usdt_settings (walletAddress, networkType, minPaymentAmount, isActive)
                VALUES (%s, %s, %s, %s)
            """, (
                data.get('walletAddress'),
                data.get('networkType', 'TRC20'),
                data.get('minPaymentAmount', 10.00),
                data.get('isActive', True)
            ))
        else:
            cursor.execute("""
                UPDATE usdt_settings 
                SET walletAddress = %s, networkType = %s, minPaymentAmount = %s, isActive = %s
                WHERE id = 1
            """, (
                data.get('walletAddress'),
                data.get('networkType', 'TRC20'),
                data.get('minPaymentAmount', 10.00),
                data.get('isActive', True)
            ))
        
        db.commit()
        return jsonify({"success": True, "message": "USDT設定已更新"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

# ==========================================
# 付款記錄 API
# ==========================================

@app.route('/api/admin/payments', methods=['GET'])
def get_payment_records():
    """取得所有付款記錄"""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT pr.*, u.username, sp.planName
            FROM payment_records pr
            LEFT JOIN users u ON pr.userId = u.id
            LEFT JOIN subscription_plans sp ON pr.planId = sp.id
            ORDER BY pr.createdAt DESC
            LIMIT 100
        """)
        payments = cursor.fetchall()
        return jsonify({"payments": payments}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/admin/payments/<int:payment_id>/confirm', methods=['POST'])
def confirm_payment(payment_id):
    """確認付款"""
    data = request.json
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        # 取得付款資料
        cursor.execute("SELECT * FROM payment_records WHERE id = %s", (payment_id,))
        payment = cursor.fetchone()
        
        if not payment:
            return jsonify({"success": False, "message": "找不到付款記錄"}), 404
        
        # 更新付款狀態
        cursor.execute("""
            UPDATE payment_records 
            SET status = 'confirmed', txHash = %s, confirmedAt = NOW()
            WHERE id = %s
        """, (data.get('txHash', ''), payment_id))
        
        # 更新使用者方案
        cursor.execute("UPDATE users SET planId = %s WHERE id = %s", (payment['planId'], payment['userId']))
        
        db.commit()
        return jsonify({"success": True, "message": "付款已確認，方案已啟用"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if db: db.close()

@app.route('/api/user/subscribe', methods=['POST'])
def user_subscribe():
    """使用者訂閱方案"""
    data = request.json
    user_id = data.get('userId')
    plan_id = data.get('planId')
    
    if not user_id or not plan_id:
        return jsonify({"success": False, "message": "使用者ID與方案ID為必填"}), 400
    
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        # 取得方案資料
        cursor.execute("SELECT * FROM subscription_plans WHERE id = %s", (plan_id,))
        plan = cursor.fetchone()
        
        if not plan:
            return jsonify({"success": False, "message": "找不到此方案"}), 404
        
        # 如果是免費方案，直接啟用
        if float(plan['priceUsdt']) == 0:
            cursor.execute("UPDATE users SET planId = %s WHERE id = %s", (plan_id, user_id))
            db.commit()
            return jsonify({"success": True, "message": "免費方案已啟用"}), 200
        
        # 付費方案，建立付款記錄
        cursor.execute("""
            INSERT INTO payment_records (userId, planId, amountUsdt, status)
            VALUES (%s, %s, %s, 'pending')
        """, (user_id, plan_id, plan['priceUsdt']))
        db.commit()
        
        # 取得USDT收款資訊
        cursor.execute("SELECT walletAddress, networkType FROM usdt_settings WHERE isActive = TRUE LIMIT 1")
        usdt_info = cursor.fetchone()
        
        return jsonify({
            "success": True,
            "message": "付款訂單已建立，請進行USDT付款",
            "paymentId": cursor.lastrowid,
            "amount": float(plan['priceUsdt']),
            "walletAddress": usdt_info['walletAddress'] if usdt_info else '',
            "networkType": usdt_info['networkType'] if usdt_info else 'TRC20'
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
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