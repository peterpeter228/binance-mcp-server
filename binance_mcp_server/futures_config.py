"""
Binance Futures Configuration and Client Management.

This module provides configuration and HTTP client management for Binance USDⓈ-M Futures API,
supporting both production and testnet environments with proper signature handling.
"""

import os
import time
import hmac
import hashlib
import logging
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlencode
import requests

logger = logging.getLogger(__name__)


# Allowed symbols for this implementation (hardcoded allowlist)
ALLOWED_FUTURES_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


class FuturesConfig:
    """Configuration management for Binance Futures MCP Server."""
    
    # Production URLs
    FUTURES_BASE_URL = "https://fapi.binance.com"
    FUTURES_WS_URL = "wss://fstream.binance.com"
    
    # Testnet URLs
    FUTURES_TESTNET_BASE_URL = "https://testnet.binancefuture.com"
    FUTURES_TESTNET_WS_URL = "wss://stream.binancefuture.com"
    
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        self.testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
        self.recv_window = int(os.getenv("BINANCE_RECV_WINDOW", "5000"))
        
        # Server time offset for clock synchronization
        self._server_time_offset: int = 0
        self._last_sync_time: float = 0
    
    @property
    def base_url(self) -> str:
        """Get appropriate base URL based on testnet setting."""
        if self.testnet:
            return self.FUTURES_TESTNET_BASE_URL
        return self.FUTURES_BASE_URL
    
    @property
    def ws_url(self) -> str:
        """Get appropriate WebSocket URL based on testnet setting."""
        if self.testnet:
            return self.FUTURES_TESTNET_WS_URL
        return self.FUTURES_WS_URL
    
    def is_valid(self) -> bool:
        """Check if configuration is valid."""
        return bool(self.api_key and self.api_secret)
    
    def get_validation_errors(self) -> list:
        """Get list of configuration validation errors."""
        errors = []
        if not self.api_key:
            errors.append("BINANCE_API_KEY environment variable is required")
        if not self.api_secret:
            errors.append("BINANCE_API_SECRET environment variable is required")
        return errors
    
    def sync_server_time(self) -> int:
        """
        Synchronize with Binance server time.
        
        Returns:
            int: Server time offset in milliseconds
        """
        try:
            response = requests.get(f"{self.base_url}/fapi/v1/time", timeout=5)
            response.raise_for_status()
            server_time = response.json().get("serverTime", 0)
            local_time = int(time.time() * 1000)
            self._server_time_offset = server_time - local_time
            self._last_sync_time = time.time()
            logger.info(f"Server time synced. Offset: {self._server_time_offset}ms")
            return self._server_time_offset
        except Exception as e:
            logger.warning(f"Failed to sync server time: {e}")
            return 0
    
    def get_timestamp(self) -> int:
        """
        Get current timestamp adjusted for server time offset.
        
        Returns:
            int: Adjusted timestamp in milliseconds
        """
        # Resync if more than 5 minutes since last sync
        if time.time() - self._last_sync_time > 300:
            self.sync_server_time()
        
        return int(time.time() * 1000) + self._server_time_offset


# Global futures configuration instance
_futures_config: Optional[FuturesConfig] = None


def get_futures_config() -> FuturesConfig:
    """
    Get the global FuturesConfig instance.
    
    Returns:
        FuturesConfig: The configuration instance
        
    Raises:
        RuntimeError: If configuration is not initialized or invalid
    """
    global _futures_config
    
    if _futures_config is None:
        _futures_config = FuturesConfig()
    
    if not _futures_config.is_valid():
        error_msg = "Invalid Binance Futures configuration: " + ", ".join(_futures_config.get_validation_errors())
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    
    return _futures_config


def create_signature(secret: str, query_string: str) -> str:
    """
    Create HMAC SHA256 signature for Binance API.
    
    Args:
        secret: API secret key
        query_string: Query string to sign
        
    Returns:
        str: Hexadecimal signature
    """
    return hmac.new(
        secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


class FuturesClient:
    """
    HTTP client for Binance USDⓈ-M Futures API with automatic signing and retry.
    """
    
    def __init__(self, config: Optional[FuturesConfig] = None):
        self.config = config or get_futures_config()
        self.session = requests.Session()
        self.session.headers.update({
            "X-MBX-APIKEY": self.config.api_key,
            "Content-Type": "application/x-www-form-urlencoded"
        })
    
    def _sign_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add timestamp and signature to request parameters.
        
        Args:
            params: Request parameters
            
        Returns:
            Dict with timestamp and signature added
        """
        params = params.copy()
        params["timestamp"] = self.config.get_timestamp()
        params["recvWindow"] = self.config.recv_window
        
        query_string = urlencode(params)
        params["signature"] = create_signature(self.config.api_secret, query_string)
        
        return params
    
    def _handle_response(self, response: requests.Response) -> Tuple[bool, Any]:
        """
        Handle API response with error parsing.
        
        Args:
            response: HTTP response object
            
        Returns:
            Tuple of (success, data_or_error)
        """
        try:
            data = response.json()
        except ValueError:
            data = {"msg": response.text}
        
        if response.status_code == 200:
            return True, data
        
        # Extract error code and message
        error_code = data.get("code", response.status_code)
        error_msg = data.get("msg", str(data))
        
        return False, {
            "code": error_code,
            "message": error_msg,
            "raw": data
        }
    
    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        retry_on_time_error: bool = True
    ) -> Tuple[bool, Any]:
        """
        Make an API request with optional signing and retry logic.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (e.g., /fapi/v1/order)
            params: Request parameters
            signed: Whether to sign the request
            retry_on_time_error: Whether to retry on timestamp error (-1021)
            
        Returns:
            Tuple of (success, data_or_error)
        """
        url = f"{self.config.base_url}{endpoint}"
        params = params or {}
        
        # Sign request if needed
        if signed:
            params = self._sign_request(params)
        
        try:
            if method.upper() == "GET":
                response = self.session.get(url, params=params, timeout=10)
            elif method.upper() == "POST":
                response = self.session.post(url, data=params, timeout=10)
            elif method.upper() == "PUT":
                response = self.session.put(url, data=params, timeout=10)
            elif method.upper() == "DELETE":
                response = self.session.delete(url, params=params, timeout=10)
            else:
                return False, {"code": -1, "message": f"Unsupported HTTP method: {method}"}
            
            success, result = self._handle_response(response)
            
            # Handle timestamp error with retry
            if not success and retry_on_time_error:
                error_code = result.get("code")
                if error_code == -1021:  # Timestamp outside recvWindow
                    logger.warning("Timestamp error detected, resyncing server time...")
                    self.config.sync_server_time()
                    
                    # Retry with new timestamp
                    if signed:
                        params = self._sign_request({k: v for k, v in params.items() 
                                                    if k not in ("timestamp", "signature", "recvWindow")})
                    
                    if method.upper() == "GET":
                        response = self.session.get(url, params=params, timeout=10)
                    elif method.upper() == "POST":
                        response = self.session.post(url, data=params, timeout=10)
                    elif method.upper() == "PUT":
                        response = self.session.put(url, data=params, timeout=10)
                    elif method.upper() == "DELETE":
                        response = self.session.delete(url, params=params, timeout=10)
                    
                    return self._handle_response(response)
            
            return success, result
            
        except requests.exceptions.Timeout:
            return False, {"code": -1001, "message": "Request timeout"}
        except requests.exceptions.ConnectionError:
            return False, {"code": -1002, "message": "Connection error"}
        except Exception as e:
            return False, {"code": -1, "message": str(e)}
    
    def get(self, endpoint: str, params: Optional[Dict] = None, signed: bool = False) -> Tuple[bool, Any]:
        """Make a GET request."""
        return self.request("GET", endpoint, params, signed)
    
    def post(self, endpoint: str, params: Optional[Dict] = None, signed: bool = True) -> Tuple[bool, Any]:
        """Make a POST request (signed by default)."""
        return self.request("POST", endpoint, params, signed)
    
    def put(self, endpoint: str, params: Optional[Dict] = None, signed: bool = True) -> Tuple[bool, Any]:
        """Make a PUT request (signed by default)."""
        return self.request("PUT", endpoint, params, signed)
    
    def delete(self, endpoint: str, params: Optional[Dict] = None, signed: bool = True) -> Tuple[bool, Any]:
        """Make a DELETE request (signed by default)."""
        return self.request("DELETE", endpoint, params, signed)


# Global client instance
_futures_client: Optional[FuturesClient] = None


def get_futures_client() -> FuturesClient:
    """
    Get or create the global FuturesClient instance.
    
    Returns:
        FuturesClient: Configured HTTP client for futures API
    """
    global _futures_client
    
    if _futures_client is None:
        _futures_client = FuturesClient()
    
    return _futures_client
