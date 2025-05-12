from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from typing import List, Tuple, Dict, Any, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import schemas
from app.chain.media import MediaChain
from app.core.config import settings
from app.core.metainfo import MetaInfoPath
from app.db.transferhistory_oper import TransferHistoryOper
from app.db.plugindata_oper import PluginDataOper
from app.helper.nfo import NfoReader
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import MediaType
from app.utils.system import SystemUtils

# UnscrapFile
class UnscrapFile(_PluginBase):
    # 插件名称
    plugin_name = "对未刮削目录手动赋值"
    # 插件描述
    plugin_desc = "配合官方媒体库插件使用，对未刮削目录手动赋TMDBID"
    # 插件图标
    plugin_icon = "scraper.png"
    # 插件版本
    plugin_version = "0.0.1"
    # 插件作者
    plugin_author = "Linford"
    # 作者主页
    author_url = "https://github.com/Xlinford"
    # 插件配置项ID前缀
    plugin_config_prefix = "unscrapfile"
    # 加载顺序
    plugin_order = 7
    # 可使用的用户级别
    user_level = 1

    # 私有属性
    transferhis = None
    mediachain = None
    _scheduler = None
    _scraper = None
    _plugindata = None

    # 限速开关
    _enabled = False
    _onlyonce = False
    _cron = None
    _mode = ""
    _scraper_paths = ""
    _exclude_paths = ""
    _ids = ""
    _unscrapfiles = ""
    # 退出事件
    _event = Event()

    def init_plugin(self, config: dict = None):
        self.mediachain = MediaChain()
        self._plugindata = PluginDataOper()
        # 读取配置
        if config:
            self._ids = config.get("ids") or ""
        self._unscrapfiles =self._plugindata.get_data(plugin_id="LibraryScraperLin", key="unscrapfiles")
        # 停止现有任务
        self.stop_service()

        # 启动定时任务 & 立即运行一次
        if self._ids:
            self.transferhis = TransferHistoryOper()

            logger.info(f"媒体库手动刮削服务，立即运行一次")
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(func=self.__unscrapfile, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="媒体库手动刮削")
            if self._scheduler.get_jobs():
                # 启动服务
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self) -> bool:
        return False

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

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
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VCardText',
                                        'props': {'cols': 6},
                                        'content': [{'component': 'span', 'text': self._unscrapfiles}]
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
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'ids',
                                            'label': '查得的TMDB Id',
                                            'rows': 2,
                                            'placeholder': '使用逗号分隔'
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
                                            'text': '刮削路径后拼接#电视剧/电影，强制指定该媒体路径媒体类型。'
                                                    '不加默认根据文件名自动识别媒体类型。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "ids": "",
        }

    def get_page(self) -> List[dict]:
        pass
    def __unscrapfile(self):
        """
        开始刮削媒体库
        """
        # 获取未识别的媒体数据
        self._unscrapfiles = self._plugindata.get_data(plugin_id="LibraryScraperLin", key="unscrapfiles")
        logger.info(f"获取到未识别的媒体数据：{self._unscrapfiles}")

        # 拆分目录列表与ID列表
        media_info = [line for line in self._unscrapfiles.strip().split("\n") if line]
        ids = [i for i in self._ids.strip().split(",") if i]

        # 校验数量匹配
        if len(media_info) != len(ids):
            logger.warning(f"未识别的媒体数据数量不匹配，请检查，id数 {len(ids)} 不等于目录数 {len(media_info)}")
            return

        if not media_info:
            logger.info("未发现需要刮削的目录")
            return

        for idx, item in enumerate(media_info):
            if self._event.is_set():
                logger.info("媒体库刮削服务停止")
                return

            path_str, mtype = None, None

            if "#" in item:
                try:
                    path_str, type_str = item.split("#", 1)
                    mtype = next(
                        (media_type for media_type in MediaType.__members__.values()
                        if media_type.value == type_str),
                        None
                    )
                except ValueError:
                    logger.warning(f"路径信息格式不正确：{item}")
                    continue
            else:
                path_str = item

            try:
                path = Path(path_str)
            except Exception as e:
                logger.warning(f"无法识别路径：{path_str}，错误：{e}")
                continue

            if not path.exists():
                logger.warning(f"媒体库刮削路径不存在：{path}")
                continue

            logger.info(f"开始检索目录：{path} 类型：{mtype} Id: {ids[idx]}")
            self.__scrape_dir(path, mtype, ids[idx])
        self._ids =""
        self._plugindata.del_data(plugin_id="LibraryScraperLin", key="unscrapfiles")

    def __scrape_dir(self, path: Path, mtype: MediaType, tmdbid: Optional[str] = None):
        """
        削刮一个目录，该目录必须是媒体文件目录
        """

        if tmdbid and tmdbid!="0":
            # 按TMDBID识别
            logger.info(f"读取到传入的tmdbid：{tmdbid}")
            mediainfo = self.chain.recognize_media(tmdbid=tmdbid, mtype=mtype)
        else:
            logger.info(f"读取到传入的的tmdbid：{tmdbid} 放弃")

            return
        # 如果未开启新增已入库媒体是否跟随TMDB信息变化则根据tmdbid查询之前的title
        if not settings.SCRAP_FOLLOW_TMDB:
            transfer_history = self.transferhis.get_by_type_tmdbid(tmdbid=mediainfo.tmdb_id,
                                                                   mtype=mediainfo.type.value)
            if transfer_history:
                mediainfo.title = transfer_history.title
        # 获取图片
        self.chain.obtain_images(mediainfo)
        # 刮削
        self.mediachain.scrape_metadata(
            fileitem=schemas.FileItem(
                storage="local",
                type="dir",
                path=str(path).replace("\\", "/") + "/",
                name=path.name,
                basename=path.stem,
                modify_time=path.stat().st_mtime,
            ),
            mediainfo=mediainfo,
            overwrite=True if self._mode else False
        )
        logger.info(f"{path} 刮削完成")

    @staticmethod
    def __get_tmdbid_from_nfo(file_path: Path):
        """
        从nfo文件中获取信息
        :param file_path:
        :return: tmdbid
        """
        if not file_path:
            return None
        xpaths = [
            "uniqueid[@type='Tmdb']",
            "uniqueid[@type='tmdb']",
            "uniqueid[@type='TMDB']",
            "tmdbid"
        ]
        try:
            reader = NfoReader(file_path)
            for xpath in xpaths:
                tmdbid = reader.get_element_value(xpath)
                if tmdbid:
                    return tmdbid
        except Exception as err:
            logger.warn(f"从nfo文件中获取tmdbid失败：{str(err)}")
        return None

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            print(str(e))

       