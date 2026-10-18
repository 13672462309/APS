import requests
import time
import threading
from logging_config import get_logger


class HttpApiClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def get(self, path, params=None, headers=None):
        """
        执行HTTP GET请求
        :param path: 请求的路径
        :param params: 查询参数（可选）
        :param headers: 请求头（可选）
        :return: 包含状态和数据的字典
        """
        url = f"{self.base_url}{path}"
        try:
            response = requests.get(url, params=params, headers=headers)
            if response.status_code == 200:
                return {
                    "success": True,
                    "data": response.json(),
                    "status_code": response.status_code
                }
            else:
                return {
                    "success": False,
                    "error": response.text,
                    "status_code": response.status_code
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "status_code": None
            }

    def post(self, path, data=None, json=None, headers=None):
        """
        执行HTTP POST请求
        :param path: 请求的路径
        :param data: 表单数据（可选）
        :param json: JSON数据（可选）
        :param headers: 请求头（可选）
        :return: 包含状态和数据的字典
        """
        url = f"{self.base_url}{path}"
        try:
            response = requests.post(url, data=data, json=json, headers=headers)
            if response.status_code == 200:
                return {
                    "success": True,
                    "data": response.json(),
                    "status_code": response.status_code
                }
            else:
                return {
                    "success": False,
                    "error": response.text,
                    "status_code": response.status_code
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "status_code": None
            }


class HpaasApiClient:
    """HPAAS 开放接口客户端：自动管理 OAuth2 client_credentials token"""

    def __init__(self, token_url, client_id, client_secret):
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = None
        self._token_expires_at = 0
        self._lock = threading.Lock()
        self._logger = get_logger()

    def _fetch_token(self):
        """向 HPAAS OAuth 接口获取 access_token"""
        try:
            resp = requests.post(self._token_url, data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            }, timeout=15)
            resp.raise_for_status()
            body = resp.json()
            self._access_token = body["access_token"]
            expires_in = int(body.get("expires_in", 3600))
            self._token_expires_at = time.time() + expires_in - 60
            self._logger.info(f"HPAAS token 获取成功, expires_in={expires_in}s")
        except Exception as e:
            self._logger.error(f"HPAAS token 获取失败: {e}")
            raise

    def _ensure_token(self):
        with self._lock:
            if self._access_token is None or time.time() >= self._token_expires_at:
                self._fetch_token()
        return self._access_token

    def post_json(self, url, payload, timeout=30):
        """带 Bearer token 的 POST JSON 请求，token 过期自动重试一次"""
        for attempt in range(2):
            token = self._ensure_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
                if resp.status_code == 401 and attempt == 0:
                    self._logger.warning("HPAAS 返回 401，刷新 token 后重试")
                    with self._lock:
                        self._access_token = None
                    continue
                return {
                    "success": resp.status_code == 200,
                    "status_code": resp.status_code,
                    "data": resp.json() if resp.status_code == 200 else None,
                    "error": resp.text if resp.status_code != 200 else None,
                }
            except Exception as e:
                self._logger.error(f"HPAAS POST 请求异常: {e}")
                return {"success": False, "status_code": None, "error": str(e)}
        return {"success": False, "status_code": 401, "error": "token refresh failed"}