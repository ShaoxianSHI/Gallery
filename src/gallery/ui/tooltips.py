"""悬停提示的总闸。

Qt 的悬停提示是一个**顶层窗口**（macOS 上白底，贴在控件旁边），不是画在界面里的。
它有两个麻烦，加在一起就是「打开照片时照片上凭空闪出一个装着元数据的小白窗」：

1. ``QToolTip.hideText()`` 只隐藏**已经显示**的窗口，**不取消已经排好的悬停定时器**。
2. 更要命的是：Qt 根本不检查目标控件还在不在屏幕上。只要 ``ToolTip`` 事件送到了某个控件，
   它就照弹 —— 哪怕这个控件早就随着页面切换被换下屏幕了（实测：网格页已经换成大图页，
   事件送到网格视口，提示照弹，里面是上一张卡片的名字、格式与大小）。

第 2 条才是主犯。悬停是在**鼠标停下那一刻**就排好的，等大图铺上来、网格页被换下去之后，
这条迟到的提示才被送来；用户看到的正是它落在照片原本的位置上。

所以这里装一道总闸：**凡是目标控件不在屏幕上的悬停提示事件，一律不送给它。**

判据只用控件自己的可见性，不用「现在是哪一页」或「模型答不答得出来」—— 后两者都得靠
每条切图路径记得自己处理，漏一次就闪一次。数据侧那道防线
（``ui.browser.model.PhotoListModel.set_tooltips_enabled``）仍然留着：它治的是另一类
竞态（事件准时送到、只是内容该为空），两道防线各治一类。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QToolTip, QWidget

#: 总闸在 QApplication 上的属性名。装第二次时靠它认出「已经装过了」——
#: 同一个进程里反复装只会多挂一个事件过滤器，白白发事件。
_GATE_ATTRIBUTE = "_gallery_tooltip_gate"


class ToolTipGate(QObject):
    """拦住指向「不在屏幕上」的控件的悬停提示事件。

    拦在**应用级**事件过滤器上而不是某个控件上：迟到的提示事件是由平台按「当时鼠标在
    哪里」投递的，它的目标控件可能早在这一轮事件处理里就被换下屏幕了，只有在事件真正
    到达时问一句「你现在还看得见吗」才拦得住。挂在控件上的过滤器反而会跟着控件一起失效。
    """

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        app.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt 命名
        if event.type() != QEvent.Type.ToolTip or not isinstance(obj, QWidget):
            return False
        # 在屏幕上的控件照常给提示（那是功能）；不在屏幕上的吞掉 —— 这就是这里要的全部。
        # 不必顺手 hideText：那个窗口要么已经被 :func:`dismiss` 收掉了，要么根本还没出现；
        # 顺手去动它反而会误伤此刻正当正当显示着的另一个提示。
        return not obj.isVisible()


def install(app: QApplication | None = None) -> ToolTipGate:
    """装一次总闸，返回它（幂等）。"""
    application = app if app is not None else QGuiApplication.instance()
    if not isinstance(application, QApplication):
        raise RuntimeError("悬停提示总闸要装在 QApplication 上")
    existing = application.property(_GATE_ATTRIBUTE)
    if isinstance(existing, ToolTipGate):
        return existing
    gate = ToolTipGate(application)
    application.setProperty(_GATE_ATTRIBUTE, gate)
    return gate


def dismiss() -> None:
    """收掉此刻显示着的提示窗口。

    与 :func:`install` 是两件事：总闸保证「迟到的请求不会被受理」，而这里保证「已经显示
    的那个立刻下台」。换页时两者都要做，前者治将来、后者治当下。
    """
    QToolTip.hideText()


__all__ = ["ToolTipGate", "dismiss", "install"]
