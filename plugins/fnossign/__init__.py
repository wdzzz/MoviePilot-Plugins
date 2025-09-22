import re
import time
import hashlib
from datetime import datetime, timedelta

import pytz
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType


class JingKeJuSignin(_PluginBase):
    # 插件名称
    plugin_name = "镜客居签到"
    # 插件描述
    plugin_desc = "镜客居论坛自动签到，获取积分奖励。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/madrays/MoviePilot-Plugins/main/icons/fnos.ico"
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "改编自用户提供代码"
    # 作者主页
    author_url = ""
    # 插件配置项ID前缀
    plugin_config_prefix = "jingkejusignin_"
    # 加载顺序
    plugin_order = 25
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _cron = None
    _onlyonce = False
    _notify = False
    _history_days = None
    # 重试相关
    _retry_count = 0  # 最大重试次数
    _current_retry = 0  # 当前重试次数
    _retry_interval = 2  # 重试间隔(小时)
    # 代理相关
    _use_proxy = True  # 是否使用代理，默认启用
    # 用户名密码
    _username = None
    _password = None
    _is_email = False  # 是否使用邮箱登录

    # 网站相关常量
    LOGIN_PAGE = "https://www.jkju.cc/member.php"
    LOGIN_URL = "https://www.jkju.cc/member.php"
    SIGN_URL = "https://www.jkju.cc/plugin.php"
    SIGN_PAGE_URL = "https://www.jkju.cc/plugin.php?id=zqlj_sign"

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        """
        插件初始化
        """
        # 接收参数
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron", "30 9 * * *")
            self._onlyonce = config.get("onlyonce", False)
            self._history_days = config.get("history_days", 30)
            self._retry_count = int(config.get("retry_count", 0))
            self._retry_interval = int(config.get("retry_interval", 2))
            self._use_proxy = config.get("use_proxy", True)
            self._username = config.get("username", "")
            self._password = config.get("password", "")
            self._is_email = config.get("is_email", False)
        
        # 重置重试计数
        self._current_retry = 0
        
        # 停止现有任务
        self.stop_service()
        
        # 确保scheduler是新的
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        
        # 立即运行一次
        if self._onlyonce:
            logger.info(f"镜客居签到服务启动，立即运行一次")
            self._scheduler.add_job(
                func=self.__signin, 
                trigger='date',
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name="镜客居签到"
            )
            # 关闭一次性开关
            self._onlyonce = False
            self.update_config({
                "onlyonce": False,
                "cron": self._cron,
                "enabled": self._enabled,
                "notify": self._notify,
                "history_days": self._history_days,
                "retry_count": self._retry_count,
                "retry_interval": self._retry_interval,
                "use_proxy": self._use_proxy,
                "username": self._username,
                "password": self._password,
                "is_email": self._is_email
            })
        # 周期运行
        elif self._cron and self._enabled:
            logger.info(f"镜客居签到服务启动，周期：{self._cron}")
            self._scheduler.add_job(
                func=self.__signin,
                trigger=CronTrigger.from_crontab(self._cron),
                name="镜客居签到"
            )

        # 启动任务
        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

    def _send_notification(self, title, text):
        """
        发送通知
        """
        if self._notify:
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title=title,
                text=text
            )

    def _schedule_retry(self, hours=None):
        """
        安排重试任务
        :param hours: 重试间隔小时数，如果不指定则使用配置的_retry_interval
        """
        if not self._scheduler:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        # 计算下次重试时间
        retry_interval = hours if hours is not None else self._retry_interval
        next_run_time = datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(hours=retry_interval)
        
        # 安排重试任务
        self._scheduler.add_job(
            func=self.__signin, 
            trigger='date',
            run_date=next_run_time,
            name=f"镜客居签到重试 ({self._current_retry}/{self._retry_count})"
        )
        
        logger.info(f"镜客居签到失败，将在{retry_interval}小时后重试，当前重试次数: {self._current_retry}/{self._retry_count}")
        
        # 启动定时器（如果未启动）
        if not self._scheduler.running:
            self._scheduler.start()

    def _get_proxies(self):
        """
        获取代理设置
        """
        if not self._use_proxy:
            logger.info("未启用代理")
            return None
            
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

    def __signin(self):
        """
        镜客居签到主方法
        """
        # 增加任务锁，防止重复执行
        if hasattr(self, '_signing_in') and self._signing_in:
            logger.info("已有签到任务在执行，跳过当前任务")
            return
            
        self._signing_in = True
        try:
            # 检查用户名密码是否配置
            if not self._username or not self._password:
                logger.error("未配置用户名密码，无法进行签到")
                if self._notify:
                    self._send_notification(
                        title="【❌ 镜客居签到失败】",
                        text=(
                            f"📢 执行结果\n"
                            f"━━━━━━━━━━\n"
                            f"🕐 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"❌ 状态：签到失败，未配置用户名密码\n"
                            f"━━━━━━━━━━\n"
                            f"💡 配置方法\n"
                            f"• 在插件设置中填写镜客居论坛用户名和密码\n"
                            f"━━━━━━━━━━"
                        )
                    )
                return False
                
            # 初始化会话
            session = requests.Session()
            proxies = self._get_proxies()
            session.proxies = proxies if proxies else {}
            
            # 登录表单数据
            login_form_data = {
                "referer": "https://www.jkju.cc/",
                "questionid": 0,
                "answer": "",
                "cookietime": "2592000",
                "username": self._username,
                "password": self._password,
                "loginfield": "email" if self._is_email else "username"
            }
            
            login_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
                "Origin": "https://www.jkju.cc",
                "Referer": "https://www.jkju.cc/member.php?mod=logging&action=login",
            }
            
            # 登录参数
            login_params = {
                "mod": "logging",
                "action": "login",
                "loginsubmit": "yes",
                "inajax": 1,
            }
            
            # 获取登录哈希
            try:
                resp = session.get(
                    self.LOGIN_PAGE, 
                    params={"mod": "logging", "action": "login"}
                )
                if resp.status_code == 403:
                    resp = session.get(
                        self.LOGIN_PAGE, 
                        params={"mod": "logging", "action": "login"}
                    )
                
                soup = BeautifulSoup(resp.text, "html.parser")
                form_tag = soup.find("form", {"name": "login"})
                if not form_tag:
                    logger.error("无法找到登录表单")
                    self._handle_sign_failure("无法找到登录表单")
                    return False
                    
                formhash = form_tag.find("input", {"name": "formhash", "type": "hidden"}).get("value")
                loginhash = form_tag.get("action").split("&")[-1].split("=")[-1]
                
                login_form_data["formhash"] = formhash
                login_params["loginhash"] = loginhash
                
            except Exception as e:
                logger.error(f"获取登录哈希失败: {str(e)}")
                self._handle_sign_failure(f"获取登录信息失败: {str(e)}")
                return False
            
            # 执行登录
            try:
                resp = session.post(
                    self.LOGIN_URL,
                    params=login_params,
                    data=login_form_data,
                    headers=login_headers,
                )
                
                if resp.status_code == 403:
                    session.cookies.clear_expired_cookies()
                    resp = session.post(
                        self.LOGIN_URL,
                        params=login_params,
                        data=login_form_data,
                        headers=login_headers,
                    )
                
                text = resp.text
                if "请输入验证码继续登录" in text:
                    logger.error("登录需要验证码")
                    self._handle_sign_failure("登录需要验证码，请手动登录一次")
                    return False
                if "欢迎您回来" not in text:
                    logger.error("登录失败，未找到欢迎信息")
                    self._handle_sign_failure("登录失败，用户名或密码可能不正确")
                    return False
                    
                logger.info("登录成功")
                
            except Exception as e:
                logger.error(f"登录过程出错: {str(e)}")
                self._handle_sign_failure(f"登录过程出错: {str(e)}")
                return False
            
            # 获取签到页面
            try:
                sign_page_html = session.get(self.SIGN_PAGE_URL).text
                if not sign_page_html:
                    logger.error("获取签到页面失败")
                    self._handle_sign_failure("获取签到页面失败")
                    return False
            except Exception as e:
                logger.error(f"获取签到页面出错: {str(e)}")
                self._handle_sign_failure(f"获取签到页面出错: {str(e)}")
                return False
            
            # 检查是否已签到
            try:
                soup = BeautifulSoup(sign_page_html, "html.parser")
                sign_status_text = soup.find("div", class_="bm signbtn cl").find("a").text
                if "今日已打卡" in sign_status_text:
                    logger.info("今日已签到")
                    
                    # 获取签到趋势
                    trend_text = self._get_sign_trend(sign_page_html)
                    
                    # 发送通知
                    if self._notify:
                        self._send_notification(
                            title="【✅ 镜客居签到结果】",
                            text=(
                                f"📢 执行结果\n"
                                f"━━━━━━━━━━\n"
                                f"🕐 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"✨ 状态：今日已签到\n"
                                f"━━━━━━━━━━\n"
                                f"📊 签到趋势\n"
                                f"{trend_text}\n"
                                f"━━━━━━━━━━"
                            )
                        )
                    
                    # 保存历史记录
                    self._save_history({
                        "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                        "status": "已签到",
                        "trend": trend_text
                    })
                    
                    # 重置重试计数
                    self._current_retry = 0
                    return True
                    
            except Exception as e:
                logger.error(f"检查签到状态出错: {str(e)}")
                self._handle_sign_failure(f"检查签到状态出错: {str(e)}")
                return False
            
            # 执行签到
            try:
                # 获取签到哈希
                soup = BeautifulSoup(sign_page_html, "html.parser")
                form_tag = soup.find("form", {"id": "scbar_form"})
                sign_hash = form_tag.find("input", {"name": "formhash", "type": "hidden"}).get("value")
                
                # 发送签到请求
                sign_headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
                    "Referer": "https://www.jkju.cc/",
                }
                
                resp = session.get(
                    self.SIGN_URL,
                    headers=sign_headers,
                    params={"id": "zqlj_sign", "sign": sign_hash},
                ).text
                
                # 检查签到结果
                if "恭喜您，打卡成功！" in resp:
                    logger.info("签到成功")
                    
                    # 重新获取签到页面以获取最新趋势
                    sign_page_html = session.get(self.SIGN_PAGE_URL).text
                    trend_text = self._get_sign_trend(sign_page_html)
                    
                    # 发送通知
                    if self._notify:
                        self._send_notification(
                            title="【✅ 镜客居签到成功】",
                            text=(
                                f"📢 执行结果\n"
                                f"━━━━━━━━━━\n"
                                f"🕐 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"✨ 状态：签到成功\n"
                                f"━━━━━━━━━━\n"
                                f"📊 签到趋势\n"
                                f"{trend_text}\n"
                                f"━━━━━━━━━━"
                            )
                        )
                    
                    # 保存历史记录
                    self._save_history({
                        "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                        "status": "签到成功",
                        "trend": trend_text
                    })
                    
                    # 重置重试计数
                    self._current_retry = 0
                    return True
                elif "您今天已经打过卡了，请勿重复操作！" in resp:
                    logger.info("今日已签到")
                    
                    # 获取签到趋势
                    trend_text = self._get_sign_trend(sign_page_html)
                    
                    # 发送通知
                    if self._notify:
                        self._send_notification(
                            title="【✅ 镜客居签到结果】",
                            text=(
                                f"📢 执行结果\n"
                                f"━━━━━━━━━━\n"
                                f"🕐 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"✨ 状态：今日已签到\n"
                                f"━━━━━━━━━━\n"
                                f"📊 签到趋势\n"
                                f"{trend_text}\n"
                                f"━━━━━━━━━━"
                            )
                        )
                    
                    # 保存历史记录
                    self._save_history({
                        "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                        "status": "已签到",
                        "trend": trend_text
                    })
                    
                    # 重置重试计数
                    self._current_retry = 0
                    return True
                else:
                    logger.error(f"签到失败，响应内容: {resp[:200]}")
                    self._handle_sign_failure("签到失败，未知错误")
                    return False
                    
            except Exception as e:
                logger.error(f"执行签到出错: {str(e)}")
                self._handle_sign_failure(f"执行签到出错: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"签到过程发生未知错误: {str(e)}")
            self._handle_sign_failure(f"签到过程发生未知错误: {str(e)}")
            return False
        finally:
            # 释放锁
            self._signing_in = False

    def _get_sign_trend(self, html: str) -> str:
        """获取签到趋势信息"""
        try:
            soup = BeautifulSoup(html, "lxml")
            trend_lis = soup.select('#wp > div.ct2.cl > div.sd > div:nth-of-type(3) > div.bm_c > ul > li')
            if trend_lis:
                return "\n".join(li.text.strip() for li in trend_lis[:5])  # 只取前5条
            return "无法获取签到趋势"
        except Exception as e:
            logger.error(f"获取签到趋势出错: {str(e)}")
            return "获取签到趋势失败"

    def _handle_sign_failure(self, reason: str):
        """处理签到失败情况"""
        # 发送通知
        if self._notify:
            self._send_notification(
                title="【❌ 镜客居签到失败】",
                text=(
                    f"📢 执行结果\n"
                    f"━━━━━━━━━━\n"
                    f"🕐 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"❌ 状态：{reason}\n"
                    f"━━━━━━━━━━\n"
                    f"🔄 重试信息\n"
                    f"• 最大重试次数：{self._retry_count}\n"
                    f"• 重试间隔：{self._retry_interval}小时\n"
                    f"━━━━━━━━━━"
                )
            )
        
        # 保存历史记录
        self._save_history({
            "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
            "status": f"签到失败: {reason}",
            "trend": ""
        })
        
        # 设置下次定时重试
        if self._retry_count > 0 and self._current_retry < self._retry_count:
            self._current_retry += 1
            retry_hours = self._retry_interval * self._current_retry
            logger.info(f"安排第{self._current_retry}次定时重试，将在{retry_hours}小时后重试")
            self._schedule_retry(hours=retry_hours)
        else:
            self._current_retry = 0

    def _save_history(self, record):
        """
        保存签到历史记录
        """
        # 读取历史记录
        history = self.get_data('history') or []
        
        # 如果是失败状态，添加重试信息
        if "失败" in record.get("status", ""):
            record["retry"] = {
                "enabled": self._retry_count > 0,
                "current": self._current_retry,
                "max": self._retry_count,
                "interval": self._retry_interval
            }
        
        # 添加新记录
        history.append(record)
        
        # 保留指定天数的记录
        if self._history_days:
            try:
                days_ago = time.time() - int(self._history_days) * 24 * 60 * 60
                history = [record for record in history if
                          datetime.strptime(record["date"],
                                         '%Y-%m-%d %H:%M:%S').timestamp() >= days_ago]
            except Exception as e:
                logger.error(f"清理历史记录异常: {str(e)}")
        
        # 保存历史记录
        self.save_data(key="history", value=history)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        """
        services = []
        
        if self._enabled and self._cron:
            services.append({
                "id": "JingKeJuSignin",
                "name": "镜客居签到服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__signin,
                "kwargs": {}
            })
            
        return services

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mt-3'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'd-flex align-center'
                                },
                                'content': [
                                    {
                                        'component': 'VIcon',
                                        'props': {
                                            'style': 'color: #1976D2;',
                                            'class': 'mr-2'
                                        },
                                        'text': 'mdi-calendar-check'
                                    },
                                    {
                                        'component': 'span',
                                        'text': '镜客居签到设置'
                                    }
                                ]
                            },
                            {
                                'component': 'VDivider'
                            },
                            {
                                'component': 'VCardText',
                                'content': [
                                    # 基本开关设置
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
                                                    'md': 4
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
                                                    'md': 4
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
                                            }
                                        ]
                                    },
                                    # 用户名密码输入
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
                                                            'model': 'username',
                                                            'label': '用户名/邮箱',
                                                            'placeholder': '镜客居论坛用户名或邮箱',
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
                                                            'model': 'password',
                                                            'label': '密码',
                                                            'placeholder': '镜客居论坛密码',
                                                            'type': 'password',
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    # 登录方式和签到周期
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
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'is_email',
                                                            'label': '使用邮箱登录',
                                                            'hint': '如果使用邮箱登录，请开启此选项'
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
                                                        'component': 'VCronField',
                                                        'props': {
                                                            'model': 'cron',
                                                            'label': '签到周期',
                                                            'placeholder': '30 9 * * *',
                                                            'hint': '五位cron表达式，每天早上9:30执行'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    # 历史保留和重试设置
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
                                                            'model': 'history_days',
                                                            'label': '历史保留天数',
                                                            'placeholder': '30',
                                                            'hint': '历史记录保留天数'
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
                                                            'model': 'retry_count',
                                                            'label': '失败重试次数',
                                                            'type': 'number',
                                                            'placeholder': '0',
                                                            'hint': '0表示不重试，大于0则在签到失败后重试'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    # 重试间隔和代理设置
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
                                                            'model': 'retry_interval',
                                                            'label': '重试间隔(小时)',
                                                            'type': 'number',
                                                            'placeholder': '2',
                                                            'hint': '签到失败后多少小时后重试'
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
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'use_proxy',
                                                            'label': '使用代理',
                                                            'hint': '与镜客居论坛通信时使用系统代理'
                                                        }
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
            }
        ], {
            "enabled": False,
            "notify": True,
            "cron": "30 9 * * *",
            "onlyonce": False,
            "username": "",
            "password": "",
            "is_email": False,
            "history_days": 30,
            "retry_count": 0,
            "retry_interval": 2,
            "use_proxy": True
        }

    def get_page(self) -> List[dict]:
        """
        构建插件详情页面，展示签到历史
        """
        # 获取签到历史
        history = self.get_data('history') or []
        
        # 如果没有历史记录
        if not history:
            return [
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'info',
                        'variant': 'tonal',
                        'text': '暂无签到记录，请先配置用户名和密码并启用插件',
                        'class': 'mb-2',
                        'prepend-icon': 'mdi-information'
                    }
                },
                {
                    'component': 'VCard',
                    'props': {'variant': 'outlined', 'class': 'mb-4'},
                    'content': [
                        {
                            'component': 'VCardTitle',
                            'props': {'class': 'd-flex align-center'},
                            'content': [
                                {
                                    'component': 'VIcon',
                                    'props': {
                                        'color': 'blue',
                                        'class': 'mr-2'
                                    },
                                    'text': 'mdi-info-circle'
                                },
                                {
                                    'component': 'span',
                                    'props': {'class': 'text-h6'},
                                    'text': '镜客居签到说明'
                                }
                            ]
                        },
                        {
                            'component': 'VDivider'
                        },
                        {
                            'component': 'VCardText',
                            'props': {'class': 'pa-3'},
                            'content': [
                                {
                                    'component': 'div',
                                    'props': {'class': 'd-flex align-center mb-2'},
                                    'content': [
                                        {
                                            'component': 'VIcon',
                                            'props': {
                                                'style': 'color: #4CAF50;',
                                                'size': 'small',
                                                'class': 'mr-2'
                                            },
                                            'text': 'mdi-check-circle'
                                        },
                                        {
                                            'component': 'span',
                                            'text': '每日签到可获得积分奖励'
                                        }
                                    ]
                                },
                                {
                                    'component': 'div',
                                    'props': {'class': 'd-flex align-center'},
                                    'content': [
                                        {
                                            'component': 'VIcon',
                                            'props': {
                                                'style': 'color: #1976D2;',
                                                'size': 'small',
                                                'class': 'mr-2'
                                            },
                                            'text': 'mdi-calendar-check'
                                        },
                                        {
                                            'component': 'span',
                                            'text': '连续签到可累积积分，提升论坛等级'
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        
        # 按时间倒序排列历史
        history = sorted(history, key=lambda x: x.get("date", ""), reverse=True)
        
        # 构建历史记录表格行
        history_rows = []
        for record in history:
            status_text = record.get("status", "未知")
            
            # 根据状态设置颜色和图标
            if "签到成功" in status_text or "已签到" in status_text:
                status_color = "success"
                status_icon = "mdi-check-circle"
            else:
                status_color = "error"
                status_icon = "mdi-close-circle"
            
            history_rows.append({
                'component': 'tr',
                'content': [
                    # 日期列
                    {
                        'component': 'td',
                        'props': {
                            'class': 'text-caption'
                        },
                        'text': record.get("date", "")
                    },
                    # 状态列
                    {
                        'component': 'td',
                        'content': [
                            {
                                'component': 'VChip',
                                'props': {
                                    'style': 'background-color: #4CAF50; color: white;' if status_color == 'success' else 'background-color: #F44336; color: white;',
                                    'size': 'small',
                                    'variant': 'elevated'
                                },
                                'content': [
                                    {
                                        'component': 'VIcon',
                                        'props': {
                                            'start': True,
                                            'style': 'color: white;',
                                            'size': 'small'
                                        },
                                        'text': status_icon
                                    },
                                    {
                                        'component': 'span',
                                        'text': status_text
                                    }
                                ]
                            },
                            # 显示重试信息
                            {
                                'component': 'div',
                                'props': {'class': 'mt-1 text-caption grey--text'},
                                'text': f"将在{record.get('retry', {}).get('interval', self._retry_interval)}小时后重试 ({record.get('retry', {}).get('current', 0)}/{record.get('retry', {}).get('max', self._retry_count)})" if status_color == 'error' and record.get('retry', {}).get('enabled', False) and record.get('retry', {}).get('current', 0) > 0 else ""
                            }
                        ]
                    },
                    # 签到趋势列
                    {
                        'component': 'td',
                        'content': [
                            {
                                'component': 'VExpandTransition',
                                'content': [
                                    {
                                        'component': 'div',
                                        'props': {'class': 'text-sm'},
                                        'text': record.get('trend', '无数据')
                                    }
                                ]
                            }
                        ]
                    }
                ]
            })
        
        # 最终页面组装
        return [
            {
                'component': 'VCard',
                'props': {'variant': 'outlined', 'class': 'mb-4'},
                'content': [
                    {
                        'component': 'VCardTitle',
                        'props': {'class': 'd-flex align-center'},
                        'content': [
                            {
                                'component': 'VIcon',
                                'props': {
                                    'style': 'color: #1976D2;',
                                    'class': 'mr-2'
                                },
                                'text': 'mdi-calendar-check'
                            },
                            {
                                'component': 'span',
                                'props': {'class': 'text-h6 font-weight-bold'},
                                'text': '镜客居签到历史'
                            }
                        ]
                    },
                    {
                        'component': 'VDivider'
                    },
                    {
                        'component': 'VCardText',
                        'props': {'class': 'pa-2'},
                        'content': [
                            {
                                'component': 'VTable',
                                'props': {
                                    'hover': True,
                                    'density': 'comfortable'
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
                                                    {'component': 'th', 'text': '签到趋势'}
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
            },
            {
                'component': 'style',
                'text': """
                .v-table {
                    border-radius: 8px;
                    overflow: hidden;
                    box-shadow: 0 1px 2px rgba(0,0,0,0.05);
                }
                .v-table th {
                    background-color: rgba(var(--v-theme-primary), 0.05);
                    color: rgb(var(--v-theme-primary));
                    font-weight: 600;
                }
                """
            }
        ]

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e)) 
