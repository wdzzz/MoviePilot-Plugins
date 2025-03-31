import re
import requests
import time
from datetime import datetime
from typing import Any, List, Dict, Tuple, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.plugins import _PluginBase
from app.log import logger
from app.scheduler import Scheduler
from app.schemas import NotificationType
from app.utils.http import RequestUtils


class ZhuqueHelper(_PluginBase):
    # 插件名称
    plugin_name = "朱雀助手"
    # 插件描述
    plugin_desc = "技能释放、一键升级、获取执行记录。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/KoWming/MoviePilot-Plugins/main/icons/zhuquehelper.png"
    # 插件版本
    plugin_version = "1.2.5"
    # 插件作者
    plugin_author = "KoWming"
    # 作者主页
    author_url = "https://github.com/KoWming"
    # 插件配置项ID前缀
    plugin_config_prefix = "zhuquehelper_"
    # 加载顺序
    plugin_order = 24
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled: bool = False

    # 任务执行间隔
    _cron: Optional[str] = None
    _cookie: Optional[str] = None
    _onlyonce: bool = False
    _notify: bool = False
    _history_count: Optional[int] = None
    _level_up: Optional[bool] = None
    _skill_release: Optional[bool] = None
    _target_level: Optional[int] = None
    
    # 技能释放时间
    _min_next_time: Optional[int] = None

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: Optional[dict] = None) -> None:
        """
        初始化插件
        """
        # 停止现有任务
        self.stop_service()

        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron")
            self._cookie = config.get("cookie")
            self._notify = config.get("notify", False)
            self._onlyonce = config.get("onlyonce", False)
            self._history_count = int(config.get("history_count", 10))
            self._level_up = config.get("level_up", False)
            self._skill_release = config.get("skill_release", False)
            self._target_level = int(config.get("target_level", 79))

        if self._onlyonce:
            try:
                logger.info("朱雀助手服务启动，立即运行一次")
                # 关闭一次性开关
                self._onlyonce = False
                self.update_config({
                    "onlyonce": False,
                    "cron": self._cron,
                    "enabled": self._enabled,
                    "cookie": self._cookie,
                    "notify": self._notify,
                    "history_count": self._history_count,
                    "level_up": self._level_up,
                    "skill_release": self._skill_release,
                    "target_level": self._target_level,
                })

                # 启动任务
                self.__signin()
            except Exception as e:
                logger.error(f"朱雀助手服务启动失败: {str(e)}")

    def get_user_info(self, headers):
        """
        获取用户信息（灵石余额、角色最低等级和技能释放时间）
        """
        url = "https://zhuque.in/api/gaming/listGenshinCharacter"
        try:
            response = RequestUtils(headers=headers).get_res(url=url)
            response.raise_for_status()
            data = response.json().get('data', {})
            bonus = data.get('bonus', 0) 
            characters = data.get('characters', [])
            
            if not characters:
                logger.warning("角色数据为空列表")
                return None, None, None

            invalid_count = 0
            valid_levels = []
            next_times = []
            
            for char in characters:
                level = char.get('info', {}).get('level')
                next_time = char.get('info', {}).get('next_time')
                
                if level is not None:
                    valid_levels.append(level)
                else:
                    invalid_count += 1
                    
                if next_time is not None:
                    next_times.append(next_time)

            if invalid_count > 0:
                logger.warning(f"发现 {invalid_count} 条无效角色数据，已跳过")

            if not valid_levels:
                logger.error("所有角色均缺少有效等级信息")
                return None, None, None

            min_level = min(valid_levels)

            # 获取当前时间戳
            current_time = time.time()
            logger.info(f"当前时间：{self.convert_timestamp_to_datetime(current_time)}")
            # 打印所有的next_times，全部都将时间戳进行转换
            for next_time in next_times:
                logger.debug(f"角色技能释放时间：{self.convert_timestamp_to_datetime(next_time)}")
            # 获取最小next_time
            min_next_time = min((t for t in next_times if t > current_time), default=None)

            return bonus, min_level, min_next_time

        except requests.exceptions.RequestException as e:
            error_content = response.content if 'response' in locals() else '无响应'
            logger.error(f"请求失败: {e} | 响应内容: {error_content[:200]}...")
            return None, None, None

    def convert_timestamp_to_datetime(self, timestamp):
        """
        将时间戳转换为指定格式的日期时间字符串
        """
        try:
            dt = datetime.fromtimestamp(timestamp)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.error(f"时间戳转换失败: {e}")
            return None

    def __signin(self):
        """
        执行请求任务
        """
        try:
            res = RequestUtils(cookies=self._cookie).get_res(url="https://zhuque.in/index")
            if not res or res.status_code != 200:
                logger.error("请求首页失败！状态码：%s", res.status_code if res else "无响应")
                return

            # 获取csrfToken
            pattern = r'<meta\s+name="x-csrf-token"\s+content="([^"]+)">'
            csrfToken = re.findall(pattern, res.text)
            if not csrfToken:
                logger.error("请求csrfToken失败！页面内容：%s", res.text[:500])
                return

            csrfToken = csrfToken[0]
            logger.info(f"获取csrfToken成功：{csrfToken}")

            headers = {
                "cookie": self._cookie,
                "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
                "x-csrf-token": csrfToken,
            }

            try:
                res = RequestUtils(headers=headers).get_res(url="https://zhuque.in/api/user/getMainInfo")
                if not res or res.status_code != 200:
                    logger.error("请求用户信息失败！状态码：%s，响应内容：%s", res.status_code if res else "无响应",
                                 res.text if res else "")
                    return

                # 获取username
                data = res.json().get('data', {})
                username = data.get('username', res.text)
                if not username:
                    logger.error("获取用户名失败！响应内容：%s", res.text)
                    return

                logger.info(f"获取用户名成功：{username}")

                # 开始执行
                logger.info("开始获取用户信息...")
                user_info = self.get_user_info(headers)
                if not user_info or None in user_info:
                    logger.error("获取用户信息失败，跳过后续操作")
                    return

                logger.info("开始一键升级角色...")
                results = self.train_genshin_character(self._target_level, self._skill_release, self._level_up, headers)
                logger.info(f"一键升级完成，结果: {results}")

                # 重新获取用户信息
                logger.info("重新获取用户信息...")
                user_info = self.get_user_info(headers)
                if not user_info or None in user_info:
                    logger.error("获取用户信息失败，跳过后续操作")
                    return
                bonus, min_level, min_next_time = user_info
                logger.info(
                    f"获取用户信息完成，bonus: {bonus}, min_level: {min_level}, min_next_time: {self.convert_timestamp_to_datetime(min_next_time)}")

                # 保存min_next_time
                self._min_next_time = min_next_time

                # 如果开启了技能释放且有最小next_time，记录下次执行时间
                if self._skill_release and min_next_time:
                    next_time_str = self.convert_timestamp_to_datetime(min_next_time)
                    if next_time_str:
                        logger.info(f"下次技能释放时间: {next_time_str}")

                if bonus is not None and min_level is not None:
                    logger.info("开始生成报告...")
                    rich_text_report = self.generate_rich_text_report(results, bonus, min_level)
                    logger.info(f"报告生成完成：\n{rich_text_report}")
                else:
                    logger.error("获取用户信息失败，无法生成报告。")

                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "username": username,
                    "bonus": bonus,
                    "min_level": min_level,
                    "skill_release_bonus": results.get('skill_release', {}).get('bonus', 0)
                }

                # 读取历史记录
                history = self.get_data('sign_dict') or []
                history.append(sign_dict)
                
                # 只保留最新的N条记录
                if len(history) > self._history_count:
                    history = sorted(history, key=lambda x: x.get("date") or "", reverse=True)[:self._history_count]
                
                self.save_data(key="sign_dict", value=history)

                # 发送通知
                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="【任务执行完成】",
                        text=f"{rich_text_report}")

                self.reregister_plugin()

            except requests.exceptions.RequestException as e:
                logger.error(f"请求用户信息时发生异常: {e}，响应内容：{res.text if 'res' in locals() else '无响应'}")

        except requests.exceptions.RequestException as e:
            logger.error(f"请求首页时发生异常: {e}")

    def reregister_plugin(self) -> None:
        """
        重新注册插件
        """
        logger.info("重新注册插件")
        Scheduler().update_plugin_job(self.__class__.__name__)

    def train_genshin_character(self, level, skill_release, level_up, headers):
        results = {}
        # 释放技能
        if skill_release:
            url = "https://zhuque.in/api/gaming/fireGenshinCharacterMagic"
            data = {
                "all": 1,
                "resetModal": True
            }
            try:
                response = RequestUtils(headers=headers).post_res(url=url, json=data)
                response.raise_for_status()
                response_data = response.json()
                bonus = response_data['data']['bonus']
                results['skill_release'] = {
                    'status': '成功',
                    'bonus': bonus
                }
            except requests.exceptions.RequestException as e:
                results['skill_release'] = {'status': '失败', 'error': '访问错误'}

        # 一键升级
        if level_up:
            url = "https://zhuque.in/api/gaming/trainGenshinCharacter"
            data = {
                "resetModal": False,
                "level": level,
            }
            try:
                response = RequestUtils(headers=headers).post_res(url=url, json=data)
                response.raise_for_status()
                results['level_up'] = {'status': '成功'}
            except requests.exceptions.RequestException as e:
                if response.status_code == 400:
                    results['level_up'] = {'status': '成功', 'error': '灵石不足'}
                else:
                    results['level_up'] = {'status': '失败', 'error': '网络错误'}
        return results

    def generate_rich_text_report(self, results: Dict[str, Any], bonus: int, min_level: int) -> str:
        """生成报告"""
        try:
            report = "🌟 朱雀助手 🌟\n"
            report += f"技能释放：{'✅ ' if self._skill_release else '❌ '}\n"
            if 'skill_release' in results:
                if results['skill_release']['status'] == '成功':
                    report += f"成功，本次释放获得 {results['skill_release'].get('bonus', 0)} 灵石 💎\n"
                else:
                    report += f"失败，{results['skill_release'].get('error', '未知错误')} ❗️\n"
                if self._min_next_time:
                    next_time_str = self.convert_timestamp_to_datetime(self._min_next_time)
                    if next_time_str:
                        report += f"下次技能释放时间：{next_time_str} ⏰\n"
            report += f"一键升级：{'✅' if self._level_up else '❌'}\n"
            if 'level_up' in results:
                if results['level_up']['status'] == '成功':
                    if 'error' in results['level_up']:
                        report += f"升级受限，{results['level_up']['error']} ⚠️\n"
                    else:
                        report += f"升级成功 🎉\n"
                else:
                    report += f"失败，{results['level_up'].get('error', '未知错误')} ❗️\n"
            report += f"当前角色最低等级：{min_level} \n"
            report += f"当前账户灵石余额：{bonus} 💎\n"
            return report
        except Exception as e:
            logger.error(f"生成报告时发生异常: {e}")
            return "🌟 朱雀助手 🌟\n生成报告时发生错误，请检查日志以获取更多信息。"

    def get_state(self) -> bool:
        """获取插件状态"""
        return bool(self._enabled)

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """获取命令"""
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """获取API"""
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        """
        # 如果启用了技能释放且有保存的next_time，注册定时任务
        if self._skill_release and self._min_next_time:
            next_time_str = self.convert_timestamp_to_datetime(self._min_next_time)
            if next_time_str:
                logger.info(f"注册技能释放定时任务，下次执行时间: {next_time_str}")
                return [{
                    "id": "ZhuqueHelper",
                    "name": "朱雀助手",
                    "trigger": "date",
                    "func": self.__signin,
                    "kwargs": {
                        "run_date": datetime.fromtimestamp(self._min_next_time)
                    }
                }]
            
        # 如果设置了cron，注册cron定时任务
        if self._cron:
            return [{
                "id": "ZhuqueHelper",
                "name": "朱雀助手",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__signin,
                "kwargs": {}
            }]
        return []

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
                                    'md': 2
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'skill_release',
                                            'label': '技能释放',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 5
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'target_level',
                                            'label': '角色最高等级'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 5
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cookie',
                                            'label': '站点cookie'
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
                                    'md': 2
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'level_up',
                                            'label': '一键升级',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 5
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
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
                                    'md': 5
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'history_count',
                                            'label': '保留历史条数'
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
                                            'variant': 'tonal'
                                        },
                                        'content': [
                                            {
                                                'component': 'div',
                                                'content': [
                                                    {
                                                        'component': 'span',
                                                        'text': '特别鸣谢 Mr.Cai 大佬，插件源码来自于他的脚本。'
                                                    },
                                                    {
                                                        'component': 'br'
                                                    },
                                                    {
                                                        'component': 'span',
                                                        'text': '由于站点角色卡片技能释放时间不统一，导致cron定时器无法准确释放技能。'
                                                    },
                                                    {
                                                        'component': 'br'
                                                    },
                                                    {
                                                        'component': 'span',
                                                        'text': '现优化了定时器注册逻辑动态获取角色卡片下次技能释放的最近时间。'
                                                    },
                                                    {
                                                        'component': 'br'
                                                    },
                                                    {
                                                        'component': 'span',
                                                        'text': '使用获取的技能释放时间注册date定时器，如不开启【技能释放】则还是按照cron定时器执行。'
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
            "onlyonce": False,
            "notify": False,
            "level_up": False,
            "skill_release": False,
            "cookie": "",
            "history_count": 10,
            "cron": "0 9 * * *",
            "target_level": 79,
        }

    def get_page(self) -> List[dict]:
        # 查询同步详情
        historys = self.get_data('sign_dict')
        if not historys:
            logger.error("历史记录为空，无法显示任何信息。")
            return [
                {
                    'component': 'div',
                    'text': '暂无数据',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]

        if not isinstance(historys, list):
            logger.error(f"历史记录格式不正确，类型为: {type(historys)}")
            return [
                {
                    'component': 'div',
                    'text': '数据格式错误，请检查日志以获取更多信息。',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]

        # 按照签到时间倒序并限制显示条数
        historys = sorted(historys, key=lambda x: x.get("date") or "", reverse=True)
        if self._history_count:
            historys = historys[:self._history_count]

        # 签到消息
        sign_msgs = [
            {
                'component': 'tr',
                'props': {
                    'class': 'text-sm'
                },
                'content': [
                    {
                        'component': 'td',
                        'props': {
                            'class': 'whitespace-nowrap break-keep text-high-emphasis'
                        },
                        'text': history.get("date")
                    },
                    {
                        'component': 'td',
                        'text': history.get("username")
                    },
                    {
                        'component': 'td',
                        'text': history.get("min_level")
                    },
                    {
                        'component': 'td',
                        'text': f"{history.get('skill_release_bonus', 0)} 💎"
                    },
                    {
                        'component': 'td',
                        'text': f"{history.get('bonus', 0)} 💎"
                    }
                ]
            } for history in historys
        ]

        # 拼装页面
        return [
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
                                'component': 'VTable',
                                'props': {
                                    'hover': True
                                },
                                'content': [
                                    {
                                        'component': 'thead',
                                        'content': [
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '时间'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '用户名'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '当前角色最低等级'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '本次释放获得的灵石'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '当前账户灵石余额'
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'tbody',
                                        'content': sign_msgs
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self) -> None:
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
