"""Download Stat"""
import asyncio
import time
from enum import Enum
from typing import Dict, Tuple, Union

from pyrogram import Client

from module.app import TaskNode


class DownloadState(Enum):
    """Download state"""

    Downloading = 1
    StopDownload = 2


_download_result: dict = {}
_total_download_speed: int = 0
_total_download_size: int = 0
_last_download_time: float = time.time()
_download_state: DownloadState = DownloadState.Downloading
_last_cleanup_time: float = 0.0

MAX_DOWNLOAD_RESULT_PER_CHAT = 2000
MAX_DOWNLOAD_RESULT_TOTAL = 10000
FINISHED_ITEM_TTL_SECONDS = 1800


def get_download_result() -> dict:
    """get global download result"""
    return _download_result


def get_total_download_speed() -> int:
    """get total download speed"""
    return _total_download_speed


def get_download_state() -> DownloadState:
    """get download state"""
    return _download_state


# pylint: disable = W0603
def set_download_state(state: DownloadState):
    """set download state"""
    global _download_state
    _download_state = state


def _iter_items_with_key():
    for chat_id, messages in _download_result.items():
        for message_id, value in messages.items():
            yield chat_id, message_id, value


def _cleanup_download_result(force: bool = False):
    global _last_cleanup_time
    now = time.time()
    if not force and now - _last_cleanup_time < 5:
        return

    _last_cleanup_time = now

    # 1) 优先清理过期的已完成任务
    for chat_id, messages in list(_download_result.items()):
        for message_id, value in list(messages.items()):
            finished_time = value.get("finished_time")
            if finished_time and now - finished_time > FINISHED_ITEM_TTL_SECONDS:
                messages.pop(message_id, None)
        if not messages:
            _download_result.pop(chat_id, None)

    # 2) 控制每个 chat 的最大记录数（按最近更新时间保留）
    for chat_id, messages in list(_download_result.items()):
        if len(messages) <= MAX_DOWNLOAD_RESULT_PER_CHAT:
            continue
        sorted_items = sorted(
            messages.items(),
            key=lambda item: item[1].get("end_time", 0),
            reverse=True,
        )
        keep = dict(sorted_items[:MAX_DOWNLOAD_RESULT_PER_CHAT])
        _download_result[chat_id] = keep

    # 3) 控制全局最大记录数
    all_items = list(_iter_items_with_key())
    if len(all_items) <= MAX_DOWNLOAD_RESULT_TOTAL:
        return

    all_items.sort(key=lambda item: item[2].get("end_time", 0), reverse=True)
    keep_keys: Dict[Tuple[Union[int, str], int], bool] = {}
    for chat_id, message_id, _ in all_items[:MAX_DOWNLOAD_RESULT_TOTAL]:
        keep_keys[(chat_id, message_id)] = True

    for chat_id, messages in list(_download_result.items()):
        for message_id in list(messages.keys()):
            if (chat_id, message_id) not in keep_keys:
                messages.pop(message_id, None)
        if not messages:
            _download_result.pop(chat_id, None)


async def update_download_status(
    down_byte: int,
    total_size: int,
    message_id: int,
    file_name: str,
    start_time: float,
    node: TaskNode,
    client: Client,
):
    """update_download_status"""
    cur_time = time.time()
    # pylint: disable = W0603
    global _total_download_speed
    global _total_download_size
    global _last_download_time

    if node.is_stop_transmission:
        client.stop_transmission()

    chat_id = node.chat_id

    while get_download_state() == DownloadState.StopDownload:
        if node.is_stop_transmission:
            client.stop_transmission()
        await asyncio.sleep(1)

    if not _download_result.get(chat_id):
        _download_result[chat_id] = {}

    if _download_result[chat_id].get(message_id):
        last_download_byte = _download_result[chat_id][message_id]["down_byte"]
        last_time = _download_result[chat_id][message_id]["end_time"]
        download_speed = _download_result[chat_id][message_id]["download_speed"]
        each_second_total_download = _download_result[chat_id][message_id][
            "each_second_total_download"
        ]
        end_time = _download_result[chat_id][message_id]["end_time"]

        _total_download_size += down_byte - last_download_byte
        each_second_total_download += down_byte - last_download_byte

        if cur_time - last_time >= 1.0:
            download_speed = int(each_second_total_download / (cur_time - last_time))
            end_time = cur_time
            each_second_total_download = 0

        download_speed = max(download_speed, 0)

        _download_result[chat_id][message_id]["down_byte"] = down_byte
        _download_result[chat_id][message_id]["end_time"] = end_time
        _download_result[chat_id][message_id]["download_speed"] = download_speed
        _download_result[chat_id][message_id][
            "each_second_total_download"
        ] = each_second_total_download
    else:
        each_second_total_download = down_byte
        _download_result[chat_id][message_id] = {
            "down_byte": down_byte,
            "total_size": total_size,
            "file_name": file_name,
            "start_time": start_time,
            "end_time": cur_time,
            "download_speed": down_byte / (cur_time - start_time),
            "each_second_total_download": each_second_total_download,
            "task_id": node.task_id,
        }
        _total_download_size += down_byte

    if cur_time - _last_download_time >= 1.0:
        # update speed
        _total_download_speed = int(
            _total_download_size / (cur_time - _last_download_time)
        )
        _total_download_speed = max(_total_download_speed, 0)
        _total_download_size = 0
        _last_download_time = cur_time

    # 标记完成时间，便于后续清理
    if total_size > 0 and down_byte >= total_size:
        item = _download_result.get(chat_id, {}).get(message_id)
        if item is not None:
            item["finished_time"] = cur_time

    _cleanup_download_result()
