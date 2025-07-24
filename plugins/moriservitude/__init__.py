"""
末日黑奴的自我修养插件
版本: 1.0.0
作者: madrays  
功能:
- 监控末日站点保种情况
- 检查是否满足保种体积要求
- 监控保种时长和距离退休时间
- 提供详细的保种数据展示和通知
- 支持自定义保种要求和退休阈值
"""
import time
import json
import re
import requests
from datetime import datetime, timedelta
from typing import Any, List, Dict, Tuple, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType
from app.db.site_oper import SiteOper
from app.helper.sites import SitesHelper


class moriservitude(_PluginBase):
    # 插件名称
    plugin_name = "末日黑奴的自我修养"
    # 插件描述  
    plugin_desc = "监控末日站点保种情况，检查保种体积要求，计算距离退休时间"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/madrays/MoviePilot-Plugins/main/icons/agsv.png"
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "madrays"
    # 作者主页
    author_url = "https://github.com/madrays"
    # 插件配置项ID前缀
    plugin_config_prefix = "moriservitude_"
    # 加载顺序
    plugin_order = 2
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    _notify = False
    _onlyonce = False
    _cron = "0 */6 * * *"  # 默认每6小时检查一次
    _min_seeding_size = 5.0  # 最小保种体积要求（TB，默认5TB）
    _retirement_days = 730  # 退休所需天数（2年）
    _history_days = 30  # 历史保留天数
    _start_date = None  # 用户开始保种的日期
    
    # 站点助手
    sites: SitesHelper = None
    siteoper: SiteOper = None
    
    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        logger.info("============= 末日黑奴的自我修养初始化 =============")
        try:
            if config:
                self._enabled = config.get("enabled")
                self._notify = config.get("notify")
                self._cron = config.get("cron")
                self._onlyonce = config.get("onlyonce")
                self._min_seeding_size = float(config.get("min_seeding_size", 5.0))
                self._retirement_days = int(config.get("retirement_days", 730))
                self._history_days = int(config.get("history_days", 30))
                self._start_date = config.get("start_date")
                
                logger.info(f"配置: enabled={self._enabled}, notify={self._notify}, "
                           f"cron={self._cron}, min_seeding_size={self._min_seeding_size}GB, "
                           f"retirement_days={self._retirement_days}天, start_date={self._start_date}")
                
            # 初始化站点助手
            self.sites = SitesHelper()
            self.siteoper = SiteOper()
            
            if self._onlyonce:
                logger.info("执行一次性检查")
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._scheduler.add_job(func=self.check_seeding_status, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="末日黑奴的自我修养检查")
                self._onlyonce = False
                self.update_config({
                    "onlyonce": False,
                    "enabled": self._enabled,
                    "notify": self._notify,
                    "cron": self._cron,
                    "min_seeding_size": self._min_seeding_size,
                    "retirement_days": self._retirement_days,
                    "history_days": self._history_days,
                    "start_date": self._start_date
                })

                # 启动任务
                if self._scheduler.get_jobs():
                    self._scheduler.print_jobs()
                    self._scheduler.start()

        except Exception as e:
            logger.error(f"末日黑奴的自我修养初始化错误: {str(e)}", exc_info=True)

    def check_seeding_status(self):
        """
        检查保种状态
        """
        logger.info("============= 开始检查保种状态 =============")
        
        try:
            # 获取末日站点数据
            mori_data = self._get_mori_site_data()
            if not mori_data:
                logger.error("未找到末日站点数据")
                return
                
            # 分析保种情况
            analysis = self._analyze_seeding_status(mori_data)
            
            # 保存历史记录
            self._save_history(analysis)
            
            # 发送通知
            if self._notify:
                self._send_notification(analysis)
                
            logger.info("保种状态检查完成")
            
        except Exception as e:
            logger.error(f"检查保种状态失败: {str(e)}", exc_info=True)

    def _get_mori_site_data(self) -> Optional[Dict[str, Any]]:
        """
        从末日站点的mybonus.php页面获取保种数据
        """
        try:
            # 获取末日站点配置
            mori_site_config = self._get_mori_site_config()
            if not mori_site_config:
                return None
                
            # 访问mybonus.php页面
            bonus_data = self._fetch_bonus_page(mori_site_config)
            if not bonus_data:
                return None
                
            return bonus_data
                
        except Exception as e:
            logger.error(f"获取末日站点保种数据失败: {str(e)}")
            return None

    def _get_mori_site_config(self) -> Optional[Dict[str, Any]]:
        """
        获取末日站点配置信息
        """
        try:
            # 获取所有站点配置
            all_sites = self.sites.get_indexers()
            
            # 末日站点在MP中的名称是AGSVPT，优先匹配
            agsvpt_site_names = ["AGSVPT", "agsvpt"]
                
            # 查找AGSVPT站点
            for site in all_sites:
                site_name = site.get("name", "")
                for agsvpt_name in agsvpt_site_names:
                    if agsvpt_name.lower() in site_name.lower():
                        logger.info(f"找到AGSVPT站点配置: {site_name}")
                        return site
                        
            logger.warning(f"未找到AGSVPT站点配置，检查的站点包括: {agsvpt_site_names}")
            return None
            
        except Exception as e:
            logger.error(f"获取末日站点配置失败: {str(e)}")
            return None

    def _fetch_bonus_page(self, site_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        访问站点的mybonus.php页面并解析保种数据
        """
        try:
            site_name = site_config.get("name", "")
            site_url = site_config.get("url", "")
            site_cookie = site_config.get("cookie", "")
            site_ua = site_config.get("ua", "")
            
            if not all([site_url, site_cookie]):
                logger.error(f"站点 {site_name} 配置不完整: 缺少URL或Cookie")
                return None
                
            # 构造请求
            headers = {
                "User-Agent": site_ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            }
            
            # 解析Cookie
            cookies = {}
            if site_cookie:
                for cookie_item in site_cookie.split(';'):
                    if '=' in cookie_item:
                        name, value = cookie_item.strip().split('=', 1)
                        cookies[name] = value
            
            # 构造mybonus.php的URL
            bonus_url = urljoin(site_url, "mybonus.php")
            
            logger.info(f"正在访问 {site_name} 的保种页面: {bonus_url}")
            
            # 发送请求
            response = requests.get(
                bonus_url,
                headers=headers,
                cookies=cookies,
                timeout=30,
                verify=False
            )
            response.raise_for_status()
            
            # 解析HTML
            return self._parse_bonus_page(response.text, site_name)
            
        except Exception as e:
            logger.error(f"访问保种页面失败: {str(e)}")
            return None

    def _parse_bonus_page(self, html_content: str, site_name: str) -> Optional[Dict[str, Any]]:
        """
        解析mybonus.php页面，提取官种保种数据
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 查找保种奖励表格
            table = soup.find('table', {'cellpadding': '5'})
            if not table:
                logger.error(f"未找到 {site_name} 的保种奖励表格")
                return None
                
            # 查找官种加成行
            rows = table.find_all('tr')
            official_seed_data = None
            
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 7:  # 确保有足够的列
                    reward_type = cells[0].get_text(strip=True)
                    
                    # 跳过表头行和非数据行
                    if "奖励类型" in reward_type or "Arctic" in reward_type:
                        continue
                        
                    if "官种" in reward_type or "Official" in reward_type:
                        try:
                            quantity_text = cells[1].get_text(strip=True).replace(',', '')
                            volume_text = cells[2].get_text(strip=True)
                            
                            # 验证数量是否为有效数字
                            if not quantity_text or not quantity_text.isdigit():
                                logger.warning(f"数量字段无效: '{quantity_text}'")
                                continue
                                
                            quantity = int(quantity_text)
                            
                            # 解析体积
                            volume_tb = self._parse_volume_to_tb(volume_text)
                            
                            official_seed_data = {
                                "site_name": site_name,
                                "quantity": quantity,
                                "volume_text": volume_text,
                                "volume_tb": volume_tb,
                                "reward_type": reward_type
                            }
                            
                            logger.info(f"解析到 {site_name} 官种数据: {quantity}个, {volume_text}({volume_tb:.3f}TB)")
                            break
                            
                        except (ValueError, IndexError) as e:
                            logger.warning(f"解析官种数据失败: {str(e)}")
                            continue
                            
            if not official_seed_data:
                logger.warning(f"未找到 {site_name} 的官种加成数据")
                return None
                
            return official_seed_data
            
        except Exception as e:
            logger.error(f"解析保种页面失败: {str(e)}")
            return None

    def _parse_volume_to_tb(self, volume_text: str) -> float:
        """
        将体积文本转换为TB数值
        """
        try:
            # 移除空格并转为大写
            volume_text = volume_text.replace(' ', '').upper()
            
            # 提取数字
            number_match = re.search(r'(\d+\.?\d*)', volume_text)
            if not number_match:
                return 0.0
                
            number = float(number_match.group(1))
            
            # 根据单位转换为TB
            if 'TB' in volume_text:
                return number
            elif 'GB' in volume_text:
                return number / 1024
            elif 'MB' in volume_text:
                return number / (1024 * 1024)
            elif 'KB' in volume_text:
                return number / (1024 * 1024 * 1024)
            else:
                # 默认按TB处理
                return number
                
        except Exception as e:
            logger.warning(f"解析体积失败: {volume_text} - {str(e)}")
            return 0.0

    def _analyze_seeding_status(self, site_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        分析保种状态
        """
        analysis = {
            "check_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "site_name": site_data.get("site_name", "AGSVPT"),
            "reward_type": site_data.get("reward_type", "官种加成"),
            "seeding": site_data.get("quantity", 0),  # 官种数量
            "seeding_size": site_data.get("volume_text", "0 TB"),  # 原始体积文本
            "seeding_size_tb": site_data.get("volume_tb", 0)  # 已转换的TB数值
        }
        
        # 检查是否满足保种要求
        seeding_size_tb = analysis["seeding_size_tb"]
        analysis["meets_requirement"] = seeding_size_tb >= self._min_seeding_size
        analysis["requirement_deficit"] = max(0, self._min_seeding_size - seeding_size_tb)
        
        # 计算保种时长和退休进度
        if self._start_date:
            start_date = self._parse_date(self._start_date)
            if start_date:
                days_passed = (datetime.now() - start_date).days
                analysis["days_passed"] = days_passed
                analysis["days_to_retirement"] = max(0, self._retirement_days - days_passed)
                analysis["retirement_progress"] = min(100, (days_passed / self._retirement_days) * 100)
                analysis["can_retire"] = days_passed >= self._retirement_days
            else:
                logger.warning(f"无法解析开始日期: {self._start_date}")
                analysis["days_passed"] = 0
                analysis["days_to_retirement"] = self._retirement_days
                analysis["retirement_progress"] = 0
                analysis["can_retire"] = False
        else:
            logger.warning("未设置开始日期，无法计算退休进度")
            analysis["days_passed"] = 0
            analysis["days_to_retirement"] = self._retirement_days
            analysis["retirement_progress"] = 0
            analysis["can_retire"] = False
            
        # 状态评估
        if analysis["can_retire"] and analysis["meets_requirement"]:
            analysis["status"] = "可以退休"
            analysis["status_emoji"] = "🎉"
        elif analysis["meets_requirement"]:
            analysis["status"] = "保种达标"
            analysis["status_emoji"] = "✅"
        else:
            analysis["status"] = "保种不足"
            analysis["status_emoji"] = "⚠️"
            
        logger.info(f"保种分析完成: {analysis['status']} - "
                   f"官种{analysis['seeding']}个, {seeding_size_tb:.1f}TB, "
                   f"进度{analysis['retirement_progress']:.1f}%")
        
        return analysis



    def _parse_date(self, date_str) -> Optional[datetime]:
        """
        解析日期字符串
        """
        if not date_str:
            return None
            
        try:
            # 常见日期格式
            formats = [
                '%Y-%m-%d',
                '%Y-%m-%d %H:%M:%S',
                '%Y/%m/%d',
                '%Y/%m/%d %H:%M:%S',
                '%Y.%m.%d',
                '%Y.%m.%d %H:%M:%S'
            ]
            
            for fmt in formats:
                try:
                    return datetime.strptime(str(date_str), fmt)
                except ValueError:
                    continue
                    
            logger.warning(f"无法解析日期: {date_str}")
            return None
            
        except Exception as e:
            logger.error(f"解析日期失败: {date_str} - {str(e)}")
            return None

    def _save_history(self, analysis: Dict[str, Any]):
        """
        保存历史记录
        """
        try:
            # 获取现有历史
            history = self.get_data('seeding_history') or []
            
            # 添加新记录
            history.append(analysis)
            
            # 清理旧记录
            retention_days = self._history_days
            now = datetime.now()
            valid_history = []
            
            for record in history:
                try:
                    record_time = datetime.strptime(record["check_time"], '%Y-%m-%d %H:%M:%S')
                    if (now - record_time).days < retention_days:
                        valid_history.append(record)
                except (ValueError, KeyError):
                    # 保留格式错误的记录，但修复时间
                    record["check_time"] = now.strftime('%Y-%m-%d %H:%M:%S')
                    valid_history.append(record)
            
            # 保存历史
            self.save_data(key="seeding_history", value=valid_history)
            logger.info(f"保存保种历史记录，当前共有 {len(valid_history)} 条记录")
            
        except Exception as e:
            logger.error(f"保存历史记录失败: {str(e)}", exc_info=True)

    def _send_notification(self, analysis: Dict[str, Any]):
        """
        发送通知
        """
        try:
            status = analysis["status"]
            status_emoji = analysis["status_emoji"]
            site_name = analysis["site_name"]
            seeding_size_tb = analysis["seeding_size_tb"]
            seeding_count = analysis["seeding"]
            retirement_progress = analysis["retirement_progress"]
            days_to_retirement = analysis["days_to_retirement"]
            
            title = f"【{status_emoji} 末日黑奴的自我修养】"
            
            text = (
                f"📊 保种状态报告\n"
                f"━━━━━━━━━━\n"
                f"🏠 站点：{site_name}\n"
                f"📈 状态：{status}\n"
                f"━━━━━━━━━━\n"
                f"📦 官种保种信息\n"
                f"🗂 官种数量：{seeding_count} 个\n"
                f"💾 官种体积：{seeding_size_tb:.1f} TB\n"
                f"🎯 最低要求：{self._min_seeding_size} TB\n"
            )
            
            if not analysis["meets_requirement"]:
                deficit = analysis["requirement_deficit"]
                text += f"⚠️ 体积不足：{deficit:.1f} TB\n"
            
            text += (
                f"━━━━━━━━━━\n"
                f"🎓 退休进度\n"
                f"📅 进度：{retirement_progress:.1f}%\n"
            )
            
            if analysis["can_retire"]:
                text += f"🎉 恭喜！已满足退休条件\n"
            else:
                text += f"⏰ 距退休：{days_to_retirement} 天\n"
                
            text += (
                f"━━━━━━━━━━\n"
                f"💪 继续保持，早日退休！"
            )
            
            # 发送通知
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title=title,
                text=text
            )
            
        except Exception as e:
            logger.error(f"发送通知失败: {str(e)}", exc_info=True)

    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            return [{
                "id": "moriservitude",
                "name": "末日黑奴的自我修养",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.check_seeding_status,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
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
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'start_date',
                                            'label': '开始日期',
                                            'type': 'date',
                                            'hint': '保种开始日期，用于计算退休进度（必填）',
                                            'persistent-hint': True
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
                                            'label': '检查周期'
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
                                            'model': 'min_seeding_size',
                                            'label': '最小保种体积(TB)',
                                            'type': 'number',
                                            'hint': 'Agsv站保种组要求的最小官种体积（默认5TB）'
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
                                            'model': 'retirement_days',
                                            'label': '退休所需天数',
                                            'type': 'number',
                                            'hint': '达到此天数可申请退休'
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
                                            'hint': '保种历史记录保留时间'
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
                                            'text': '【使用说明】\n1. 确保已在MoviePilot中添加并配置好AGSVPT站点(需要Cookie)\n2. 设置保种开始日期，用于计算退休进度（必填）\n3. 设置符合Agsv站保种组要求的最小官种体积\n4. 设置检查频率，建议每6小时检查一次\n5. 开启通知可及时了解保种状态变化\n\n【重要说明】\n- 本插件专门监控AGSVPT站点的"官种加成"保种数据\n- 数据来源于站点的mybonus.php页面\n- 必须设置开始日期才能正确计算退休进度\n- 插件会自动识别AGSVPT站点，无需手动选择\n\n【保种组要求】\n- 最小官种体积：5TB\n- 满2年可申请退休\n- 需持续保持官种保种要求'
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
            "start_date": "",
            "cron": "0 */6 * * *",
            "min_seeding_size": 5.0,
            "retirement_days": 730,
            "history_days": 30
        }

    def get_page(self) -> List[dict]:
        """
        构建插件详情页面，展示保种历史和状态
        """
        # 获取保种历史
        history = self.get_data('seeding_history') or []
        
        # 如果没有历史记录
        if not history:
            return [
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'info',
                        'variant': 'tonal',
                        'text': '暂无保种记录，请先确保已配置末日站点并启用插件',
                        'class': 'mb-2'
                    }
                }
            ]
        
        # 按时间倒序排列历史
        history = sorted(history, key=lambda x: x.get("check_time", ""), reverse=True)
        
        # 获取最新记录用于展示当前状态
        latest = history[0] if history else {}
        
        # 构建状态卡片
        status_card = {
            'component': 'VCard',
            'props': {'variant': 'outlined', 'class': 'mb-4'},
            'content': [
                {
                    'component': 'VCardTitle',
                    'props': {'class': 'text-h6'},
                    'text': f'🎯 当前状态 - {latest.get("status", "未知")} {latest.get("status_emoji", "")}'
                },
                {
                    'component': 'VCardText',
                    'content': [
                        {
                            'component': 'VRow',
                            'content': [
                                {
                                    'component': 'VCol',
                                    'props': {'cols': 12, 'md': 3},
                                    'content': [
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-center'},
                                            'content': [
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'text-h4 primary--text'},
                                                    'text': f"{latest.get('seeding_size_tb', 0):.1f}"
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'text-caption'},
                                                    'text': '官种体积(TB)'
                                                }
                                            ]
                                        }
                                    ]
                                },
                                {
                                    'component': 'VCol',
                                    'props': {'cols': 12, 'md': 3},
                                    'content': [
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-center'},
                                            'content': [
                                                                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-h4 success--text'},
                                            'text': str(latest.get('seeding', 0))
                                        },
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-caption'},
                                            'text': '官种数量'
                                        }
                                            ]
                                        }
                                    ]
                                },
                                {
                                    'component': 'VCol',
                                    'props': {'cols': 12, 'md': 3},
                                    'content': [
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-center'},
                                            'content': [
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'text-h4 warning--text'},
                                                    'text': f"{latest.get('retirement_progress', 0):.1f}%"
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'text-caption'},
                                                    'text': '退休进度'
                                                }
                                            ]
                                        }
                                    ]
                                },
                                {
                                    'component': 'VCol',
                                    'props': {'cols': 12, 'md': 3},
                                    'content': [
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-center'},
                                            'content': [
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'text-h4 info--text'},
                                                    'text': str(latest.get('days_to_retirement', self._retirement_days))
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'text-caption'},
                                                    'text': '距退休天数'
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
        
        # 构建历史记录表格
        history_rows = []
        for record in history:
            # 状态颜色
            status = record.get("status", "未知")
            if status == "可以退休":
                status_color = "success"
            elif status == "保种达标":
                status_color = "primary"
            else:
                status_color = "warning"
            
            history_rows.append({
                'component': 'tr',
                'content': [
                    {
                        'component': 'td',
                        'props': {'class': 'text-caption'},
                        'text': record.get("check_time", "")
                    },
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
                                'text': f"{record.get('status_emoji', '')} {status}"
                            }
                        ]
                    },
                    {
                        'component': 'td',
                        'text': f"{record.get('seeding_size_tb', 0):.1f} TB"
                    },
                    {
                        'component': 'td',
                        'text': str(record.get('seeding', 0))
                    },
                    {
                        'component': 'td',
                        'text': f"{record.get('retirement_progress', 0):.1f}%"
                    },
                    {
                        'component': 'td',
                        'text': str(record.get('days_to_retirement', '—'))
                    }
                ]
            })
        
        # 历史记录表格
        history_table = {
            'component': 'VCard',
            'props': {'variant': 'outlined', 'class': 'mb-4'},
            'content': [
                {
                    'component': 'VCardTitle',
                    'props': {'class': 'text-h6'},
                    'text': '📊 保种历史记录'
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
                                {
                                    'component': 'thead',
                                    'content': [
                                        {
                                            'component': 'tr',
                                            'content': [
                                                {'component': 'th', 'text': '检查时间'},
                                                {'component': 'th', 'text': '状态'},
                                                {'component': 'th', 'text': '官种体积'},
                                                {'component': 'th', 'text': '官种数量'},
                                                {'component': 'th', 'text': '退休进度'},
                                                {'component': 'th', 'text': '剩余天数'}
                                            ]
                                        }
                                    ]
                                },
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
        
        return [status_card, history_table]

    def stop_service(self):
        """停止服务"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止服务失败: {str(e)}")

    def get_command(self) -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [] 