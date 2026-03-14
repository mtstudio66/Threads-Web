#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全面功能測試套件
測試所有 API 端點與核心功能
"""

import os
import io
import sys
import json
import struct
import zlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

TAIPEI_TZ_OFFSET = -480

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
from bot import app, hash_password, verify_password, INDEX_FILE, normalize_scheduled_at, post_to_threads


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
        self.executed = []
        
    def execute(self, query, params=None):
        self.executed.append((query, params))
    
    def executemany(self, query, params=None):
        self.executed.append((query, params))
    
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


class SequentialMockDBCursor(MockDBCursor):
    """依序回傳 fetchall/fetchone 結果的 cursor"""
    def fetchone(self):
        if self.results and self._index < len(self.results):
            result = self.results[self._index]
            self._index += 1
            return result
        return None

    def fetchall(self):
        if self.results and self._index < len(self.results):
            result = self.results[self._index]
            self._index += 1
            return result
        return []


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

    @patch('bot.get_db_connection')
    def test_get_dashboard_data_formats_taipei_datetimes(self, mock_db):
        """測試 Dashboard API 會以台北時間字串回傳排程與歷史時間"""
        cursor = SequentialMockDBCursor([
            [],  # accounts
            [],  # templates
            [{'id': 1, 'scheduledAt': datetime(2026, 3, 14, 10, 0, 17), 'status': 'pending', 'content': '排程', 'imageUrl': None, 'accountName': 'demo'}],
            [{'id': 2, 'publishedAt': datetime(2026, 3, 14, 18, 54, 4), 'status': 'published', 'content': '已發布', 'errorMessage': None, 'accountName': 'demo'}],
            {'pricePerPost': 0.5, 'freeQuota': 100},  # billing
            {'postCount': 10, 'totalCost': 5.0}  # usage
        ])
        mock_db.return_value = MockDBConnection(cursor)

        response = self.app.get('/api/dashboard')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['schedules'][0]['scheduledAt'], '2026-03-14 10:00:17')
        self.assertEqual(data['history'][0]['publishedAt'], '2026-03-14 18:54:04')
        print("✅ Dashboard API 會回傳台北時間字串，避免 GMT 誤判")


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
    
    @patch('bot.validate_threads_token')
    @patch('bot.get_db_connection')
    def test_add_account_success(self, mock_db, mock_validate):
        """測試新增帳號成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        mock_validate.return_value = (True, 'Token 驗證成功', {'username': 'test_account'})
        
        response = self.app.post('/api/accounts',
                                 data=json.dumps({
                                     'accountName': 'test_account',
                                     'accessToken': 'test_token_123'
                                  }),
                                  content_type='application/json')
        self.assertEqual(response.status_code, 200)
        print("✅ 新增帳號成功")

    @patch('bot.validate_threads_token')
    def test_validate_account_token_invalid(self, mock_validate):
        """測試驗證帳號 Token 失敗"""
        mock_validate.return_value = (False, '權杖已過期', None)

        response = self.app.post('/api/accounts/validate-token',
                                 data=json.dumps({'accessToken': 'bad_token'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('Token 驗證失敗', data['message'])
        print("✅ 驗證無效 Token 時正確回傳 400")

    @patch('bot.validate_threads_token')
    def test_validate_account_token_success(self, mock_validate):
        """測試驗證帳號 Token 成功"""
        mock_validate.return_value = (True, 'Token 驗證成功', {'username': 'demo_user'})

        response = self.app.post('/api/accounts/validate-token',
                                 data=json.dumps({'accessToken': 'good_token'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['username'], 'demo_user')
        print("✅ 驗證 Token 成功")

    @patch('bot.validate_threads_token')
    def test_add_account_invalid_token(self, mock_validate):
        """測試新增帳號時攔截無效 Token"""
        mock_validate.return_value = (False, '權杖已過期', None)

        response = self.app.post('/api/accounts',
                                 data=json.dumps({
                                     'accountName': 'test_account',
                                     'accessToken': 'bad_token'
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('Token 驗證失敗', data['message'])
        print("✅ 新增帳號時會先驗證 Token")
    
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
    
    @patch('bot.validate_threads_token')
    @patch('bot.get_db_connection')
    def test_save_schedule_success(self, mock_db, mock_validate):
        """測試新增排程成功"""
        cursor = MockDBCursor([{'accessToken': 'good_token'}])
        mock_db.return_value = MockDBConnection(cursor)
        mock_validate.return_value = (True, 'Token 驗證成功', {'username': 'demo_user'})
        
        response = self.app.post('/api/schedule',
                                 data=json.dumps({
                                     'accountId': 1,
                                     'content': '測試內容',
                                     'scheduledAt': '2024-12-31T12:00:00Z',
                                     'timezoneOffsetMinutes': -480
                                   }),
                                  content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cursor.executed[-1][1][3], datetime(2024, 12, 31, 20, 0, 0))
        print("✅ 新增排程成功")

    @patch('bot.validate_threads_token')
    @patch('bot.get_db_connection')
    def test_save_schedule_uses_taipei_time(self, mock_db, mock_validate):
        """測試新增排程時可用台北本地時間送出"""
        cursor = MockDBCursor([{'accessToken': 'good_token'}])
        mock_db.return_value = MockDBConnection(cursor)
        mock_validate.return_value = (True, 'Token 驗證成功', {'username': 'demo_user'})

        response = self.app.post('/api/schedule',
                                 data=json.dumps({
                                     'accountId': 1,
                                     'content': '台北時間排程',
                                     'scheduledAt': '2026-03-14 10:00:17',
                                     'timezoneOffsetMinutes': TAIPEI_TZ_OFFSET
                                   }),
                                  content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cursor.executed[-1][1][3], datetime(2026, 3, 14, 10, 0, 17))
        print("✅ 台北時間排程會保留台北本地時間儲存")

    @patch('bot.validate_threads_token')
    @patch('bot.get_db_connection')
    def test_save_schedule_invalid_token(self, mock_db, mock_validate):
        """測試新增排程時攔截無效 Token"""
        cursor = MockDBCursor([{'accessToken': 'bad_token'}])
        mock_db.return_value = MockDBConnection(cursor)
        mock_validate.return_value = (False, '權杖已過期', None)

        response = self.app.post('/api/schedule',
                                 data=json.dumps({
                                     'accountId': 1,
                                     'content': '測試內容',
                                     'scheduledAt': '2024-12-31T12:00:00Z'
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('Token 無效', data['message'])
        print("✅ 新增排程時會先驗證帳號 Token")
    
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

    @staticmethod
    def _minimal_png():
        """生成最小有效 PNG 供測試使用 (1×1 白色像素)"""
        def chunk(name, data):
            c = struct.pack('>I', len(data)) + name + data
            return c + struct.pack('>I', zlib.crc32(name + data) & 0xffffffff)
        sig = b'\x89PNG\r\n\x1a\n'
        ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
        idat = chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff'))
        iend = chunk(b'IEND', b'')
        return sig + ihdr + idat + iend

    def test_upload_image_success_returns_file_url(self):
        """測試上傳圖片後回傳的是對應實際儲存檔案的 URL，而非固定示意圖"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('bot.UPLOAD_DIR', tmpdir):
                png_data = self._minimal_png()
                data = {'image': (io.BytesIO(png_data), 'test.png', 'image/png')}
                response = self.app.post('/api/upload', base_url='https://threads.example', data=data, content_type='multipart/form-data')
                self.assertEqual(response.status_code, 200)
                result = json.loads(response.data)
                self.assertIn('url', result)
                self.assertTrue(result['url'].startswith('https://threads.example/uploads/'))
                self.assertTrue(result['url'].endswith('.png'))
                self.assertNotIn('unsplash.com', result['url'])
                print("✅ 上傳圖片成功，回傳可供 Threads 使用的完整圖片 URL")

    @patch('bot.validate_threads_token', return_value=(True, 'ok', {'id': '123'}))
    @patch('bot.get_db_connection')
    def test_save_schedule_normalizes_relative_image_url(self, mock_db, mock_validate_token):
        """測試新增排程時會把相對圖片路徑轉成完整 URL"""
        cursor = MockDBCursor(results=[{'accessToken': 'valid-token'}])
        mock_db.return_value = MockDBConnection(cursor)

        response = self.app.post(
            '/api/schedule',
            base_url='https://threads.example',
            data=json.dumps({
                'accountId': 1,
                'content': '測試貼文',
                'imageUrl': '/uploads/test.png',
                'scheduledAt': '2026-03-14 09:44:11',
                'timezoneOffsetMinutes': TAIPEI_TZ_OFFSET,
            }),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        insert_query, insert_params = cursor.executed[-1]
        self.assertIn('INSERT INTO scheduled_posts', insert_query)
        self.assertEqual(insert_params[2], 'https://threads.example/uploads/test.png')
        print("✅ 新增排程時會儲存完整圖片 URL，避免 Threads 拒絕相對路徑")

    @patch('bot.requests.post')
    def test_post_to_threads_normalizes_relative_image_url_before_publish(self, mock_post):
        """測試發文前會把相對圖片路徑轉成合法 URI"""
        mock_post.side_effect = [
            MagicMock(json=MagicMock(return_value={'id': 'creation-123'})),
            MagicMock(json=MagicMock(return_value={'id': 'publish-456'})),
        ]

        with patch.dict(os.environ, {'PUBLIC_BASE_URL': 'https://threads.example'}, clear=False):
            success, result = post_to_threads('測試貼文', '/uploads/test.png', 'valid-token')

        self.assertTrue(success)
        self.assertEqual(result, 'publish-456')
        first_call = mock_post.call_args_list[0]
        self.assertEqual(first_call.kwargs['data']['image_url'], 'https://threads.example/uploads/test.png')
        self.assertEqual(first_call.kwargs['data']['media_type'], 'IMAGE')
        print("✅ 發文前會把相對圖片路徑轉成 Threads 可接受的完整 URI")

    def test_upload_missing_file_returns_400(self):
        """測試未提供檔案時回傳 400"""
        response = self.app.post('/api/upload', data={}, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.data)
        self.assertIn('message', result)
        print("✅ 未提供圖片時正確回傳 400")

    def test_upload_non_image_returns_400(self):
        """測試上傳非圖片檔案時回傳 400"""
        data = {'image': (io.BytesIO(b'not an image'), 'test.txt', 'text/plain')}
        response = self.app.post('/api/upload', data=data, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.data)
        self.assertIn('message', result)
        print("✅ 非圖片檔案被正確拒絕，回傳 400")

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


class TestSchedulingUtilities(unittest.TestCase):
    """測試排程時間工具"""

    def test_normalize_scheduled_at_keeps_taipei_local_time(self):
        """測試台北本地時間會維持台北時間儲存"""
        normalized = normalize_scheduled_at('2026-03-14 09:44:11', TAIPEI_TZ_OFFSET)
        self.assertEqual(normalized, datetime(2026, 3, 14, 9, 44, 11))
        print("✅ 台北本地時間會維持台北時間")

    def test_normalize_scheduled_at_converts_utc_to_taipei(self):
        """測試 UTC 時間輸入會轉成台北時間"""
        normalized = normalize_scheduled_at('2026-03-14T09:44:11Z', TAIPEI_TZ_OFFSET)
        self.assertEqual(normalized, datetime(2026, 3, 14, 17, 44, 11))
        print("✅ UTC 時間會正確轉成台北時間")


class TestContentSettingsAPI(unittest.TestCase):
    """測試文案設定 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_get_public_content_settings(self, mock_db):
        """測試公開取得文案設定成功"""
        cursor = MockDBCursor([
            {'settingKey': 'site_title', 'settingValue': 'AutoThreader'},
            {'settingKey': 'site_welcome', 'settingValue': 'Welcome'},
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.get('/api/content-settings')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('settings', data)
        print("✅ 公開取得文案設定成功")
    
    @patch('bot.get_db_connection')
    def test_update_content_settings_success(self, mock_db):
        """測試更新文案設定成功"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/content-settings',
                                 data=json.dumps({'settings': {
                                     'site_title': 'My AutoThreader',
                                     'site_welcome': '歡迎！'
                                 }}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        print("✅ 更新文案設定成功")
    
    @patch('bot.get_db_connection')
    def test_update_content_settings_invalid_format(self, mock_db):
        """測試文案設定格式錯誤時回傳 400"""
        cursor = MockDBCursor()
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/admin/content-settings',
                                 data=json.dumps({'settings': 'not_a_dict'}),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        print("✅ 文案設定格式錯誤時正確回傳 400")


class TestPublicPlansAndUSDTAPI(unittest.TestCase):
    """測試公開方案與 USDT 設定 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_get_public_plans(self, mock_db):
        """測試公開取得方案列表成功"""
        cursor = MockDBCursor([
            {'id': 1, 'planName': '免費方案', 'description': '基本方案', 'priceUsdt': 0,
             'monthlyPostLimit': 100, 'aiGenerationLimit': 10, 'features': '["基本發文"]'},
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.get('/api/plans')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('plans', data)
        print("✅ 公開取得方案列表成功")
    
    @patch('bot.get_db_connection')
    def test_get_public_usdt_settings(self, mock_db):
        """測試公開取得 USDT 設定成功"""
        cursor = MockDBCursor([
            {'walletAddress': 'TEST_ADDR', 'networkType': 'TRC20', 'minPaymentAmount': 10.00}
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.get('/api/usdt-settings')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('settings', data)
        print("✅ 公開取得 USDT 設定成功")


class TestPaymentNotifyAPI(unittest.TestCase):
    """測試付款通知 API"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    def test_notify_payment_not_found(self, mock_db):
        """測試付款記錄不存在時回傳 404"""
        cursor = MockDBCursor([])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/user/payment/999/notify')
        self.assertEqual(response.status_code, 404)
        print("✅ 付款記錄不存在時正確回傳 404")
    
    @patch('bot.get_db_connection')
    def test_notify_payment_success(self, mock_db):
        """測試通知付款成功"""
        cursor = MockDBCursor([
            {'id': 1, 'userId': 1, 'planId': 1, 'amountUsdt': 9.99, 'status': 'pending', 'notifiedAt': None}
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/user/payment/1/notify')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        print("✅ 通知付款成功")
    
    @patch('bot.get_db_connection')
    def test_notify_payment_already_confirmed(self, mock_db):
        """測試已確認的付款無法再次通知"""
        cursor = MockDBCursor([
            {'id': 1, 'userId': 1, 'planId': 1, 'amountUsdt': 9.99, 'status': 'confirmed', 'notifiedAt': None}
        ])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/user/payment/1/notify')
        self.assertEqual(response.status_code, 400)
        print("✅ 已確認付款無法再次通知，正確回傳 400")
    
    @patch('bot.get_db_connection')
    def test_get_admin_notifications(self, mock_db):
        """測試取得後台通知成功"""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = {'cnt': 0}
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        
        response = self.app.get('/api/admin/notifications')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('notifications', data)
        self.assertIn('count', data)
        print("✅ 取得後台通知成功")


class TestFirstCommentSchedule(unittest.TestCase):
    """測試第一則留言排程功能"""
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
    
    @patch('bot.get_db_connection')
    @patch('bot.validate_threads_token')
    def test_schedule_with_first_comment(self, mock_validate, mock_db):
        """測試排程時可設定第一則留言"""
        mock_validate.return_value = (True, 'Token 有效', {'username': 'test_user'})
        cursor = MockDBCursor([{'accessToken': 'test_token_valid'}])
        mock_db.return_value = MockDBConnection(cursor)
        
        response = self.app.post('/api/schedule',
                                 data=json.dumps({
                                     'accountId': 1,
                                     'content': '測試貼文',
                                     'scheduledAt': '2030-01-01 12:00:00',
                                     'firstComment': '這是第一則留言',
                                     'firstCommentPinned': True
                                 }),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        # 驗證 INSERT 語句包含 firstComment 欄位
        insert_queries = [q for q, _ in cursor.executed if 'firstComment' in q]
        self.assertTrue(len(insert_queries) > 0, "排程 INSERT 應包含 firstComment 欄位")
        print("✅ 排程時可設定第一則留言")


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
        TestSchedulingUtilities,
        TestContentSettingsAPI,
        TestPublicPlansAndUSDTAPI,
        TestPaymentNotifyAPI,
        TestFirstCommentSchedule,
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
