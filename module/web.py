"""web ui for media download"""

import logging
import os
import threading
from typing import List

from flask import Flask, jsonify, render_template, request
from flask_login import LoginManager, UserMixin, login_required, login_user

import utils
from module.app import Application
from module.download_stat import (
    DownloadState,
    get_download_result,
    get_download_state,
    get_total_download_speed,
    set_download_state,
)
from utils.crypto import AesBase64
from utils.format import format_byte

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

_flask_app = Flask(__name__)

_flask_app.secret_key = "tdl"
_login_manager = LoginManager()
_login_manager.login_view = "login"
_login_manager.init_app(_flask_app)
web_login_users: dict = {}
deAesCrypt = AesBase64("1234123412ABCDEF", "ABCDEF1234123412")


class User(UserMixin):
    """Web Login User"""

    def __init__(self):
        self.sid = "root"

    @property
    def id(self):
        """ID"""
        return self.sid


@_login_manager.user_loader
def load_user(_):
    """
    Load a user object from the user ID.

    Returns:
        User: The user object.
    """
    return User()


def get_flask_app() -> Flask:
    """get flask app instance"""
    return _flask_app


def _get_bot_instance():
    from module.bot import _bot  # pylint: disable=C0415

    return _bot


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _build_task_list() -> List[dict]:
    bot = _get_bot_instance()
    tasks = []
    for task_id, node in bot.task_node.items():
        total = node.total_download_task
        success = node.success_download_task
        failed = node.failed_download_task
        skipped = node.skip_download_task
        if str(node.task_type.name) in {"Forward", "ListenForward"}:
            total = node.total_forward_task
            success = node.success_forward_task
            failed = node.failed_forward_task
            skipped = node.skip_forward_task
        tasks.append(
            {
                "task_id": task_id,
                "task_type": str(node.task_type.name),
                "chat_id": str(node.chat_id),
                "running": bool(node.is_running and not node.is_finish()),
                "total": total,
                "success": success,
                "failed": failed,
                "skipped": skipped,
            }
        )
    return tasks


def run_web_server(app: Application):
    """
    Runs a web server using the Flask framework.
    """

    get_flask_app().run(
        app.web_host, app.web_port, debug=app.debug_web, use_reloader=False
    )


# pylint: disable = W0603
def init_web(app: Application):
    """
    Set the value of the users variable.

    Args:
        users: The list of users to set.

    Returns:
        None.
    """
    global web_login_users
    if app.web_login_secret:
        web_login_users = {"root": app.web_login_secret}
    else:
        _flask_app.config["LOGIN_DISABLED"] = True
    if app.debug_web:
        threading.Thread(target=run_web_server, args=(app,)).start()
    else:
        threading.Thread(
            target=get_flask_app().run, daemon=True, args=(app.web_host, app.web_port)
        ).start()


@_flask_app.route("/login", methods=["GET", "POST"])
def login():
    """
    Function to handle the login route.

    Parameters:
    - No parameters

    Returns:
    - If the request method is "POST" and the username and
      password match the ones in the web_login_users dictionary,
      it returns a JSON response with a code of "1".
    - Otherwise, it returns a JSON response with a code of "0".
    - If the request method is not "POST", it returns the rendered "login.html" template.
    """
    if request.method == "POST":
        username = "root"
        web_login_form = {}
        for key, value in request.form.items():
            if value:
                value = deAesCrypt.decrypt(value)
            web_login_form[key] = value

        if not web_login_form.get("password"):
            return jsonify({"code": "0"})

        password = web_login_form["password"]
        if username in web_login_users and web_login_users[username] == password:
            user = User()
            login_user(user)
            return jsonify({"code": "1"})

        return jsonify({"code": "0"})

    return render_template("login.html")


@_flask_app.route("/")
@login_required
def index():
    """Index html"""
    return render_template(
        "index.html",
        download_state=(
            "pause" if get_download_state() is DownloadState.Downloading else "continue"
        ),
    )


@_flask_app.route("/get_download_status")
@login_required
def get_download_speed():
    """Get download speed"""
    return (
        '{ "download_speed" : "'
        + format_byte(get_total_download_speed())
        + '/s" , "upload_speed" : "0.00 B/s" } '
    )


@_flask_app.route("/set_download_state", methods=["POST"])
@login_required
def web_set_download_state():
    """Set download state"""
    state = request.args.get("state")

    if state == "continue" and get_download_state() is DownloadState.StopDownload:
        set_download_state(DownloadState.Downloading)
        return "pause"

    if state == "pause" and get_download_state() is DownloadState.Downloading:
        set_download_state(DownloadState.StopDownload)
        return "continue"

    return state


@_flask_app.route("/get_app_version")
def get_app_version():
    """Get telegram_media_downloader version"""
    return utils.__version__


@_flask_app.route("/get_download_list")
@login_required
def get_download_list():
    """get download list"""
    if request.args.get("already_down") is None:
        return "[]"

    already_down = request.args.get("already_down") == "true"

    download_result = get_download_result()
    result = "["
    for chat_id, messages in list(download_result.items()):
        for idx, value in list(messages.items()):
            is_already_down = value["down_byte"] == value["total_size"]

            if already_down and not is_already_down:
                continue

            if result != "[":
                result += ","
            download_speed = format_byte(value["download_speed"]) + "/s"
            result += (
                '{ "chat":"'
                + f"{chat_id}"
                + '", "id":"'
                + f"{idx}"
                + '", "filename":"'
                + os.path.basename(value["file_name"])
                + '", "total_size":"'
                + f'{format_byte(value["total_size"])}'
                + '" ,"download_progress":"'
            )
            result += (
                f'{round(value["down_byte"] / value["total_size"] * 100, 1)}'
                + '" ,"download_speed":"'
                + download_speed
                + '" ,"save_path":"'
                + value["file_name"].replace("\\", "/")
                + '"}'
            )

    result += "]"
    return result


@_flask_app.route("/bot/tasks")
@login_required
def bot_tasks():
    """Get bot task list"""
    return jsonify({"code": 0, "data": _build_task_list()})


@_flask_app.route("/bot/stop", methods=["POST"])
@login_required
def bot_stop_task():
    """Stop bot task"""
    task_id = request.args.get("task_id", "all")
    bot = _get_bot_instance()
    bot.stop_task(task_id)
    return jsonify({"code": 0, "message": "ok"})


@_flask_app.route("/bot/download", methods=["POST"])
@login_required
def bot_download_task():
    """Create download task from web"""
    bot = _get_bot_instance()
    if not bot.bot:
        return jsonify({"code": 1, "message": "bot is not running"})

    chat_link = request.form.get("chat_link", "").strip()
    start_id = _safe_int(request.form.get("start_id", "1"), 1)
    end_id = _safe_int(request.form.get("end_id", "0"), 0)
    download_filter = request.form.get("download_filter", "").strip()

    if not chat_link:
        return jsonify({"code": 1, "message": "chat_link is required"})

    cmd = f"/download {chat_link} {start_id} {end_id}"
    if download_filter:
        cmd += f" {download_filter}"

    async def _submit():
        from types import SimpleNamespace
        from module.bot import download_from_bot  # pylint: disable=C0415

        user_id = bot.allowed_user_ids[0] if bot.allowed_user_ids else 0
        user = SimpleNamespace(id=user_id)
        fake_message = SimpleNamespace(
            text=cmd,
            from_user=user,
            id=0,
            chat=SimpleNamespace(id=user.id),
        )
        await download_from_bot(bot.bot, fake_message)

    bot.app.loop.call_soon_threadsafe(lambda: bot.app.loop.create_task(_submit()))
    return jsonify({"code": 0, "message": "submitted"})


@_flask_app.route("/bot/forward", methods=["POST"])
@login_required
def bot_forward_task():
    """Create forward task from web"""
    bot = _get_bot_instance()
    if not bot.bot:
        return jsonify({"code": 1, "message": "bot is not running"})

    src_link = request.form.get("src_link", "").strip()
    dst_link = request.form.get("dst_link", "").strip()
    start_id = _safe_int(request.form.get("start_id", "1"), 1)
    end_id = _safe_int(request.form.get("end_id", "0"), 0)
    download_filter = request.form.get("download_filter", "").strip()

    if not src_link or not dst_link:
        return jsonify({"code": 1, "message": "src_link and dst_link are required"})

    cmd = f"/forward {src_link} {dst_link} {start_id} {end_id}"
    if download_filter:
        cmd += f" {download_filter}"

    async def _submit():
        from types import SimpleNamespace
        from module.bot import forward_messages  # pylint: disable=C0415

        user_id = bot.allowed_user_ids[0] if bot.allowed_user_ids else 0
        user = SimpleNamespace(id=user_id)
        fake_message = SimpleNamespace(
            text=cmd,
            from_user=user,
            id=0,
            chat=SimpleNamespace(id=user.id),
        )
        await forward_messages(bot.bot, fake_message)

    bot.app.loop.call_soon_threadsafe(lambda: bot.app.loop.create_task(_submit()))
    return jsonify({"code": 0, "message": "submitted"})
