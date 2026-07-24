from astrbot.api.all import *
import re
import aiohttp
import asyncio
import os
import shutil
import time
from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.message_components import Video, Image, Plain, Nodes, Node, File
from astrbot.api.event.filter import PermissionType
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

@register("fake_forward", "Jason.Joestar", "一个伪造转发消息的插件", "1.0.0", "插件仓库URL")
class NodeTestPlugin(Star):
    def __init__(self, context: Context, **kwargs):
        self.config = kwargs.get("config", {})
        super().__init__(context, **kwargs)
        if not hasattr(self, 'config') or not self.config:
            self.config = kwargs.get("config", {})

        self.admin_ids = self.config.get("admin_ids", [])
        self.api_token = self.config.get("api_token", "")
        self.debug_mode = self.config.get("debug_mode", False)
        self.timeout = self.config.get("timeout", 120)

        if not self.admin_ids:
            logger.warning("[伪造插件] 未配置可使用者列表，任何人将无法使用此插件。请在 WebUI 中设置。")
        if not self.api_token:
            logger.warning("[伪造插件] 未配置 API Token，昵称获取将回退到“用户+QQ号”。")

        self.pending_requests = {}
        self.nick_cache = {}

        self.temp_dir = get_astrbot_temp_path()
        os.makedirs(self.temp_dir, exist_ok=True)
        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        now = time.time()
        for f in os.listdir(self.cache_dir):
            fpath = os.path.join(self.cache_dir, f)
            if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > 3600:
                try:
                    os.unlink(fpath)
                    self._log_debug(f"清理过期缓存: {fpath}")
                except Exception:
                    pass

        logger.info(f"[伪造插件] 缓存目录: {self.cache_dir}")
        logger.info(f"[伪造插件] 临时目录: {self.temp_dir}")
        logger.info(f"[伪造插件] 可使用者列表: {self.admin_ids}")
        if self.debug_mode:
            logger.info("[伪造插件] 调试模式已开启")

    def _log_debug(self, msg):
        if self.debug_mode:
            logger.info(f"[DEBUG] {msg}")

    def _clear_cache(self, sender_id: str = None, expired_only: bool = False):
        try:
            now = time.time()
            for f in os.listdir(self.cache_dir):
                fpath = os.path.join(self.cache_dir, f)
                if not os.path.isfile(fpath):
                    continue
                should_delete = False
                if sender_id is not None:
                    if f.startswith(f"{sender_id}_"):
                        should_delete = True
                else:
                    if expired_only:
                        if (now - os.path.getmtime(fpath)) > 3600:
                            should_delete = True
                    else:
                        should_delete = True
                if should_delete:
                    os.unlink(fpath)
                    self._log_debug(f"清理缓存: {fpath}")
            if sender_id:
                self._log_debug(f"用户 {sender_id} 缓存已清理")
        except Exception as e:
            self._log_debug(f"清理缓存失败: {e}")

    async def get_qq_nickname(self, qq_number):
        if qq_number in self.nick_cache:
            self._log_debug(f"昵称缓存命中: {qq_number} -> {self.nick_cache[qq_number]}")
            return self.nick_cache[qq_number]

        if not self.api_token:
            nickname = f"用户{qq_number}"
            self.nick_cache[qq_number] = nickname
            return nickname

        url = f"https://api.qzqi.com/api/v1/QQInfo?qq={qq_number}"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        for attempt in range(1, 4):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("success") and "data" in data:
                                nickname = data["data"].get("name", "")
                                if nickname:
                                    self.nick_cache[qq_number] = nickname
                                    return nickname
            except Exception:
                pass
            await asyncio.sleep(0.5)
        nickname = f"用户{qq_number}"
        self.nick_cache[qq_number] = nickname
        return nickname

    async def _download_and_cache_url(self, url: str, sender_id: str, ext: str = None) -> str:
        if not url:
            return None
        if ext is None:
            match = re.search(r'\.([a-zA-Z0-9]+)(?:\?|$)', url)
            if match:
                ext = match.group(1)
            else:
                ext = 'tmp'
        filename = f"{sender_id}_{int(time.time())}.{ext}"
        cached_path = os.path.join(self.cache_dir, filename)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=60) as resp:
                    if resp.status != 200:
                        self._log_debug(f"下载失败 HTTP {resp.status}")
                        return None
                    content = await resp.read()
                    if len(content) == 0:
                        self._log_debug("下载文件为空")
                        return None
                    with open(cached_path, 'wb') as f:
                        f.write(content)
                    self._log_debug(f"下载文件成功: {cached_path} ({len(content)} bytes)")
                    return cached_path
        except Exception as e:
            self._log_debug(f"下载文件异常: {e}")
            return None

    async def parse_message_components(self, message_obj):
        self._log_debug("开始解析消息组件")
        media_items = []
        if hasattr(message_obj, 'message'):
            self._log_debug(f"消息链长度: {len(message_obj.message)}")
            for idx, comp in enumerate(message_obj.message):
                self._log_debug(f"组件{idx}: {type(comp).__name__}")
                if isinstance(comp, Video):
                    url = getattr(comp, 'url', None)
                    if url:
                        if url.startswith(('http://', 'https://')):
                            self._log_debug(f"视频网络URL: {url[:80]}...")
                            media_items.append((url, 'video', 'url', None))
                        else:
                            self._log_debug(f"视频'URL'实际是本地路径: {url}")
                            if os.path.exists(url):
                                media_items.append((url, 'video', 'file', None))
                            else:
                                temp_dir = self.temp_dir
                                full_path = os.path.join(temp_dir, os.path.basename(url))
                                if os.path.exists(full_path):
                                    media_items.append((full_path, 'video', 'file', None))
                                else:
                                    media_items.append((url, 'video', 'file', None))
                        continue
                    file_path = getattr(comp, 'file', None)
                    self._log_debug(f"视频文件路径: {file_path}")
                    if file_path:
                        if os.path.exists(file_path):
                            media_items.append((file_path, 'video', 'file', None))
                            self._log_debug(f"视频文件存在: {file_path}")
                        else:
                            temp_dir = self.temp_dir
                            full_path = os.path.join(temp_dir, os.path.basename(file_path))
                            self._log_debug(f"尝试补全路径: {full_path}")
                            if os.path.exists(full_path):
                                media_items.append((full_path, 'video', 'file', None))
                                self._log_debug(f"补全路径存在: {full_path}")
                            else:
                                media_items.append((file_path, 'video', 'file', None))
                                self._log_debug(f"文件不存在，记录原路径: {file_path}")
                    path_attr = getattr(comp, 'path', None)
                    if path_attr and os.path.exists(path_attr):
                        media_items.append((path_attr, 'video', 'file', None))
                        self._log_debug(f"通过 path 属性找到视频: {path_attr}")
                elif isinstance(comp, Image):
                    url = getattr(comp, 'url', None)
                    if url:
                        if url.startswith(('http://', 'https://')):
                            self._log_debug(f"图片网络URL: {url[:80]}...")
                            media_items.append((url, 'image', 'url', None))
                        else:
                            self._log_debug(f"图片'URL'实际是本地路径: {url}")
                            if os.path.exists(url):
                                media_items.append((url, 'image', 'file', None))
                            else:
                                temp_dir = self.temp_dir
                                full_path = os.path.join(temp_dir, os.path.basename(url))
                                if os.path.exists(full_path):
                                    media_items.append((full_path, 'image', 'file', None))
                                else:
                                    media_items.append((url, 'image', 'file', None))
                        continue
                    file_path = getattr(comp, 'file', None)
                    self._log_debug(f"图片文件路径: {file_path}")
                    if file_path:
                        if os.path.exists(file_path):
                            media_items.append((file_path, 'image', 'file', None))
                            self._log_debug(f"图片文件存在: {file_path}")
                        else:
                            temp_dir = self.temp_dir
                            full_path = os.path.join(temp_dir, os.path.basename(file_path))
                            self._log_debug(f"尝试补全路径: {full_path}")
                            if os.path.exists(full_path):
                                media_items.append((full_path, 'image', 'file', None))
                                self._log_debug(f"补全路径存在: {full_path}")
                            else:
                                media_items.append((file_path, 'image', 'file', None))
                                self._log_debug(f"文件不存在，记录原路径: {file_path}")
                    path_attr = getattr(comp, 'path', None)
                    if path_attr and os.path.exists(path_attr):
                        media_items.append((path_attr, 'image', 'file', None))
                        self._log_debug(f"通过 path 属性找到图片: {path_attr}")
                elif isinstance(comp, File):
                    original_name = getattr(comp, 'name', None)
                    if not original_name:
                        file_path = getattr(comp, 'file_', None) or getattr(comp, 'file', None)
                        if file_path:
                            original_name = os.path.basename(file_path)
                    self._log_debug(f"文件原始名: {original_name}")

                    url = getattr(comp, 'url', None)
                    if url:
                        if url.startswith(('http://', 'https://')):
                            self._log_debug(f"文件网络URL: {url[:80]}...")
                            media_items.append((url, 'file', 'url', original_name))
                        else:
                            self._log_debug(f"文件'URL'实际是本地路径: {url}")
                            if os.path.exists(url) and os.path.getsize(url) > 0:
                                media_items.append((url, 'file', 'file', original_name))
                            else:
                                temp_dir = self.temp_dir
                                full_path = os.path.join(temp_dir, os.path.basename(url))
                                if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                                    media_items.append((full_path, 'file', 'file', original_name))
                                else:
                                    media_items.append((url, 'file', 'file', original_name))
                        continue
                    file_path = getattr(comp, 'file_', None) or getattr(comp, 'file', None)
                    self._log_debug(f"文件路径: {file_path}")
                    if file_path:
                        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                            media_items.append((file_path, 'file', 'file', original_name))
                            self._log_debug(f"文件存在: {file_path} (大小: {os.path.getsize(file_path)})")
                        else:
                            temp_dir = self.temp_dir
                            full_path = os.path.join(temp_dir, os.path.basename(file_path))
                            if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                                media_items.append((full_path, 'file', 'file', original_name))
                                self._log_debug(f"补全路径存在: {full_path} (大小: {os.path.getsize(full_path)})")
                            else:
                                media_items.append((file_path, 'file', 'file', original_name))
                                self._log_debug(f"文件不存在或为空，记录原路径: {file_path}")
                    path_attr = getattr(comp, 'path', None)
                    if path_attr and os.path.exists(path_attr) and os.path.getsize(path_attr) > 0:
                        media_items.append((path_attr, 'file', 'file', original_name))
                        self._log_debug(f"通过 path 属性找到文件: {path_attr} (大小: {os.path.getsize(path_attr)})")
        self._log_debug(f"解析结果: {len(media_items)} 个媒体项")
        return media_items

    async def _process_media_fill(self, event: AstrMessageEvent, sender_id: str):
        pending = self.pending_requests.get(sender_id)
        if not pending:
            self._log_debug("没有待处理的任务")
            return False

        self._log_debug(f"处理媒体填充，任务剩余: {len(pending['segments'])} 个节点")

        if time.time() - pending['timestamp'] > self.timeout:
            del self.pending_requests[sender_id]
            self._clear_cache(sender_id)
            await event.send(MessageChain([Plain(f"操作超时（{self.timeout}秒），取消伪造")]))
            return True

        if pending.get('ready', False):
            await event.send(MessageChain([Plain("所有媒体已上传，请使用 /伪造确认 发送 或 /伪造取消 取消")]))
            return True

        media_items = await self.parse_message_components(event.message_obj)
        if not media_items:
            self._log_debug("当前消息无媒体")
            await event.send(MessageChain([Plain("当前消息未检测到媒体文件，请重新发送图片或视频")]))
            return True

        all_filled_before = all(ph['filled'] for seg in pending['segments'] for ph in seg['placeholders'])
        if all_filled_before:
            await event.send(MessageChain([Plain("所有占位符已填满，多余媒体将被忽略")]))
            return True

        filled_count = 0
        for original_path, media_type, path_type, original_name in media_items:
            if all(ph['filled'] for seg in pending['segments'] for ph in seg['placeholders']):
                break

            if path_type == 'url':
                if media_type == 'file':
                    ext = None
                    match = re.search(r'\.([a-zA-Z0-9]+)(?:\?|$)', original_path)
                    if match:
                        ext = match.group(1)
                else:
                    ext = None
                cached_path = await self._download_and_cache_url(original_path, sender_id, ext=ext)
                if cached_path:
                    original_path = cached_path
                    path_type = 'file'
                else:
                    self._log_debug("URL下载失败，仍使用原URL")

            filled = False
            for seg_idx, seg in enumerate(pending['segments']):
                for ph_idx, ph in enumerate(seg['placeholders']):
                    if not ph['filled']:
                        if path_type == 'file':
                            if os.path.dirname(original_path) == self.cache_dir:
                                path = original_path
                                self._log_debug("媒体已在缓存目录，直接使用")
                            else:
                                self._log_debug("处理本地文件，准备复制到缓存")
                                if not os.path.exists(original_path):
                                    self._log_debug(f"源文件不存在: {original_path}")
                                    if original_path.startswith(('http://', 'https://')):
                                        cached = await self._download_and_cache_url(original_path, sender_id)
                                        if cached:
                                            original_path = cached
                                            path_type = 'file'
                                        else:
                                            await event.send(MessageChain([Plain("媒体文件无法获取，请重新发送")]))
                                            return True
                                    else:
                                        await event.send(MessageChain([Plain("媒体文件已失效，请重新发送")]))
                                        return True
                                filename = os.path.basename(original_path)
                                unique_name = f"{sender_id}_{int(time.time())}_{filename}"
                                cached_path = os.path.join(self.cache_dir, unique_name)
                                self._log_debug(f"目标缓存路径: {cached_path}")
                                try:
                                    shutil.copy2(original_path, cached_path)
                                    path = cached_path
                                    self._log_debug(f"复制成功，新路径: {path}")
                                except Exception as e:
                                    self._log_debug(f"复制失败: {e}")
                                    await event.send(MessageChain([Plain("媒体文件复制失败，请重新发送")]))
                                    return True
                        else:
                            path = original_path
                            self._log_debug("使用网络URL，不复制")

                        if os.path.exists(path):
                            size = os.path.getsize(path)
                            self._log_debug(f"填充使用的文件大小: {size} bytes")
                            if size == 0:
                                self._log_debug("警告：文件大小为 0，可能无法下载")
                        else:
                            self._log_debug("警告：填充后文件不存在")

                        ph['filled'] = True
                        ph['path'] = path
                        ph['path_type'] = path_type
                        ph['actual_type'] = media_type
                        ph['original_name'] = original_name
                        filled = True
                        pending['fill_history'].append((seg_idx, ph_idx))
                        filled_count += 1
                        self._log_debug(f"填充占位符: 节点 {seg['qq']} placeholder {ph_idx} <- {path} (类型: {path_type})")
                        break
                if filled:
                    break

            if not filled:
                self._log_debug("没有可填充的占位符，跳过剩余媒体")
                break

        all_filled_after = all(ph['filled'] for seg in pending['segments'] for ph in seg['placeholders'])
        if all_filled_after and len(media_items) > filled_count:
            await event.send(MessageChain([Plain("所有占位符已填满，多余的媒体将被忽略")]))

        if filled_count == 0:
            self._log_debug("没有可填充的占位符")
            await event.send(MessageChain([Plain("所有节点已填充，多余媒体将被忽略")]))
            return True

        total = sum(1 for s in pending['segments'] for p in s['placeholders'])
        filled_so_far = len(pending['fill_history'])
        await event.send(MessageChain([Plain(f"已上传: {filled_so_far}/{total}")]))

        if all_filled_after:
            self._log_debug("所有占位符已填充，进入待确认状态")
            pending['ready'] = True
            await event.send(MessageChain([Plain("所有节点已填充 输入 /伪造确认 发送合并转发，或 /伪造取消 取消任务")]))
            return True
        else:
            self._log_debug("还有占位符未填充，继续等待")
            return True

    async def _build_nodes(self, segments, preview=False):
        nodes_list = []
        for seg in segments:
            if not seg['placeholders']:
                node_content = [Plain(seg['clean_text'])] if seg.get('clean_text', '') else []
                if node_content:
                    node = Node(uin=int(seg['qq']), name=seg['nickname'], content=node_content)
                    nodes_list.append(node)
            else:
                fragments = seg['fragments']
                current_content = []
                for frag in fragments:
                    if frag['type'] == 'text':
                        current_content.append(Plain(frag['text']))
                    else:
                        ph = seg['placeholders'][frag['ph_idx']]
                        if ph['filled']:
                            actual = ph['actual_type']
                            path = ph['path']
                            path_type = ph['path_type']
                            if actual == 'video':
                                if current_content:
                                    nodes_list.append(Node(uin=int(seg['qq']), name=seg['nickname'], content=current_content))
                                    current_content = []
                                try:
                                    if path_type == 'url':
                                        video = Video.fromURL(path)
                                    else:
                                        video = Video.fromFileSystem(path)
                                except Exception:
                                    video = Video.fromFileSystem(path)
                                nodes_list.append(Node(uin=int(seg['qq']), name=seg['nickname'], content=[video]))
                            elif actual == 'file':
                                if current_content:
                                    nodes_list.append(Node(uin=int(seg['qq']), name=seg['nickname'], content=current_content))
                                    current_content = []
                                original_name = ph.get('original_name', os.path.basename(path))
                                self._log_debug(f"构造 File 组件: file={path}, name={original_name}")
                                try:
                                    file_obj = File(file=path, name=original_name)
                                except Exception as e:
                                    self._log_debug(f"File 构造失败: {e}")
                                    file_obj = File(file=path, name=original_name)
                                nodes_list.append(Node(uin=int(seg['qq']), name=seg['nickname'], content=[file_obj]))
                            else:  # image
                                try:
                                    if path_type == 'url':
                                        img = Image.fromURL(path)
                                    else:
                                        img = Image(file=path)
                                except Exception:
                                    img = Image(file=path)
                                current_content.append(img)
                        else:
                            if preview:
                                current_content.append(Plain("[资源]"))
                if current_content:
                    nodes_list.append(Node(uin=int(seg['qq']), name=seg['nickname'], content=current_content))
        return nodes_list

    async def _send_nodes(self, event: AstrMessageEvent, pending: dict, preview=False):
        nodes_list = await self._build_nodes(pending['segments'], preview=preview)
        if not nodes_list:
            await event.send(MessageChain([Plain("没有可显示的内容")]))
            return False

        # 根据第一个节点的昵称生成标题
        first_seg = pending['segments'][0] if pending['segments'] else None
        if first_seg and first_seg.get('nickname'):
            summary = f"{first_seg['nickname']}的聊天记录"
        else:
            summary = "群聊的聊天记录"

        if preview:
            summary = f"[预览] {summary}"

        nodes = Nodes(nodes=nodes_list, summary=summary)
        try:
            await event.send(MessageChain([nodes]))
            self._log_debug("合并转发发送成功")

            # 非预览、私聊、且第一个节点使用了自定义昵称 → 发送提醒
            if not preview and first_seg and first_seg.get('is_custom_name', False):
                if event.message_type == 'private':
                    await event.send(MessageChain([Plain("自定义标题需要从私聊中转发才可生效")]))
                    await event.send(MessageChain([Plain("从群转私聊 再从私聊转发无效")]))

            return True
        except Exception as e:
            self._log_debug(f"发送失败: {e}")
            await event.send(MessageChain([Plain(f"合并转发生成失败: {str(e)}，请重新发送媒体")]))
            return False

    @event_message_type(EventMessageType.GROUP_MESSAGE | EventMessageType.PRIVATE_MESSAGE)
    async def on_all_message(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        if sender_id not in self.admin_ids:
            return

        message_text = event.message_str.strip()
        self._log_debug(f"收到消息: {message_text}")

        cmd = message_text.lstrip("/").strip()

        if cmd.startswith("伪造"):
            sub_cmd = cmd[2:].strip()
            if sub_cmd == "取消":
                self._log_debug("匹配到 /伪造取消")
                if sender_id in self.pending_requests:
                    del self.pending_requests[sender_id]
                    self._clear_cache(sender_id)
                    await event.send(MessageChain([Plain("已取消")]))
                else:
                    await event.send(MessageChain([Plain("当前没有待处理的伪造消息")]))
                return
            elif sub_cmd == "回退":
                self._log_debug("匹配到 /伪造回退")
                pending = self.pending_requests.get(sender_id)
                if not pending or not pending['fill_history']:
                    await event.send(MessageChain([Plain("当前无媒体可回退")]))
                else:
                    pending['ready'] = False
                    seg_idx, ph_idx = pending['fill_history'].pop()
                    ph = pending['segments'][seg_idx]['placeholders'][ph_idx]
                    ph['filled'] = False
                    ph['path'] = None
                    ph['path_type'] = None
                    ph['actual_type'] = None
                    ph['original_name'] = None
                    filled_count = len(pending['fill_history'])
                    total = sum(1 for s in pending['segments'] for p in s['placeholders'])
                    await event.send(MessageChain([Plain(f"已回退到 {filled_count}/{total}")]))
                return
            elif sub_cmd == "预览":
                self._log_debug("匹配到 /伪造预览")
                pending = self.pending_requests.get(sender_id)
                if not pending:
                    await event.send(MessageChain([Plain("当前没有待处理的伪造消息")]))
                else:
                    await self._send_nodes(event, pending, preview=True)
                return
            elif sub_cmd in ("确认", "发送"):
                self._log_debug(f"匹配到 /伪造{sub_cmd}")
                pending = self.pending_requests.get(sender_id)
                if not pending:
                    await event.send(MessageChain([Plain("当前没有待处理的伪造消息")]))
                elif not pending.get('ready', False):
                    await event.send(MessageChain([Plain("媒体尚未全部上传，请继续发送媒体")]))
                else:
                    success = await self._send_nodes(event, pending, preview=False)
                    if success:
                        self._clear_cache(sender_id)
                        del self.pending_requests[sender_id]
                return
            else:
                pass

        if re.search(r'伪造消息', message_text):
            if sender_id in self.pending_requests:
                self._clear_cache(sender_id)
                del self.pending_requests[sender_id]

            # 直接提取参数，不再解析标题
            if message_text.startswith("/伪造消息"):
                args = message_text[len("/伪造消息"):].lstrip()
            else:
                args = re.sub(r'^伪造消息\s*', '', message_text)

            if not args:
                await event.send(MessageChain([Plain("格式错误，请使用：伪造消息 [QQ号#昵称] 内容 | [QQ号#昵称] 内容 | ...")]))
                return

            parts = args.split('|')
            segments = []
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                match = re.match(r'^\s*(\d+)(?:#\s*([^#]+))?\s+(.*)', part)
                if not match:
                    continue
                qq = match.group(1)
                custom_name = match.group(2)
                content = match.group(3).strip()
                if not content:
                    continue

                pattern = r'\[资源\]'
                matches = list(re.finditer(pattern, content))
                fragments = []
                placeholders = []
                last_end = 0
                for idx, m in enumerate(matches):
                    if m.start() > last_end:
                        fragments.append({'type': 'text', 'text': content[last_end:m.start()]})
                    ph = {
                        'filled': False,
                        'path': None,
                        'path_type': None,
                        'actual_type': None,
                        'original_name': None
                    }
                    ph_idx = len(placeholders)
                    placeholders.append(ph)
                    fragments.append({'type': 'placeholder', 'ph_idx': ph_idx})
                    last_end = m.end()
                if last_end < len(content):
                    fragments.append({'type': 'text', 'text': content[last_end:]})

                nickname = custom_name if custom_name else await self.get_qq_nickname(qq)
                # 标记是否使用了自定义昵称
                is_custom = bool(custom_name)
                if not placeholders:
                    seg = {
                        'qq': qq,
                        'nickname': nickname,
                        'clean_text': content,
                        'placeholders': [],
                        'fragments': [],
                        'is_custom_name': is_custom
                    }
                else:
                    seg = {
                        'qq': qq,
                        'nickname': nickname,
                        'clean_text': None,
                        'placeholders': placeholders,
                        'fragments': fragments,
                        'is_custom_name': is_custom
                    }
                segments.append(seg)

            if not segments:
                await event.send(MessageChain([Plain("未能解析出任何有效的消息节点")]))
                return

            has_placeholder = any(seg.get('placeholders') for seg in segments)
            if not has_placeholder:
                # 无媒体，直接发送，通过 _send_nodes 统一处理
                pending = {
                    'segments': segments,
                    'summary': "群聊的聊天记录",
                    'ready': True
                }
                await self._send_nodes(event, pending, preview=False)
                return

            self.pending_requests[sender_id] = {
                'segments': segments,
                'timestamp': time.time(),
                'fill_history': [],
                'ready': False
            }

            total = sum(1 for seg in segments for ph in seg['placeholders'])
            qq_list = [seg['qq'] for seg in segments if seg['placeholders']]
            await event.send(MessageChain([Plain(f"开始生成\n需要媒体的节点：{', '.join(qq_list)}\n共 {total} 个媒体。输入 /伪造回退 撤销上一个，/伪造预览 预览，全部上传后输入 /伪造确认 发送")]))
            return

        if sender_id in self.pending_requests:
            processed = await self._process_media_fill(event, sender_id)
            if processed:
                return

    @filter.command("伪造帮助", permission=PermissionType.ADMIN)
    async def help_command(self, event: AstrMessageEvent):
        help_text = """伪造转发消息插件使用说明

【基本格式】
伪造消息 QQ号 内容

【多条消息】
使用 | 分割，例如：
伪造消息 123456 你好 | 12345 666

【自定义昵称】
QQ号#昵称，如：
伪造消息 123456#神人 你好

【媒体标记】
[资源] 用于标记需要媒体（图片或视频）的位置。

【媒体发送阶段】
按顺序发送图片/视频，每发送一个显示进度。
全部上传后，输入 /伪造确认 发送合并转发。
其他命令：
  /伪造回退  - 撤销上一个媒体
  /伪造预览  - 预览当前已上传的媒体（未填充显示 [资源]）
  /伪造取消  - 取消当前任务

【标题】
合并转发消息的外部卡片标题由第一个发言者的昵称决定（格式：{昵称}的聊天记录）。
你可以通过 #昵称 自定义昵称来改变标题。
"""
        yield event.plain_result(help_text)

    async def terminate(self):
        self._clear_cache(expired_only=False)
        pass
