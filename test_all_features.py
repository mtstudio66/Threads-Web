#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全面功能測試套件
測試所有 API 端點與核心功能
"""

import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

# 設置環境變數以模擬資料庫連線
os.environ['MYSQL_HOST'] = 'localhost'
os.environ['MYSQL_PORT'] = '3306'
os.environ['MYSQL_USER'] = 'test'
os.environ['MYSQL_PASSWORD'] = 'test'
os.environ['MYSQL_DATABASE'] = 'test_db'

# Mock mysql.connector before importing bot - must be done before any import
class MockMySQLConnector:
    @staticmethod
    def connect(**kwargs):
        return MagicMock()

sys.modules['mysql'] = MagicMock()
sys.modules['mysql.connector'] = MockMySQLConnector()

# Now import the app
from bot import app, hash_password, verify_password, INDEX_FILE


class TestPasswordHashing(unittest.TestCase):
    """測試密碼雜湊功能"""
    
    def test_hash_password_returns_salt_and_hash(self):
        """測試密碼雜湊格式正確"""
        result = hash_password('testpassword')
        self.assertIn(':', result)
        parts = result.split(':')
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0]), 32)  # 16 bytes hex = 32 chars
        self.assertEqual(len(parts[1]), 64)  # SHA-256 hex = 64 chars
        print("✅ 密碼雜湊格式正確")
    
    def test_hash_password_different_salts(self):
        """測試每次雜湊產生不同的鹽值"""
        hash1 = hash_password('testpassword')
        hash2 = hash_password('testpassword')
        self.assertNotEqual(hash1, hash2)
        print("✅ 每次雜湊產生不同的鹽值")
    
    def test_verify_password_correct(self):
        """測試密碼驗證成功"""
        hashed = hash_password('correctpassword')
        self.assertTrue(verify_password('correctpassword', hashed))
        print("✅ 密碼驗證正確通過")
    
    def test_verify_password_incorrect(self):
        """測試密碼驗證失敗"""
        hashed = hash_password('correctpassword')
        self.assertFalse(verify_password('wrongpassword', hashed))
        print("✅ 錯誤密碼驗證失敗")
    
    def test_verify_password_backward_compatible(self):
        """測試舊版無雜湊密碼向下相容"""
        self.assertTrue(verify_password('plaintext', 'plaintext'))
        print("✅ 舊版密碼向下相容")


class TestIndexAndStaticFiles(unittest.TestCase):
    """測試首頁與靜態檔案"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    def test_index_returns_html(self):
        """測試首頁回傳 HTML"""
        with patch('bot.send_file') as mock_send:
            from flask import Response
            mock_send.return_value = Response('index.html content', mimetype='text/html')
            response = self.app.get('/')
            mock_send.assert_called_once_with(INDEX_FILE)
            assert response.headers.get('Cache-Control') == 'no-cache, no-store, must-revalidate'
            assert response.headers.get('Pragma') == 'no-cache'
            assert response.headers.get('Expires') == '0'
        print("✅ 首頁路由正確，快取控制頭已設置")


class MockDBCursor:
    """模擬資料庫 cursor"""
    def __init__(self, results=None):
        self.results = results or []
        self.lastrowid = 1
        self._index = 0
        
    def execute(self, query, params=None):
        pass
    
    def executemany(self, query, params=None):
        pass
    
    def fetchone(self):
        if self.results and self._index < len(self.results):
            result = self.results[self._index]
            self._index += 1
            return result
        return None
    
    def fetchall(self):
        return self.results
    
    def close(self):
        pass


class MockDBConnection:
    """模擬資料庫連線"""
    def __init__(self, cursor):
        self._cursor = cursor
        
    def cursor(self, dictionary=False):
        return self._cursor
    
    def commit(self):
        pass
    
    def rollback(self):
        pass
    
    def close(self):
        pass
    
    def is_connected(self):
        return True


class TestDashboardAPI(unittest.TestCase):
    """測試 Dashboard API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_get_dashboard_data_success(self, mock_db):
        """測試取得儀表板資料成功"""
        cursor = MockDBCursor([
            [],  # accounts
            [],  # templates
            [],  # schedules
            [],  # history
            {'pricePerPost': 0.5, 'freeQuota': 100},  # billing
            {'postCount': 10, 'totalCost': 5.0}  # usage
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.get('/api/dashboard')
        self.assertEqual(response.status_code, 200)
        print("✅ Dashboard API 回傳成功")


class TestAdminLoginAPI(unittest.TestCase):
    """測試管理員登入 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    def test_login_missing_credentials(self):
        """測試登入缺少帳號密碼"""
        response = self.app.post('/api/admin/login',
                                 data=json.dumps({}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertFalse(data['success'])
        print("✅ 缺少帳號密碼時正確回傳 400")
    
    @patch('bot.get_db_connection')
    def test_login_invalid_credentials(self, mock_db):
        """測試登入帳號密碼錯誤"""
        cursor = MockDBCursor([None])  # No user found
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/login',
                                 data=json.dumps({'username': 'wrong', 'password': 'wrong'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 401)
        print("✅ 帳號密碼錯誤時正確回傳 401")
    
    @patch('bot.get_db_connection')
    @patch('bot.verify_password')
    def test_login_success(self, mock_verify, mock_db):
        """測試登入成功"""
        cursor = MockDBCursor([{'id': 1, 'username': 'Admin', 'password': 'hashed', 'role': 'admin'}])
        mock_db.return_value = MockDBConnection(cursor)
        mock_verify.return_value = True
        
        response = self.app.post('/api/admin/login',
                                 data=json.dumps({'username': 'Admin', 'password': 'admin123'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['user']['username'], 'Admin')
        print("✅ 管理員登入成功")


class TestUserManagementAPI(unittest.TestCase):
    """測試使用者管理 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_get_all_users(self, mock_db):
        """測試取得所有使用者"""
        # This endpoint does multiple queries, mock them properly
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [{'id': 1, 'username': 'Admin', 'email': '', 'role': 'admin', 'planId': None, 'isActive': True, 'createdAt': '2024-01-01', 'planName': None}],
            []  # permissions
        ]
        mock_cursor.fetchone.return_value = {'postCount': 0, 'totalCost': 0}
        
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        
        response = self.app.get('/api/admin/users')
        self.assertEqual(response.status_code, 200)
        print("✅ 取得所有使用者列表成功")
    
    def test_create_user_missing_fields(self):
        """測試新增使用者缺少必填欄位"""
        response = self.app.post('/api/admin/users',
                                 data=json.dumps({'username': 'test'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        print("✅ 新增使用者缺少密碼時正確回傳 400")
    
    @patch('bot.get_db_connection')
    @patch('bot.hash_password')
    def test_create_user_success(self, mock_hash, mock_db):
        """測試新增使用者成功"""
        cursor = MockDBCursor()
        cursor.lastrowid = 2
        mock_db.return_value = MockDBConnection(cursor)
        mock_hash.return_value = 'hashed_password'
        
        response = self.app.post('/api/admin/users',
                                 data=json.dumps({
                                     'username': 'newuser',
                                     'password': 'password123',
                                     'email': 'test@test.com',
                                     'role': 'user'
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        print("✅ 新增使用者成功")
    
    @patch('bot.get_db_connection')
    def test_update_user_success(self, mock_db):
        """測試更新使用者成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.put('/api/admin/users/1',
                                data=json.dumps({'email': 'newemail@test.com'}),
                                content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 更新使用者成功")
    
    @patch('bot.get_db_connection')
    def test_delete_user_success(self, mock_db):
        """測試刪除使用者成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.delete('/api/admin/users/2')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        print("✅ 刪除使用者成功")


class TestPermissionAPI(unittest.TestCase):
    """測試權限管理 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_get_user_permissions(self, mock_db):
        """測試取得使用者權限"""
        cursor = MockDBCursor([{'permission': 'post', 'createdAt': '2024-01-01'}])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.get('/api/admin/permissions/1')
        self.assertEqual(response.status_code, 200)
        print("✅ 取得使用者權限成功")
    
    def test_grant_permission_missing_permission(self):
        """測試授予權限缺少權限名稱"""
        response = self.app.post('/api/admin/permissions/1',
                                 data=json.dumps({}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        print("✅ 缺少權限名稱時正確回傳 400")
    
    @patch('bot.get_db_connection')
    def test_grant_permission_success(self, mock_db):
        """測試授予權限成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/permissions/1',
                                 data=json.dumps({'permission': 'post'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 授予權限成功")
    
    @patch('bot.get_db_connection')
    def test_revoke_permission_success(self, mock_db):
        """測試撤銷權限成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.delete('/api/admin/permissions/1/post')
        self.assertEqual(response.status_code, 200)
        print("✅ 撤銷權限成功")


class TestSubscriptionPlanAPI(unittest.TestCase):
    """測試訂閱方案 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_get_subscription_plans(self, mock_db):
        """測試取得所有訂閱方案"""
        cursor = MockDBCursor([
            {'id': 1, 'planName': '免費方案', 'priceUsdt': 0, 'isActive': True}
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.get('/api/admin/plans')
        self.assertEqual(response.status_code, 200)
        print("✅ 取得所有訂閱方案成功")
    
    @patch('bot.get_db_connection')
    def test_create_plan_success(self, mock_db):
        """測試新增訂閱方案成功"""
        cursor = MockDBCursor()
        cursor.lastrowid = 5
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/plans',
                                 data=json.dumps({
                                     'planName': '測試方案',
                                     'priceUsdt': 19.99,
                                     'monthlyPostLimit': 200,
                                     'features': ['功能1', '功能2']
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 新增訂閱方案成功")
    
    @patch('bot.get_db_connection')
    def test_update_plan_success(self, mock_db):
        """測試更新訂閱方案成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.put('/api/admin/plans/1',
                                data=json.dumps({
                                    'planName': '更新方案',
                                    'priceUsdt': 29.99,
                                    'monthlyPostLimit': 300,
                                    'aiGenerationLimit': 100,
                                    'features': ['新功能']
                                }),
                                content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 更新訂閱方案成功")
    
    @patch('bot.get_db_connection')
    def test_delete_plan_success(self, mock_db):
        """測試刪除訂閱方案成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.delete('/api/admin/plans/5')
        self.assertEqual(response.status_code, 200)
        print("✅ 刪除訂閱方案成功")


class TestUSDTSettingsAPI(unittest.TestCase):
    """測試 USDT 設定 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_get_usdt_settings(self, mock_db):
        """測試取得 USDT 設定"""
        cursor = MockDBCursor([
            {'walletAddress': 'TRX123', 'networkType': 'TRC20', 'minPaymentAmount': 10.00}
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.get('/api/admin/usdt-settings')
        self.assertEqual(response.status_code, 200)
        print("✅ 取得 USDT 設定成功")
    
    @patch('bot.get_db_connection')
    def test_update_usdt_settings_insert(self, mock_db):
        """測試新增 USDT 設定"""
        cursor = MockDBCursor([(0,)])  # count = 0, need insert
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/usdt-settings',
                                 data=json.dumps({
                                     'walletAddress': 'TRXnew123',
                                     'networkType': 'TRC20',
                                     'minPaymentAmount': 15.00
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 新增 USDT 設定成功")
    
    @patch('bot.get_db_connection')
    def test_update_usdt_settings_update(self, mock_db):
        """測試更新 USDT 設定"""
        cursor = MockDBCursor([(1,)])  # count = 1, need update
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/usdt-settings',
                                 data=json.dumps({
                                     'walletAddress': 'TRXupdated456',
                                     'networkType': 'ERC20',
                                     'minPaymentAmount': 20.00
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 更新 USDT 設定成功")


class TestPaymentAPI(unittest.TestCase):
    """測試付款記錄 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_get_payment_records(self, mock_db):
        """測試取得付款記錄"""
        cursor = MockDBCursor([
            {'id': 1, 'userId': 1, 'planId': 1, 'amountUsdt': 9.99, 'status': 'pending'}
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.get('/api/admin/payments')
        self.assertEqual(response.status_code, 200)
        print("✅ 取得付款記錄成功")
    
    @patch('bot.get_db_connection')
    def test_confirm_payment_not_found(self, mock_db):
        """測試確認付款 - 找不到記錄"""
        cursor = MockDBCursor([None])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/payments/999/confirm',
                                 data=json.dumps({'txHash': 'abc123'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 404)
        print("✅ 找不到付款記錄時正確回傳 404")
    
    @patch('bot.get_db_connection')
    def test_confirm_payment_success(self, mock_db):
        """測試確認付款成功"""
        cursor = MockDBCursor([
            {'id': 1, 'userId': 1, 'planId': 2, 'amountUsdt': 9.99}
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/payments/1/confirm',
                                 data=json.dumps({'txHash': 'tx123abc'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 確認付款成功")


class TestUserSubscribeAPI(unittest.TestCase):
    """測試使用者訂閱 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    def test_subscribe_missing_fields(self):
        """測試訂閱缺少必填欄位"""
        response = self.app.post('/api/user/subscribe',
                                 data=json.dumps({}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        print("✅ 訂閱缺少欄位時正確回傳 400")
    
    @patch('bot.get_db_connection')
    def test_subscribe_plan_not_found(self, mock_db):
        """測試訂閱找不到方案"""
        cursor = MockDBCursor([None])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/user/subscribe',
                                 data=json.dumps({'userId': 1, 'planId': 999}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 404)
        print("✅ 找不到方案時正確回傳 404")
    
    @patch('bot.get_db_connection')
    def test_subscribe_free_plan(self, mock_db):
        """測試訂閱免費方案"""
        cursor = MockDBCursor([
            {'id': 1, 'planName': '免費方案', 'priceUsdt': 0}
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/user/subscribe',
                                 data=json.dumps({'userId': 1, 'planId': 1}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        print("✅ 訂閱免費方案成功")
    
    @patch('bot.get_db_connection')
    def test_subscribe_paid_plan(self, mock_db):
        """測試訂閱付費方案"""
        cursor = MockDBCursor([
            {'id': 2, 'planName': '標準方案', 'priceUsdt': 9.99},
            {'walletAddress': 'TRX123', 'networkType': 'TRC20'}
        ])
        cursor.lastrowid = 1
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/user/subscribe',
                                 data=json.dumps({'userId': 1, 'planId': 2}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn('paymentId', data)
        self.assertIn('walletAddress', data)
        print("✅ 訂閱付費方案成功")


class TestAccountAPI(unittest.TestCase):
    """測試帳號管理 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    def test_add_account_missing_fields(self):
        """測試新增帳號缺少欄位"""
        response = self.app.post('/api/accounts',
                                 data=json.dumps({'accountName': 'test'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        print("✅ 新增帳號缺少欄位時正確回傳 400")
    
    @patch('bot.get_db_connection')
    def test_add_account_success(self, mock_db):
        """測試新增帳號成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/accounts',
                                 data=json.dumps({
                                     'accountName': 'test_account',
                                     'accessToken': 'test_token_123'
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 新增帳號成功")
    
    @patch('bot.get_db_connection')
    def test_delete_account_success(self, mock_db):
        """測試刪除帳號成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.delete('/api/accounts/1')
        self.assertEqual(response.status_code, 200)
        print("✅ 刪除帳號成功")


class TestScheduleAPI(unittest.TestCase):
    """測試排程管理 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_save_schedule_success(self, mock_db):
        """測試新增排程成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/schedule',
                                 data=json.dumps({
                                     'accountId': 1,
                                     'content': '測試內容',
                                     'scheduledAt': '2024-12-31 12:00:00'
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 新增排程成功")
    
    @patch('bot.get_db_connection')
    def test_cancel_schedule_success(self, mock_db):
        """測試取消排程成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.delete('/api/schedule/1')
        self.assertEqual(response.status_code, 200)
        print("✅ 取消排程成功")


class TestUtilityAPI(unittest.TestCase):
    """測試工具 API (上傳、AI生成)"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.time.sleep')  # Skip sleep for faster tests
    def test_upload_returns_url(self, mock_sleep):
        """測試上傳回傳 URL"""
        response = self.app.post('/api/upload')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('url', data)
        self.assertTrue(data['url'].startswith('http'))
        print("✅ 上傳 API 回傳 URL 成功")
    
    @patch('bot.time.sleep')  # Skip sleep for faster tests
    def test_generate_ai_success(self, mock_sleep):
        """測試 AI 生成成功"""
        response = self.app.post('/api/generate-ai',
                                 data=json.dumps({'topic': '測試主題'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('content', data)
        self.assertIn('測試主題', data['content'])
        print("✅ AI 生成 API 成功")
    
    @patch('bot.time.sleep')
    def test_generate_ai_default_topic(self, mock_sleep):
        """測試 AI 生成預設主題"""
        response = self.app.post('/api/generate-ai',
                                 data=json.dumps({}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('隨機主題', data['content'])
        print("✅ AI 生成預設主題成功")


class TestBillingSettingsAPI(unittest.TestCase):
    """測試計費設定 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_update_billing_settings_success(self, mock_db):
        """測試更新計費設定成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/settings',
                                 data=json.dumps({
                                     'pricePerPost': 0.3,
                                     'freeQuota': 150
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 更新計費設定成功")


def run_all_tests():
    """執行所有測試並輸出結果摘要"""
    print("\n" + "="*60)
    print("🚀 開始執行全面功能測試")
    print("="*60 + "\n")
    
    # 創建測試套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加所有測試類別
    test_classes = [
        TestPasswordHashing,
        TestIndexAndStaticFiles,
        TestDashboardAPI,
        TestAdminLoginAPI,
        TestUserManagementAPI,
        TestPermissionAPI,
        TestSubscriptionPlanAPI,
        TestUSDTSettingsAPI,
        TestPaymentAPI,
        TestUserSubscribeAPI,
        TestAccountAPI,
        TestScheduleAPI,
        TestUtilityAPI,
        TestBillingSettingsAPI,
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    # 執行測試
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 輸出摘要
    print("\n" + "="*60)
    print("📊 測試結果摘要")
    print("="*60)
    print(f"總測試數: {result.testsRun}")
    print(f"✅ 通過: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"❌ 失敗: {len(result.failures)}")
    print(f"⚠️  錯誤: {len(result.errors)}")
    
    if result.failures:
        print("\n失敗的測試:")
        for test, traceback in result.failures:
            print(f"  - {test}")
    
    if result.errors:
        print("\n錯誤的測試:")
        for test, traceback in result.errors:
            print(f"  - {test}")
    
    print("="*60)
    
    if result.wasSuccessful():
        print("🎉 所有測試通過！系統功能正常運作。")
    else:
        print("⚠️  部分測試未通過，請檢查上述問題。")
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
