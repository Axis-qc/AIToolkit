"""
桌面工具核心模块——截屏、模拟点击、模拟输入。

通过 pyautogui 直接操作本机屏幕与键鼠，供 MCP 工具（mcp_server.py）调用。
截屏返回 PIL Image，由 FastMCP 转成 image 内容块回传给模型。

坐标体系：进程首次调用任一工具时开启 DPI 感知，物理像素与 pyautogui.size() 一致。
click_screen 默认接受「最后一次截图上的坐标」并自动换算，space="physical" 走物理像素。
安全阀：pyautogui 自带 failsafe（鼠标甩到屏幕左上角立即中止后续操作），保留默认开启。
"""
import logging
import threading

logger = logging.getLogger(__name__)

_dpi_lock = threading.Lock()
_dpi_done = False

_shot_lock = threading.Lock()
_last_shot_size: tuple[int, int] | None = None  # 最后一次截图返回给模型的图片尺寸


def _ensure_dpi_awareness() -> None:
    """开启进程级 DPI 感知，保证坐标与物理像素一致（高缩放屏必需）。

    SetProcessDpiAwareness 在进程内成功一次即可，重复调用会报错，故加锁并记忆。
    """
    global _dpi_done
    if _dpi_done:
        return
    with _dpi_lock:
        if _dpi_done:
            return
        try:
            import ctypes
            try:
                # PER_MONITOR_DPI_AWARE
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                # 低版本 Windows 没有该 API，退回系统级
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception as e:
            logger.warning(f"DPI 感知设置失败（可忽略）: {e}")
        _dpi_done = True


def take_screenshot(max_width: int | None = 1280):
    """截取整个屏幕，返回 (PIL.Image, 信息 dict)。

    max_width 为正数且图片更宽时按比例缩放，用于省上下文 token。
    截图尺寸记入模块状态，click_screen 的 shot 坐标空间以它为基准换算。
    """
    global _last_shot_size
    _ensure_dpi_awareness()
    import pyautogui

    shot = pyautogui.screenshot()  # RGB PIL Image
    screen_w, screen_h = pyautogui.size()
    info = {
        "screen": [screen_w, screen_h],
        "image_size": [shot.width, shot.height],
        "scaled": False,
    }
    if max_width and shot.width > max_width:
        ratio = max_width / shot.width
        new_h = max(1, round(shot.height * ratio))
        shot = shot.resize((max_width, new_h))
        info["image_size"] = [shot.width, shot.height]
        info["scaled"] = True
    with _shot_lock:
        _last_shot_size = (shot.width, shot.height)
    return shot, info


def _resolve_point(x: int, y: int, space: str = "shot") -> tuple[int, int, str, tuple[int, int]]:
    """把 (x, y) 换算成物理像素并做屏幕范围检查。

    space="shot"（默认）：坐标是最后一次截图上的位置，按截图与屏幕的比例
    自动换算成物理像素，模型看到哪点哪，不用自己做乘法。
    space="physical"：坐标就是物理像素，与 pyautogui.size() 同一体系。
    返回 (物理 x, 物理 y, 换算说明, 屏幕尺寸)。
    """
    import pyautogui

    screen_w, screen_h = pyautogui.size()
    if space == "physical":
        phys_x, phys_y = x, y
        space_note = "physical"
    elif space == "shot":
        with _shot_lock:
            shot_size = _last_shot_size
        if shot_size is None:
            raise ValueError("本会话还没有截图，无法按截图坐标换算；请先调 take_screenshot，或用 space='physical' 传物理坐标")
        phys_x = round(x * screen_w / shot_size[0])
        phys_y = round(y * screen_h / shot_size[1])
        space_note = f"shot {shot_size[0]}x{shot_size[1]} -> physical"
    else:
        raise ValueError(f"space 只接受 shot/physical，收到 {space!r}")

    if not (0 <= phys_x < screen_w and 0 <= phys_y < screen_h):
        raise ValueError(f"换算后坐标超出屏幕范围 (0,0)-({screen_w - 1},{screen_h - 1})")
    return phys_x, phys_y, space_note, (screen_w, screen_h)


def click_screen(
    x: int,
    y: int,
    space: str = "shot",
    duration_ms: int = 120,
    button: str = "left",
    clicks: int = 1,
) -> dict:
    """移动鼠标到指定坐标并点击。

    button：left（默认）/ right / middle。
    clicks：连点次数，2 即双击（同一位置连点两次）。
    返回值带换算后的实际物理落点，可自查。
    """
    _ensure_dpi_awareness()
    if button not in ("left", "right", "middle"):
        raise ValueError(f"button 只接受 left/right/middle，收到 {button!r}")
    if clicks < 1:
        raise ValueError(f"clicks 至少为 1，收到 {clicks}")
    phys_x, phys_y, space_note, screen = _resolve_point(x, y, space)
    import pyautogui

    pyautogui.click(phys_x, phys_y, clicks=clicks, button=button, duration=duration_ms / 1000)
    return {
        "clicked": [phys_x, phys_y],
        "button": button,
        "clicks": clicks,
        "space": space_note,
        "screen": list(screen),
    }


def scroll_screen(clicks_amount: int, x: int | None = None, y: int | None = None, space: str = "shot") -> dict:
    """滚动滚轮。

    clicks_amount 为正向上滚（内容下移），为负向下滚（内容上移），
    单位是滚轮格数；1 格具体滚多少像素由目标应用决定。
    x/y 可省略：省略时在当前鼠标位置滚动；传了则先把鼠标移过去再滚
    （坐标语义与 click_screen 相同，默认按截图坐标换算）。
    """
    _ensure_dpi_awareness()
    if clicks_amount == 0:
        raise ValueError("clicks_amount 不能为 0，正数上滚，负数下滚")
    space_note = "current position"
    if x is not None and y is not None:
        phys_x, phys_y, space_note, screen = _resolve_point(x, y, space)
        import pyautogui

        pyautogui.moveTo(phys_x, phys_y, duration=0.12)
    else:
        import pyautogui

        phys_x, phys_y = pyautogui.position()
        screen = pyautogui.size()
    pyautogui.scroll(clicks_amount)
    return {
        "scrolled": clicks_amount,
        "at": [phys_x, phys_y],
        "space": space_note,
        "screen": list(screen),
    }


def type_text(text: str, interval_s: float = 0.0) -> dict:
    """向当前焦点窗口输入文本。

    纯可打印 ASCII 走 pyautogui 逐键输入；其余（中文、换行等）经剪贴板
    写入后 Ctrl+V 粘贴，约 1 秒后后台恢复剪贴板原内容。
    """
    _ensure_dpi_awareness()
    if not text:
        return {"typed": 0, "mode": "none"}
    if all(ord(c) < 128 and c.isprintable() for c in text):
        import pyautogui
        pyautogui.write(text, interval=interval_s)
        return {"typed": len(text), "mode": "keys"}

    import pyautogui
    import pyperclip

    old = None
    try:
        old = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")

    def _restore():
        import time
        time.sleep(1.0)
        try:
            if old is not None:
                pyperclip.copy(old)
        except Exception:
            pass

    threading.Thread(target=_restore, daemon=True).start()
    return {"typed": len(text), "mode": "clipboard"}
