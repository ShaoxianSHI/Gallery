"""窗口用的那五个控件（ADR 0059 第二步、ADR 0060 补一个）。

**它们与 mixin 是两种东西**：这五个是 **QWidget 子类**，有自己的生命周期与事件处理；
`window/` 下那 18 个是 MainWindow 的行为切片，只借 `self` 用。只被窗口用（实测消费者
只有 `window/`），所以进 `window/`。`breadcrumb` 是 ADR 0060 从 `panels/` 搬进来的。
"""
