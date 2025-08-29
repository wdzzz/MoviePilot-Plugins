"""
NodeSeek论坛签到插件
版本: 1.1.0
作者: Madrays
功能:
- 自动完成NodeSeek论坛每日签到
- 支持选择随机奖励或固定奖励
- 自动失败重试机制
- 定时签到和历史记录
- 支持绕过CloudFlare防护
"""
import time
import random
import traceback
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType
import requests
from urllib.parse import urlencode

# cloudscraper 作为 Cloudflare 备用方案
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except Exception:
    HAS_CLOUDSCRAPER = False

# 尝试导入curl_cffi库，用于绕过CloudFlare防护
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False


class nodeseeksign(_PluginBase):
    # 插件名称
    plugin_name = "NodeSeek论坛签到"
    # 插件描述
    plugin_desc = "懒羊羊定制：自动完成NodeSeek论坛每日签到，支持随机奖励和自动重试功能"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/madrays/MoviePilot-Plugins/main/icons/nodeseeksign.png"
    # 插件版本
    plugin_version = "1.3.0"
    # 插件作者
    plugin_author = "madrays"
    # 作者主页
    author_url = "https://github.com/madrays"
    # 插件配置项ID前缀
    plugin_config_prefix = "nodeseeksign_"
    # 加载顺序
    plugin_order = 1
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    _cookie = None
    _notify = False
    _onlyonce = False
    _cron = None
    _random_choice = True  # 是否选择随机奖励，否则选择固定奖励
    _history_days = 30  # 历史保留天数
    _use_proxy = True     # 是否使用代理，默认启用
    _max_retries = 3      # 最大重试次数
    _retry_count = 0      # 当天重试计数
    _scheduled_retry = None  # 计划的重试任务
    _verify_ssl = False    # 是否验证SSL证书，默认禁用
    _min_delay = 5         # 请求前最小随机等待（秒）
    _max_delay = 12        # 请求前最大随机等待（秒）
    _member_id = ""       # NodeSeek 成员ID（可选，用于获取用户信息）

    _scraper = None        # cloudscraper 实例

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None
    _manual_trigger = False

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        logger.info("============= nodeseeksign 初始化 =============")
        try:
            if config:
                self._enabled = config.get("enabled")
                self._cookie = config.get("cookie")
                self._notify = config.get("notify")
                self._cron = config.get("cron")
                self._onlyonce = config.get("onlyonce")
                self._random_choice = config.get("random_choice")
                self._history_days = int(config.get("history_days", 30))
                self._use_proxy = config.get("use_proxy", True)
                self._max_retries = int(config.get("max_retries", 3))
                self._verify_ssl = config.get("verify_ssl", False)
                self._min_delay = int(config.get("min_delay", 5))
                self._max_delay = int(config.get("max_delay", 12))
                self._member_id = (config.get("member_id") or "").strip()
                
                logger.info(f"配置: enabled={self._enabled}, notify={self._notify}, cron={self._cron}, "
                           f"random_choice={self._random_choice}, history_days={self._history_days}, "
                           f"use_proxy={self._use_proxy}, max_retries={self._max_retries}, verify_ssl={self._verify_ssl}, "
                           f"min_delay={self._min_delay}, max_delay={self._max_delay}, member_id={self._member_id or '未设置'}")
                # 初始化 cloudscraper（可选，用于绕过 Cloudflare）
                if HAS_CLOUDSCRAPER:
                    try:
                        # 简化初始化，兼容不同 cloudscraper 版本
                        self._scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
                        # 应用代理
                        proxies = self._get_proxies()
                        if proxies:
                            self._scraper.proxies = proxies
                            logger.info(f"cloudscraper 初始化代理: {self._scraper.proxies}")
                        logger.info("cloudscraper 初始化成功")
                    except Exception as e:
                        logger.warning(f"cloudscraper 初始化失败: {str(e)}")
            
            if self._onlyonce:
                logger.info("执行一次性签到")
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._manual_trigger = True
                self._scheduler.add_job(func=self.sign, trigger='date',
                                   run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                   name="NodeSeek论坛签到")
                self._onlyonce = False
                self.update_config({
                    "onlyonce": False,
                    "enabled": self._enabled,
                    "cookie": self._cookie,
                    "notify": self._notify,
                    "cron": self._cron,
                    "random_choice": self._random_choice,
                    "history_days": self._history_days,
                    "use_proxy": self._use_proxy,
                    "max_retries": self._max_retries,
                    "verify_ssl": self._verify_ssl,
                    "min_delay": self._min_delay,
                    "max_delay": self._max_delay,
                    "member_id": self._member_id
                })

                # 启动任务
                if self._scheduler.get_jobs():
                    self._scheduler.print_jobs()
                    self._scheduler.start()

        except Exception as e:
            logger.error(f"nodeseeksign初始化错误: {str(e)}", exc_info=True)

    def sign(self):
        """
        执行NodeSeek签到
        """
        logger.info("============= 开始NodeSeek签到 =============")
        sign_dict = None
        
        try:
            # 检查是否今日已成功签到（通过记录）
            if self._is_already_signed_today():
                logger.info("根据历史记录，今日已成功签到，跳过本次执行")
                
                # 创建跳过记录
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "跳过: 今日已签到",
                }
                
                # 发送通知
                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="【NodeSeek论坛重复签到】",
                        text=f"今日已完成签到，跳过执行\n⏱️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                
                return sign_dict
            
            # 检查Cookie
            if not self._cookie:
                logger.error("未配置Cookie")
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "签到失败: 未配置Cookie",
                }
                self._save_sign_history(sign_dict)
                
                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="【NodeSeek论坛签到失败】",
                        text="未配置Cookie，请在设置中添加Cookie"
                    )
                return sign_dict
            
            # 请求前随机等待
            self._wait_random_interval()

            # 执行API签到
            result = self._run_api_sign()
            
            # 处理签到结果
            if result["success"]:
                # 保存签到记录
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "签到成功" if not result.get("already_signed") else "已签到",
                    "message": result.get("message", "")
                }
                self._save_sign_history(sign_dict)
                self._save_last_sign_date()
                # 重置重试计数
                self._retry_count = 0
                
                # 获取用户信息（有成员ID就拉取）
                user_info = None
                try:
                    if getattr(self, "_member_id", ""):
                        user_info = self._fetch_user_info(self._member_id)
                except Exception as e:
                    logger.warning(f"获取用户信息失败: {str(e)}")

                # 发送通知
                if self._notify:
                    self._send_sign_notification(sign_dict, result, user_info)
            else:
                # 签到失败，安排重试
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "签到失败",
                    "message": result.get("message", "")
                }
                self._save_sign_history(sign_dict)
                
                # 检查是否需要重试
                if self._max_retries and self._retry_count < self._max_retries:
                    self._retry_count += 1
                    retry_minutes = random.randint(5, 15)
                    retry_time = datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(minutes=retry_minutes)
                    
                    logger.info(f"签到失败，将在 {retry_minutes} 分钟后重试 (重试 {self._retry_count}/{self._max_retries})")
                    
                    # 安排重试任务
                    if not self._scheduler:
                        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                        if not self._scheduler.running:
                            self._scheduler.start()
                    
                    # 移除之前计划的重试任务（如果有）
                    if self._scheduled_retry:
                        try:
                            self._scheduler.remove_job(self._scheduled_retry)
                        except Exception as e:
                            # 忽略移除不存在任务的错误
                            logger.warning(f"移除旧任务时出错 (可忽略): {str(e)}")
                    
                    # 添加新的重试任务
                    self._scheduled_retry = f"nodeseek_retry_{int(time.time())}"
                    self._scheduler.add_job(
                        func=self.sign,
                        trigger='date',
                        run_date=retry_time,
                        id=self._scheduled_retry,
                        name=f"NodeSeek论坛签到重试 {self._retry_count}/{self._max_retries}"
                    )
                    
                    if self._notify:
                        self.post_message(
                            mtype=NotificationType.SiteMessage,
                            title="【NodeSeek论坛签到失败】",
                            text=f"签到失败: {result.get('message', '未知错误')}\n将在 {retry_minutes} 分钟后进行第 {self._retry_count}/{self._max_retries} 次重试\n⏱️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                else:
                    # 达到最大重试次数，不再重试
                    if self._max_retries == 0:
                        logger.info("未配置自动重试 (max_retries=0)，本次结束")
                    else:
                        logger.warning(f"已达到最大重试次数 ({self._max_retries})，今日不再重试")
                    
                    if self._notify:
                        self.post_message(
                            mtype=NotificationType.SiteMessage,
                            title="【NodeSeek论坛签到失败】",
                            text=(
                                f"签到失败: {result.get('message', '未知错误')}\n"
                                + ("未配置自动重试 (max_retries=0)，本次结束\n" if self._max_retries == 0 else f"已达到最大重试次数 ({self._max_retries})，今日不再重试\n")
                                + f"⏱️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            )
                        )
            
            return sign_dict
        
        except Exception as e:
            logger.error(f"NodeSeek签到过程中出错: {str(e)}", exc_info=True)
            sign_dict = {
                "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                "status": f"签到出错: {str(e)}",
            }
            self._save_sign_history(sign_dict)
            
            if self._notify:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【NodeSeek论坛签到出错】",
                    text=f"签到过程中出错: {str(e)}\n⏱️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            
            return sign_dict
    
    def _run_api_sign(self):
        """
        使用API执行NodeSeek签到
        """
        try:
            logger.info("使用API执行NodeSeek签到...")
            
            # 初始化结果字典
            result = {
                "success": False,
                "signed": False,
                "already_signed": False,
                "message": ""
            }
            
            # 准备请求头
            headers = {
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate, br, zstd',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Content-Length': '0',
                'Origin': 'https://www.nodeseek.com',
                'Referer': 'https://www.nodeseek.com/board',
                'Sec-CH-UA': '"Chromium";v="136", "Not:A-Brand";v="24", "Google Chrome";v="136"',
                'Sec-CH-UA-Mobile': '?0',
                'Sec-CH-UA-Platform': '"Windows"',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
                'Cookie': self._cookie
            }
            
            # 构建签到URL，根据配置决定是否使用随机奖励
            random_param = "true" if self._random_choice else "false"
            url = f"https://www.nodeseek.com/api/attendance?random={random_param}"
            
            # 获取代理设置
            proxies = self._get_proxies()
            
            # 输出调试信息
            if proxies:
                logger.info(f"使用代理: {proxies}")
            
            logger.info(f"执行签到请求: {url}")
            
            # 通过统一请求适配层发送请求（优先 curl_cffi -> cloudscraper -> requests）
            response = self._smart_post(url=url, headers=headers, data=b'', proxies=proxies, timeout=30)
            
            # 解析响应（无论状态码是否200，先尝试读取JSON，按 message 判定）
            try:
                response_data = response.json()
                logger.info(f"签到响应: {response_data}")
                message = response_data.get('message', '')
                # 判断签到结果（优先以业务语义为准）
                if "鸡腿" in message or response_data.get('success') is True:
                    result["success"] = True
                    result["signed"] = True
                    result["message"] = message
                    logger.info(f"签到成功: {message}")
                elif "已完成签到" in message:
                    result["success"] = True
                    result["already_signed"] = True
                    result["message"] = message
                    logger.info(f"今日已签到: {message}")
                elif message == "USER NOT FOUND" or response_data.get('status') == 404:
                    result["message"] = "Cookie已失效，请更新"
                    logger.error("Cookie已失效，请更新")
                else:
                    result["message"] = message or f"请求失败，状态码: {response.status_code}"
                    # 若非200则仍记录状态码，便于排查
                    if response.status_code != 200:
                        logger.error(f"签到请求非200({response.status_code}): {message}")
            except ValueError:
                # 非JSON响应
                if response.status_code == 200:
                    result["message"] = f"解析响应失败: {response.text[:100]}..."
                else:
                    result["message"] = f"请求失败，状态码: {response.status_code}"
                logger.error(f"签到响应非JSON({response.status_code}): {response.text[:100]}...")

            # 去除额外CloudFlare提示（全局处理，无需重复提示）
                # 404/403 时对代理与直连互相回退一次
                try:
                    if response.status_code in (403, 404):
                        if proxies:
                            logger.info("检测到 403/404，尝试去代理直连回退一次...")
                            response_retry = self._smart_post(url=url, headers=headers, proxies=None, timeout=30)
                        else:
                            logger.info("检测到 403/404，尝试走代理回退一次...")
                            alt_proxies = self._get_proxies()
                            response_retry = self._smart_post(url=url, headers=headers, proxies=alt_proxies, timeout=30)
                        if response_retry and response_retry.status_code == 200:
                            response_data = response_retry.json()
                            message = response_data.get('message', '')
                            if "鸡腿" in message or response_data.get('success') == True:
                                result.update({"success": True, "signed": True, "message": message})
                            elif "已完成签到" in message:
                                result.update({"success": True, "already_signed": True, "message": message})
                            else:
                                result["message"] = f"回退后仍失败: {message}"
                except Exception as e:
                    logger.warning(f"回退请求失败（忽略）：{str(e)}")
            
            return result
            
        except Exception as e:
            logger.error(f"API签到出错: {str(e)}", exc_info=True)
            return {
                "success": False,
                "message": f"API签到出错: {str(e)}"
            }
    
    def _get_proxies(self):
        """
        获取代理设置
        """
        if not self._use_proxy:
            logger.info("未启用代理")
            return None
        try:
            if hasattr(settings, 'PROXY') and settings.PROXY:
                norm = self._normalize_proxies(settings.PROXY)
                if norm:
                    return norm
            logger.warning("系统代理未配置或无效")
            return None
        except Exception as e:
            logger.error(f"获取代理设置出错: {str(e)}")
            return None

    def _normalize_proxies(self, proxies_input):
        """
        归一化代理配置为 requests 兼容格式 {"http": url, "https": url}
        支持字符串或字典输入。
        """
        try:
            if not proxies_input:
                return None
            if isinstance(proxies_input, str):
                return {"http": proxies_input, "https": proxies_input}
            if isinstance(proxies_input, dict):
                http_url = proxies_input.get("http") or proxies_input.get("HTTP") or proxies_input.get("https") or proxies_input.get("HTTPS")
                https_url = proxies_input.get("https") or proxies_input.get("HTTPS") or proxies_input.get("http") or proxies_input.get("HTTP")
                if not http_url and not https_url:
                    return None
                return {"http": http_url or https_url, "https": https_url or http_url}
        except Exception as e:
            logger.warning(f"代理归一化失败，将忽略代理: {str(e)}")
        return None
    def _wait_random_interval(self):
        """
        在请求前随机等待，模拟人类行为
        """
        try:
            if self._max_delay and self._min_delay and self._max_delay >= self._min_delay:
                delay = random.uniform(float(self._min_delay), float(self._max_delay))
                logger.info(f"请求前随机等待 {delay:.2f} 秒...")
                time.sleep(delay)
        except Exception as e:
            logger.debug(f"随机等待失败（忽略）：{str(e)}")

    def _smart_post(self, url, headers=None, data=None, json=None, proxies=None, timeout=30):
        """
        统一的POST请求适配器：
        1) curl_cffi (impersonate Chrome)
        2) cloudscraper
        3) requests
        """
        last_error = None

        # 1) cloudscraper 优先（与示例一致）
        if HAS_CLOUDSCRAPER and self._scraper:
            try:
                logger.info("使用 cloudscraper 发送请求")
                if proxies:
                    self._scraper.proxies = self._normalize_proxies(proxies) or {}
                    if self._scraper.proxies:
                        logger.info(f"cloudscraper 已应用代理: {self._scraper.proxies}")
                if self._verify_ssl:
                    return self._scraper.post(url, headers=headers, data=data, json=json, timeout=timeout, verify=True)
                return self._scraper.post(url, headers=headers, data=data, json=json, timeout=timeout)
            except Exception as e:
                last_error = e
                logger.warning(f"cloudscraper 请求失败，将回退：{str(e)}")

        # 2) curl_cffi 次选
        if HAS_CURL_CFFI:
            try:
                logger.info("使用 curl_cffi 发送请求 (Chrome-124 仿真)")
                session = curl_requests.Session(impersonate="chrome124")
                if proxies:
                    session.proxies = self._normalize_proxies(proxies) or {}
                    if session.proxies:
                        logger.info(f"curl_cffi 已应用代理: {session.proxies}")
                if self._verify_ssl:
                    return session.post(url, headers=headers, data=data, json=json, timeout=timeout, verify=True)
                return session.post(url, headers=headers, data=data, json=json, timeout=timeout)
            except Exception as e:
                last_error = e
                logger.warning(f"curl_cffi 请求失败，将回退：{str(e)}")

        # 3) requests 兜底
        try:
            logger.info("使用 requests 发送请求")
            norm = self._normalize_proxies(proxies)
            if norm:
                logger.info(f"requests 已应用代理: {norm}")
            if self._verify_ssl:
                return requests.post(url, headers=headers, data=data, json=json, proxies=norm, timeout=timeout, verify=True)
            return requests.post(url, headers=headers, data=data, json=json, proxies=norm, timeout=timeout)
        except Exception as e:
            logger.error(f"requests 请求失败：{str(e)}")
            if last_error:
                logger.error(f"此前错误：{str(last_error)}")
            raise

    def _smart_get(self, url, headers=None, proxies=None, timeout=30):
        """
        统一的GET请求适配器（顺序同 _smart_post）
        """
        last_error = None
        if HAS_CURL_CFFI:
            try:
                session = curl_requests.Session(impersonate="chrome124")
                if proxies:
                    session.proxies = self._normalize_proxies(proxies) or {}
                    if session.proxies:
                        logger.info(f"curl_cffi 已应用代理: {session.proxies}")
                if self._verify_ssl:
                    return session.get(url, headers=headers, timeout=timeout, verify=True)
                return session.get(url, headers=headers, timeout=timeout)
            except Exception as e:
                last_error = e
                logger.warning(f"curl_cffi GET 失败，将回退：{str(e)}")
        if HAS_CLOUDSCRAPER and self._scraper:
            try:
                if proxies:
                    self._scraper.proxies = self._normalize_proxies(proxies) or {}
                    if self._scraper.proxies:
                        logger.info(f"cloudscraper 已应用代理: {self._scraper.proxies}")
                if self._verify_ssl:
                    return self._scraper.get(url, headers=headers, timeout=timeout, verify=True)
                return self._scraper.get(url, headers=headers, timeout=timeout)
            except Exception as e:
                last_error = e
                logger.warning(f"cloudscraper GET 失败，将回退：{str(e)}")
        try:
            norm = self._normalize_proxies(proxies)
            if norm:
                logger.info(f"requests 已应用代理: {norm}")
            if self._verify_ssl:
                return requests.get(url, headers=headers, proxies=norm, timeout=timeout, verify=True)
            return requests.get(url, headers=headers, proxies=norm, timeout=timeout)
        except Exception as e:
            logger.error(f"requests GET 失败：{str(e)}")
            if last_error:
                logger.error(f"此前错误：{str(last_error)}")
            raise

    def _fetch_user_info(self, member_id: str) -> dict:
        """
        拉取 NodeSeek 用户信息（可选）
        """
        if not member_id:
            return {}
        url = f"https://www.nodeseek.com/api/account/getInfo/{member_id}?readme=1"
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.nodeseek.com",
            "Referer": f"https://www.nodeseek.com/space/{member_id}",
            "Sec-CH-UA": '"Chromium";v="136", "Not:A-Brand";v="24", "Google Chrome";v="136"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        }
        proxies = self._get_proxies()
        resp = self._smart_get(url=url, headers=headers, proxies=proxies, timeout=30)
        try:
            data = resp.json()
            detail = data.get("detail") or {}
            if detail:
                self.save_data('last_user_info', detail)
            return detail
        except Exception:
            return {}
            
        try:
            # 获取系统代理设置
            if hasattr(settings, 'PROXY') and settings.PROXY:
                logger.info(f"使用系统代理: {settings.PROXY}")
                return settings.PROXY
            else:
                logger.warning("系统代理未配置")
                return None
        except Exception as e:
            logger.error(f"获取代理设置出错: {str(e)}")
            return None

    def _save_sign_history(self, sign_data):
        """
        保存签到历史记录
        """
        try:
            # 读取现有历史
            history = self.get_data('sign_history') or []
            
            # 确保日期格式正确
            if "date" not in sign_data:
                sign_data["date"] = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
                
            history.append(sign_data)
            
            # 清理旧记录
            retention_days = int(self._history_days)
            now = datetime.now()
            valid_history = []
            
            for record in history:
                try:
                    # 尝试将记录日期转换为datetime对象
                    record_date = datetime.strptime(record["date"], '%Y-%m-%d %H:%M:%S')
                    # 检查是否在保留期内
                    if (now - record_date).days < retention_days:
                        valid_history.append(record)
                except (ValueError, KeyError):
                    # 如果记录日期格式不正确，尝试修复
                    logger.warning(f"历史记录日期格式无效: {record.get('date', '无日期')}")
                    # 添加新的日期并保留记录
                    record["date"] = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
                    valid_history.append(record)
            
            # 保存历史
            self.save_data(key="sign_history", value=valid_history)
            logger.info(f"保存签到历史记录，当前共有 {len(valid_history)} 条记录")
            
        except Exception as e:
            logger.error(f"保存签到历史记录失败: {str(e)}", exc_info=True)

    def _send_sign_notification(self, sign_dict, result, user_info: dict = None):
        """
        发送签到通知
        """
        if not self._notify:
            return
            
        status = sign_dict.get("status", "未知")
        sign_time = sign_dict.get("date", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        # 构建通知文本
        if "签到成功" in status:
            title = "【✅ NodeSeek论坛签到成功】"
            
            text = (
                f"📢 执行结果\n"
                f"━━━━━━━━━━\n"
                f"🕐 时间：{sign_time}\n"
                f"✨ 状态：{status}\n"
                + (f"👤 用户：{user_info.get('member_name')}  等级：{user_info.get('rank')}  鸡腿：{user_info.get('coin')}\n" if user_info else "") +
                f"━━━━━━━━━━"
            )
            
        elif "已签到" in status:
            title = "【ℹ️ NodeSeek论坛重复签到】"
            
            text = (
                f"📢 执行结果\n"
                f"━━━━━━━━━━\n"
                f"🕐 时间：{sign_time}\n"
                f"✨ 状态：{status}\n"
                + (f"👤 用户：{user_info.get('member_name')}  等级：{user_info.get('rank')}  鸡腿：{user_info.get('coin')}\n" if user_info else "") +
                f"ℹ️ 说明：今日已完成签到\n"
                f"━━━━━━━━━━"
            )
            
        else:
            title = "【❌ NodeSeek论坛签到失败】"
            text = (
                f"📢 执行结果\n"
                f"━━━━━━━━━━\n"
                f"🕐 时间：{sign_time}\n"
                f"❌ 状态：{status}\n"
                f"━━━━━━━━━━\n"
                f"💡 可能的解决方法\n"
                f"• 检查Cookie是否过期\n"
                f"• 确认站点是否可访问\n"
                f"• 检查代理设置是否正确\n"
                f"• 尝试手动登录网站\n"
                f"━━━━━━━━━━"
            )
            
        # 发送通知
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title=title,
            text=text
        )
    
    def _save_last_sign_date(self):
        """
        保存最后一次成功签到的日期和时间
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.save_data('last_sign_date', now)
        logger.info(f"记录签到成功时间: {now}")
        
    def _is_already_signed_today(self):
        """
        检查今天是否已经成功签到过
        只有当今天已经成功签到时才返回True
        """
        today = datetime.now().strftime('%Y-%m-%d')
        
        # 获取历史记录
        history = self.get_data('sign_history') or []
        
        # 检查今天的签到记录
        today_records = [
            record for record in history 
            if record.get("date", "").startswith(today) 
            and record.get("status") in ["签到成功", "已签到"]
        ]
        
        if today_records:
            return True
            
        # 获取最后一次签到的日期和时间
        last_sign_date = self.get_data('last_sign_date')
        if last_sign_date:
            try:
                last_sign_datetime = datetime.strptime(last_sign_date, '%Y-%m-%d %H:%M:%S')
                last_sign_day = last_sign_datetime.strftime('%Y-%m-%d')
                
                # 如果最后一次签到是今天且是成功的
                if last_sign_day == today:
                    return True
            except Exception as e:
                logger.error(f"解析最后签到日期时出错: {str(e)}")
        
        return False

    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            logger.info(f"注册定时服务: {self._cron}")
            return [{
                "id": "nodeseeksign",
                "name": "NodeSeek论坛签到",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.sign,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 状态提示移除CloudFlare相关文案
        curl_cffi_status = "✅ 已安装" if HAS_CURL_CFFI else "❌ 未安装"
        cloudscraper_status = "✅ 已启用" if HAS_CLOUDSCRAPER else "❌ 未启用"
        
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '开启通知',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'random_choice',
                                            'label': '随机奖励',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'use_proxy',
                                            'label': '使用代理',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'verify_ssl',
                                            'label': '验证SSL证书',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'member_id',
                                            'label': 'NodeSeek成员ID',
                                            'placeholder': '可选，用于在通知中展示用户信息'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'min_delay',
                                            'label': '最小随机延迟(秒)',
                                            'type': 'number',
                                            'placeholder': '5'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'max_delay',
                                            'label': '最大随机延迟(秒)',
                                            'type': 'number',
                                            'placeholder': '12'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cookie',
                                            'label': '站点Cookie',
                                            'placeholder': '请输入站点Cookie值'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '签到周期'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'history_days',
                                            'label': '历史保留天数',
                                            'type': 'number',
                                            'placeholder': '30'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'max_retries',
                                            'label': '失败重试次数',
                                            'type': 'number',
                                            'placeholder': '3'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': f'【使用教程】\n1. 登录NodeSeek论坛网站，按F12打开开发者工具\n2. 在"网络"或"应用"选项卡中复制Cookie\n3. 粘贴Cookie到上方输入框\n4. 设置签到时间，建议早上8点(0 8 * * *)\n5. 启用插件并保存\n\n【功能说明】\n• 随机奖励：开启则使用随机奖励，关闭则使用固定奖励\n• 使用代理：开启则使用系统配置的代理服务器访问NodeSeek\n• 验证SSL证书：关闭可能解决SSL连接问题，但会降低安全性\n• 失败重试：设置签到失败后的最大重试次数，将在5-15分钟后随机重试\n• 随机延迟：请求前随机等待，降低被风控概率\n• 用户信息：配置成员ID后，通知中展示用户名/等级/鸡腿\n\n【环境状态】\n• curl_cffi: {curl_cffi_status}；cloudscraper: {cloudscraper_status}'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "cookie": "",
            "cron": "0 8 * * *",
            "random_choice": True,
            "history_days": 30,
            "use_proxy": True,
            "max_retries": 3,
            "verify_ssl": False,
            "min_delay": 5,
            "max_delay": 12,
            "member_id": ""
        }

    def get_page(self) -> List[dict]:
        """
        构建插件详情页面，展示签到历史
        """
        # 读取缓存的用户信息
        user_info = self.get_data('last_user_info') or {}
        # 获取签到历史
        historys = self.get_data('sign_history') or []
        
        # 如果没有历史记录
        if not historys:
            return [
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'info',
                        'variant': 'tonal',
                        'text': '暂无签到记录，请先配置Cookie并启用插件',
                        'class': 'mb-2'
                    }
                }
            ]
        
        # 按时间倒序排列历史
        historys = sorted(historys, key=lambda x: x.get("date", ""), reverse=True)
        
        # 构建历史记录表格行
        history_rows = []
        for history in historys:
            status_text = history.get("status", "未知")
            status_color = "success" if status_text in ["签到成功", "已签到"] else "error"
            
            history_rows.append({
                'component': 'tr',
                'content': [
                    # 日期列
                    {
                        'component': 'td',
                        'props': {
                            'class': 'text-caption'
                        },
                        'text': history.get("date", "")
                    },
                    # 状态列
                    {
                        'component': 'td',
                        'content': [
                            {
                                'component': 'VChip',
                                'props': {
                                    'color': status_color,
                                    'size': 'small',
                                    'variant': 'outlined'
                                },
                                'text': status_text
                            }
                        ]
                    },
                    # 消息列
                    {
                        'component': 'td',
                        'text': history.get('message', '-')
                    }
                ]
            })
        
        # 用户信息卡片（可选）
        user_info_card = []
        if user_info:
            member_id = str(user_info.get('member_id') or getattr(self, '_member_id', '') or '').strip()
            avatar_url = f"https://www.nodeseek.com/avatar/{member_id}.png" if member_id else None
            user_name = user_info.get('member_name', '-')
            rank = str(user_info.get('rank', '-'))
            coin = str(user_info.get('coin', '-'))
            npost = str(user_info.get('nPost', '-'))
            ncomment = str(user_info.get('nComment', '-'))

            user_info_card = [
                {
                    'component': 'VCard',
                    'props': {'variant': 'outlined', 'class': 'mb-4'},
                    'content': [
                        {'component': 'VCardTitle', 'props': {'class': 'text-h6'}, 'text': '👤 NodeSeek 用户信息'},
                        {
                            'component': 'VCardText',
                            'content': [
                                {
                                    'component': 'VRow',
                                    'props': {'align': 'center'},
                                    'content': [
                                        {
                                            'component': 'VCol',
                                            'props': {'cols': 12, 'md': 2},
                                            'content': [
                                                (
                                                    {
                                                        'component': 'VAvatar',
                                                        'props': {'size': 72, 'class': 'mx-auto'},
                                                        'content': [
                                                            {
                                                                'component': 'VImg',
                                                                'props': {'src': avatar_url} if avatar_url else {}
                                                            }
                                                        ]
                                                    } if avatar_url else {
                                                        'component': 'VAvatar',
                                                        'props': {'size': 72, 'color': 'grey-lighten-2', 'class': 'mx-auto'},
                                                        'text': user_name[:1]
                                                    }
                                                )
                                            ]
                                        },
                                        {
                                            'component': 'VCol',
                                            'props': {'cols': 12, 'md': 10},
                                            'content': [
                                                {
                                                    'component': 'VRow',
                                                    'props': {'class': 'mb-2'},
                                                    'content': [
                                                        {'component': 'span', 'props': {'class': 'text-subtitle-1 mr-4'}, 'text': user_name},
                                                        {'component': 'VChip', 'props': {'size': 'small', 'variant': 'outlined', 'color': 'primary', 'class': 'mr-2'}, 'text': f'等级 {rank}'},
                                                        {'component': 'VChip', 'props': {'size': 'small', 'variant': 'outlined', 'color': 'amber-darken-2', 'class': 'mr-2'}, 'text': f'鸡腿 {coin}'},
                                                        {'component': 'VChip', 'props': {'size': 'small', 'variant': 'outlined', 'class': 'mr-2'}, 'text': f'主题 {npost}'},
                                                        {'component': 'VChip', 'props': {'size': 'small', 'variant': 'outlined'}, 'text': f'评论 {ncomment}'}
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]

        # 最终页面组装
        return user_info_card + [
            # 标题
            {
                'component': 'VCard',
                'props': {'variant': 'outlined', 'class': 'mb-4'},
                'content': [
                    {
                        'component': 'VCardTitle',
                        'props': {'class': 'text-h6'},
                        'text': '📊 NodeSeek论坛签到历史'
                    },
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VTable',
                                'props': {
                                    'hover': True,
                                    'density': 'compact'
                                },
                                'content': [
                                    # 表头
                                    {
                                        'component': 'thead',
                                        'content': [
                                            {
                                                'component': 'tr',
                                                'content': [
                                                    {'component': 'th', 'text': '时间'},
                                                    {'component': 'th', 'text': '状态'},
                                                    {'component': 'th', 'text': '消息'}
                                                ]
                                            }
                                        ]
                                    },
                                    # 表内容
                                    {
                                        'component': 'tbody',
                                        'content': history_rows
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self):
        """
        退出插件，停止定时任务
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"退出插件失败: {str(e)}")

    def get_command(self) -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [] 