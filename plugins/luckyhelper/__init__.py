import glob
import os
import time
import jwt
from datetime import datetime, timedelta
from pathlib import Path

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType
from app.utils.http import RequestUtils


class NoIPUpdater(_PluginBase):
    # 插件名称
    plugin_name = "No-IP 更新器"
    # 插件描述
    plugin_desc = "定时更新 No-IP 服务的 IP 地址"
    # 插件图标
    plugin_icon = "https://example.com/noip_icon.png"  # 请替换为实际图标地址
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "Your Name"
    # 作者主页
    author_url = "https://github.com/YourUsername"
    # 插件配置项ID前缀
    plugin_config_prefix = "noipupdater_"
    # 加载顺序
    plugin_order = 16
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    _username = None
    _password = None
    _hostname = None

    # 任务执行间隔
    _cron = None
    _notify = False
    _onlyonce = False

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._notify = config.get("notify")
            self._onlyonce = config.get("onlyonce")
            self._username = config.get("username")
            self._password = config.get("password")
            self._hostname = config.get("hostname")

            # 加载模块
        if self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            logger.info(f"IP 更新服务启动，立即运行一次")
            self._scheduler.add_job(func=self.__update_ip, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="IP 更新")
            # 关闭一次性开关
            self._onlyonce = False
            self.update_config({
                "onlyonce": False,
                "cron": self._cron,
                "enabled": self._enabled,
                "notify": self._notify,
                "username": self._username,
                "password": self._password,
                "hostname": self._hostname,
            })

            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_jwt(self) -> str:
        # 这里为示例，实际No-IP服务可能不需要jwt，而是其他认证方式
        payload = {
            "exp": int(time.time()) + 28 * 24 * 60 * 60,
            "iat": int(time.time())
        }
        encoded_jwt = jwt.encode(payload, self._password, algorithm="HS256")
        logger.debug(f"NoIPUpdater get jwt---》{encoded_jwt}")
        return "Bearer " + encoded_jwt

    def __update_ip(self):
        """
        自动更新 IP 地址
        """
        logger.info(f"当前时间 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))} 开始更新 IP")

        # 构建认证头（这里示例为Base64编码的用户名密码，实际按No-IP服务要求）
        credentials = f"{self._username}:{self._password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        headers = {
            'Authorization': f'Basic {encoded_credentials}'
        }

        # 构建请求 URL
        url = f"https://dynupdate.no-ip.com/nic/update?hostname={self._hostname}"

        try:
            # 发送请求
            result = (RequestUtils(headers=headers)
                     .get_res(url))

            # 检查响应状态码
            if result.status_code == 200:
                success = True
                msg = f"IP 更新成功，响应内容: {result.text.strip()}"
                logger.info(msg)
            else:
                success = False
                msg = f"IP 更新失败，状态码: {result.status_code}, 原因: {result.json().get('msg', '未知错误')}"
                logger.error(msg)
        except Exception as e:
            success = False
            msg = f"IP 更新失败，异常: {str(e)}"
            logger.error(msg)

        # 发送通知
        if self._notify:
            self.post_message(
                mtype=NotificationType.Plugin,
                title="【NoIPUpdater IP更新完成】:",
                text=f"IP 更新{'成功' if success else '失败'}\n"
                     f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}"
            )

        return success, msg

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
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled and self._cron:
            return [{
                "id": "NoIPUpdater",
                "name": "No-IP 更新定时服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__update_ip,
                "kwargs": {}
            }]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'username',
                                            'label': 'No-IP 用户名',
                                            'hint': 'No-IP 服务的用户名',
                                            'persistent-hint': True
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
                                            'model': 'password',
                                            'label': 'No-IP 密码',
                                            'hint': 'No-IP 服务的密码',
                                            'persistent-hint': True
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
                                            'model': 'hostname',
                                            'label': 'No-IP 主机名',
                                            'hint': '例如 your_hostname.no-ip.org',
                                            'persistent-hint': True
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
                                            'label': '更新周期',
                                            'placeholder': '0 8 * * *',
                                            'hint': '输入5位cron表达式，默认每天8点运行。',
                                            'persistent-hint': True
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
            "notify": False,
            "onlyonce": False,
            "cron": "0 8 * * *",
            "username": "",
            "password": "",
            "hostname": "",
        }

    def get_page(self) -> List[dict]:
        pass

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
