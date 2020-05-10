import ntpath
from pathlib import Path
from threading import Thread
from pprint import pprint
import os
from functools import partial
from circleguard import Circleguard, ReplayPath, Mod
from ossapi import ossapi
import json

from PyQt5.QtWidgets import (QWidget, QGridLayout, QLabel, QLineEdit, QMessageBox,
                             QSpacerItem, QSizePolicy, QSlider, QSpinBox, QFrame,
                             QDoubleSpinBox, QFileDialog, QPushButton, QCheckBox, QComboBox, QVBoxLayout, QScrollArea)
from PyQt5.QtGui import QRegExpValidator, QIcon, QDrag
from PyQt5.QtCore import QRegExp, Qt, QCoreApplication, pyqtSignal, QPoint, QMimeData

from settings import get_setting, reset_defaults, LinkableSetting, set_setting
from utils import resource_path, delete_widget, score_to_acc, retrying_request, cached_beatmap_lookup

SPACER = QSpacerItem(100, 0, QSizePolicy.Maximum, QSizePolicy.Minimum)


def set_event_window(window):
    """
    To emulate keypresses, we need a window to send the keypress to.
    This main window is created in gui.pyw, so we need to set it here as well.
    """
    global WINDOW
    WINDOW = window


class LineEdit(QLineEdit):
    r"""
    A QLineEdit that overrides the keyPressEvent to allow the left and right
    keys to be sent to our window that controls shortcuts, instead of being used only by the LineEdit.
    """
    def __init__(self, parent):
        super().__init__(parent)
        # save current stylesheet for resetting highlighted style. Don't
        # want to reset to an empty string because our stylesheet may cascade
        # down to here in the future instead of being empty
        self.old_stylesheet = self.styleSheet()
        self.highlighted = False

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Left or key == Qt.Key_Right:
            QCoreApplication.sendEvent(WINDOW, event)
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self.highlighted:
            self.setStyleSheet(self.old_stylesheet)
            self.highlighted = False

    def show_required(self):
        self.setStyleSheet(get_setting("required_style"))
        self.highlighted = True


class PasswordEdit(LineEdit):
    r"""
    A LineEdit that overrides focusInEvent and focusOutEven to show/hide the password on focus.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.Password)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.setEchoMode(QLineEdit.Normal)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setEchoMode(QLineEdit.Password)

class IDLineEdit(LineEdit):
    r"""
    A LineEdit that does not allow anything but digits to be entered.
    Specifically, anything not matched by regex ``\d*`` is not registered.
    """

    def __init__(self, parent):
        super().__init__(parent)
        # r prefix isn't necessary but pylint was annoying
        validator = QRegExpValidator(QRegExp(r"\d*"))
        self.setValidator(validator)


class SpinBox(QSpinBox):
    """
    A QSpinBox that overrides the keyPressEvent to allow the left and right
    keys to be sent to our window that controls shortcuts, instead of being used only by the SpinBox.
    """

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Left or key == Qt.Key_Right:
            QCoreApplication.sendEvent(WINDOW, event)
        super().keyPressEvent(event)


class DoubleSpinBox(QDoubleSpinBox):
    """
    A QDoubleSpinBox that overrides the keyPressEvent to allow the left and right
    keys to be sent to our window that controls shortcuts, instead of being used only by the DoubleSpinBox.
    """

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Left or key == Qt.Key_Right:
            QCoreApplication.sendEvent(WINDOW, event)
        super().keyPressEvent(event)


class QHLine(QFrame):
    def __init__(self, shadow=QFrame.Plain):
        super(QHLine, self).__init__()
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(shadow)


class QVLine(QFrame):
    def __init__(self, shadow=QFrame.Plain):
        super(QVLine, self).__init__()
        self.setFrameShape(QFrame.VLine)
        self.setFrameShadow(shadow)


class Separator(QFrame):
    """
    Creates a horizontal line with text in the middle.
    Useful to vertically separate other widgets.
    """

    def __init__(self, title):
        super(Separator, self).__init__()

        label = QLabel(self)
        label.setText(title)
        label.setAlignment(Qt.AlignCenter)

        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(QHLine(), 0, 0, 1, 2)
        self.layout.addWidget(label, 0, 2, 1, 1)
        self.layout.addWidget(QHLine(), 0, 3, 1, 2)
        self.setLayout(self.layout)


class InputWidget(QFrame):
    """
    A container class of widgets that represents user input for an id. This class
    holds a Label and either a PasswordEdit, IDLineEdit, or LineEdit, depending
    on the constructor call. The former two inherit from LineEdit.
    """

    def __init__(self, title, tooltip, type_, compact=False):
        super(InputWidget, self).__init__()
        label = QLabel(self)
        label.setText(title+":")
        label.setToolTip(tooltip)
        if type_ == "password":
            self.field = PasswordEdit(self)
        if type_ == "id":
            self.field = IDLineEdit(self)
        if type_ == "normal":
            self.field = LineEdit(self)
        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(label, 0, 0, 1, 1)
        if compact:
            label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        else:
            self.layout.addItem(SPACER, 0, 1, 1, 1)
        self.layout.addWidget(self.field, 0, 2, 1, 3)
        self.setLayout(self.layout)

    def show_required(self):
        """
        Shows a red border around the LineEdit to indicate a field that must be
        filled in. This border is removed when the LineEdit receieves focus again.
        """
        self.field.show_required()


class IdWidgetCombined(QFrame):
    """
    A container class of widgets that represents user input for a map id and user id.
    If no map id is given the user id field will be disabled.

    This class holds 2 rows of a Label and IDLineEdit.
    """

    def __init__(self):
        super(IdWidgetCombined, self).__init__()

        self.map_id = InputWidget("Map Id", "Beatmap id, not the mapset id!", type_="id")
        self.map_id.field.textChanged.connect(self.update_user_enabled)

        self.user_id = InputWidget("User Id", "User id, as seen in the profile url", type_="id")

        self.update_user_enabled()

        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.map_id, 0, 0, 1, 1)
        self.layout.addWidget(self.user_id, 1, 0, 1, 1)
        self.setLayout(self.layout)

    def update_user_enabled(self):
        """
        Enables the user id field if the map field has any text in it. Otherwise, disables the user id field.
        """
        self.user_id.setEnabled(self.map_id.field.text() != "")

class OptionWidget(QFrame):
    """
    A container class of widgets that represents an option with a boolean state.
    This class holds a Label and CheckBox.
    """

    def __init__(self, title, tooltip, end=":", compact=False):
        QFrame.__init__(self)

        label = QLabel(self)
        label.setText(title + end)
        label.setToolTip(tooltip)
        self.box = QCheckBox(self)
        item = CenteredWidget(self.box)
        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(label, 0, 0, 1, 1)
        if compact:
            label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
            self.layout.addWidget(item, 0, 1, 1, 1, Qt.AlignLeft)
        else:
            item.setFixedWidth(100)
            self.layout.addWidget(item, 0, 1, 1, 1, Qt.AlignRight)
        self.setLayout(self.layout)


class LinkedOptionWidget(LinkableSetting, OptionWidget):
    """
    A container class of widgets that represents an option with a boolean state.
    This class holds a Label and CheckBox.
    """

    def __init__(self, title, tooltip, setting, end=":"):
        """
        String setting: The name of the setting to link this OptionWidget to.
        """
        LinkableSetting.__init__(self, setting)
        OptionWidget.__init__(self, title, tooltip, end=end)
        self.box.setChecked(self.setting_value)
        self.box.stateChanged.connect(self.on_setting_changed_from_gui)

    def on_setting_changed(self, new_value):
        self.box.setChecked(new_value)


class CenteredWidget(QWidget):
    """
    Turns a widget with a fixed size (for example a QCheckBox) into an flexible one which can be affected by the self.layout.
    """

    def __init__(self, widget):
        super().__init__()
        self.layout = QGridLayout()
        self.layout.setAlignment(Qt.AlignCenter)
        self.layout.setContentsMargins(0,0,0,0)
        self.setContentsMargins(0,0,0,0)
        self.layout.addWidget(widget)
        self.setLayout(self.layout)


class ButtonWidget(QFrame):
    """
    A container class of widgets that represents a clickable action with a label.
    This class holds a QLabel and QPushButton.
    """

    def __init__(self, label_title, button_title, tooltip, end=":"):
        super(ButtonWidget, self).__init__()

        label = QLabel(self)
        label.setText(label_title + end)
        label.setToolTip(tooltip)
        self.button = QPushButton(button_title)
        self.button.setFixedWidth(100)

        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(label, 0, 0, 1, 1)
        self.layout.addItem(SPACER, 0, 1, 1, 1)
        self.layout.addWidget(self.button, 0, 2, 1, 1)
        self.setLayout(self.layout)


class LoglevelWidget(QFrame):
    def __init__(self, tooltip):
        super(LoglevelWidget, self).__init__()

        level_label = QLabel(self)
        level_label.setText("Debug mode:")
        level_label.setToolTip(tooltip)

        output_label = QLabel(self)
        output_label.setText("Debug Output:")
        output_label.setToolTip(tooltip)

        level_combobox = QComboBox(self)
        level_combobox.setFixedWidth(100)
        level_combobox.addItem("CRITICAL", 50)
        level_combobox.addItem("ERROR", 40)
        level_combobox.addItem("WARNING", 30)
        level_combobox.addItem("INFO", 20)
        level_combobox.addItem("DEBUG", 10)
        level_combobox.addItem("TRACE", 5)
        level_combobox.setInsertPolicy(QComboBox.NoInsert)
        self.level_combobox = level_combobox

        output_combobox = QComboBox(self)
        output_combobox.setFixedWidth(100)
        output_combobox.addItem("NONE")
        output_combobox.addItem("TERMINAL")
        output_combobox.addItem("NEW WINDOW")
        output_combobox.addItem("BOTH")
        output_combobox.setInsertPolicy(QComboBox.NoInsert)
        output_combobox.setCurrentIndex(0) # NONE by default
        self.output_combobox = output_combobox

        self.level_combobox.setCurrentIndex(get_setting("log_mode"))
        self.level_combobox.currentIndexChanged.connect(partial(set_setting, "log_mode"))

        self.output_combobox.setCurrentIndex(get_setting("log_output"))
        self.output_combobox.currentIndexChanged.connect(partial(set_setting, "log_output"))

        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(level_label, 0, 0, 1, 1)
        self.layout.addItem(SPACER, 0, 1, 1, 1)
        self.layout.addWidget(self.level_combobox, 0, 2, 1, 3, Qt.AlignRight)
        self.layout.addWidget(output_label, 1, 0, 1, 1)
        self.layout.addItem(SPACER, 1, 1, 1, 1)
        self.layout.addWidget(self.output_combobox, 1, 2, 1, 3, Qt.AlignRight)

        self.setLayout(self.layout)


class ScrollableWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout()
        self.layout.setAlignment(Qt.AlignTop)
        self.setLayout(self.layout)


# TODO remove
ScrollableLoadablesWidget = ScrollableWidget
ScrollableChecksWidget = ScrollableWidget


class DropArea(QFrame):
    # style largely taken from
    # https://doc.qt.io/qt-5/qtwidgets-draganddrop-dropsite-example.html
    # (we use a QFrame instead of a QLabel so we can have a layout and
    # add new items to it)
    def __init__(self):
        super().__init__()

        self.loadable_ids = [] # ids of loadables already in this drop area
        self.loadables = [] # LoadableInChecks in this DropArea
        self.setMinimumSize(0, 100)
        self.setFrameStyle(QFrame.Sunken | QFrame.StyledPanel)
        self.setAcceptDrops(True)

        self.layout = QVBoxLayout()
        self.layout.setAlignment(Qt.AlignTop)
        self.setLayout(self.layout)

    def dragEnterEvent(self, event):
        # need to accept the event so qt gives us the DropEvent
        # https://doc.qt.io/qt-5/qdropevent.html#details
        event.acceptProposedAction()

    def dropEvent(self, event):
        mimedata = event.mimeData()
        # don't accept drops from anywhere else
        if not mimedata.hasFormat("application/x-circleguard-loadable"):
            return
        event.acceptProposedAction()
        # second #data necessary to convert QByteArray to python byte array
        data = json.loads(mimedata.data("application/x-circleguard-loadable").data())
        id_ = data[0]
        name = data[1]
        if id_ in self.loadable_ids:
            return
        self.loadable_ids.append(id_)
        loadable = LoadableInCheck(name + f" (id: {id_})", id_)
        self.layout.addWidget(loadable)
        self.loadables.append(loadable)
        loadable.remove_loadableincheck_signal.connect(self.remove_loadable)

    def remove_loadable(self, loadable_id, loadable_in_check=True):
        """
        loadable_in_check will be True if loadable_id is a LoadableInCheck id,
        otherwise if loadable_in_check is False it is a Loadable id.
        """
        if loadable_in_check:
            loadables = [l for l in self.loadables if l.loadable_in_check_id == loadable_id]
        else:
            loadables = [l for l in self.loadables if l.loadable_id == loadable_id]
        if not loadables:
            return
        loadable = loadables[0]
        self.layout.removeWidget(loadable)
        delete_widget(loadable)
        self.loadables.remove(loadable)
        self.loadable_ids.remove(loadable.loadable_id)


class CheckW(QFrame):
    remove_check_signal = pyqtSignal(int) # check id
    ID = 0

    def __init__(self, name, double_drop_area=False):
        super().__init__()
        CheckW.ID += 1
        # so we get the DropEvent
        # https://doc.qt.io/qt-5/qdropevent.html#details
        self.setAcceptDrops(True)
        self.name = name
        self.double_drop_area = double_drop_area
        self.check_id = CheckW.ID
        # will have LoadableW objects added to it once this Check is
        # run by the main tab run button, so that cg.Check objects can
        # be easily constructed with loadables
        if double_drop_area:
            self.loadables1 = []
            self.loadables2 = []
        else:
            self.loadables = []

        self.delete_button = QPushButton(self)
        self.delete_button.setIcon(QIcon(str(resource_path("./resources/delete.png"))))
        self.delete_button.setMaximumWidth(30)
        self.delete_button.clicked.connect(partial(lambda check_id: self.remove_check_signal.emit(check_id), self.check_id))
        title = QLabel()
        title.setText(f"{name}")

        self.layout = QGridLayout()
        self.layout.addWidget(title, 0, 0, 1, 7)
        self.layout.addWidget(self.delete_button, 0, 7, 1, 1)
        if double_drop_area:
            self.drop_area1 = DropArea()
            self.drop_area2 = DropArea()
            self.layout.addWidget(self.drop_area1, 1, 0, 1, 4)
            self.layout.addWidget(self.drop_area2, 1, 4, 1, 4)
        else:
            self.drop_area = DropArea()
            self.layout.addWidget(self.drop_area, 1, 0, 1, 8)
        self.setLayout(self.layout)

    def remove_loadable(self, loadable_id):
        if self.double_drop_area:
            self.drop_area1.remove_loadable(loadable_id, loadable_in_check=False)
            self.drop_area2.remove_loadable(loadable_id, loadable_in_check=False)
        else:
            self.drop_area.remove_loadable(loadable_id, loadable_in_check=False)

    def all_loadable_ids(self):
        if self.double_drop_area:
            return self.drop_area1.loadable_ids + self.drop_area2.loadable_ids
        return self.drop_area.loadable_ids

    def all_loadables(self):
        if self.double_drop_area:
            return self.loadables1 + self.loadables2
        return self.loadables

class StealCheckW(CheckW):
    def __init__(self):
        super().__init__("Replay Stealing Check", double_drop_area=True)


class RelaxCheckW(CheckW):
    def __init__(self):
        super().__init__("Relax Check")


class CorrectionCheckW(CheckW):
    def __init__(self):
        super().__init__("Aim Correction Check")

class VisualizerW(CheckW):
    def __init__(self):
        super().__init__("Visualize")


class DragWidget(QFrame):
    """
    A widget not meant to be displayed, but rendered into a pixmap with
    #grab and stuck onto a QDrag with setPixmap to give the illusion of
    dragging another widget.
    """
    def __init__(self, text):
        super().__init__()
        self.text = QLabel(text)
        layout = QVBoxLayout()
        layout.addWidget(self.text)
        self.setLayout(layout)


class LoadableInCheck(QFrame):
    """
    Represents a LoadableW inside a CheckW.
    """
    remove_loadableincheck_signal = pyqtSignal(int)
    ID = 0
    def __init__(self, text, loadable_id):
        super().__init__()
        LoadableInCheck.ID += 1
        self.text = QLabel(text)
        self.loadable_in_check_id = LoadableInCheck.ID
        self.loadable_id = loadable_id

        self.delete_button = QPushButton(self)
        self.delete_button.setIcon(QIcon(str(resource_path("./resources/delete.png"))))
        self.delete_button.setMaximumWidth(30)
        self.delete_button.clicked.connect(partial(lambda id_: self.remove_loadableincheck_signal.emit(id_), self.loadable_in_check_id))

        self.layout = QGridLayout()
        self.layout.addWidget(self.text, 0, 0, 1, 7)
        self.layout.addWidget(self.delete_button, 0, 7, 1, 1)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.layout)


class LoadableW(QFrame):
    """
    A widget representing a circleguard.Loadable, which can be dragged onto
    a CheckW. Keeps track of how many LoadableWs have been created
    as a static ID attribute.

    W standing for widget.
    """
    ID = 0
    remove_loadable_signal = pyqtSignal(int) # id of loadable to remove

    def __init__(self, name, required_input_widgets):
        super().__init__()
        LoadableW.ID += 1
        self.name = name
        self.required_input_widgets = required_input_widgets

        self.layout = QGridLayout()
        title = QLabel(self)
        self.loadable_id = LoadableW.ID
        t = "\t" # https://stackoverflow.com/a/44780467/12164878
        # double tabs on short names to align with longer ones
        title.setText(f"{name}{t+t if len(name) < 5 else t}(id: {self.loadable_id})")

        self.delete_button = QPushButton(self)
        self.delete_button.setIcon(QIcon(str(resource_path("./resources/delete.png"))))
        self.delete_button.setMaximumWidth(30)
        self.delete_button.clicked.connect(partial(lambda loadable_id: self.remove_loadable_signal.emit(loadable_id), self.loadable_id))
        self.layout.addWidget(title, 0, 0, 1, 7)
        self.layout.addWidget(self.delete_button, 0, 7, 1, 1)
        self.setLayout(self.layout)

    # Resources for drag and drop operations:
    # qt tutorial           https://doc.qt.io/qt-5/qtwidgets-draganddrop-draggableicons-example.html
    # qdrag docs            https://doc.qt.io/qt-5/qdrag.html
    # real example code     https://lists.qt-project.org/pipermail/qt-interest-old/2011-June/034531.html
    # bad example code      https://stackoverflow.com/q/7737913/12164878
    def mouseMoveEvent(self, event):
        # 1=all the way to the right/down, 0=all the way to the left/up
        x_ratio = event.pos().x() / self.width()
        y_ratio = event.pos().y() / self.height()
        self.drag = QDrag(self)
        # https://stackoverflow.com/a/53538805/12164878
        pixmap = DragWidget(f"{self.name} (Id: {self.loadable_id})").grab()
        # put cursor in the same relative position on the dragwidget as
        # it clicked on the real Loadable widget.
        self.drag.setHotSpot(QPoint(pixmap.width() * x_ratio, pixmap.height() * y_ratio))
        self.drag.setPixmap(pixmap)
        mime_data = QMimeData()
        data = [self.loadable_id, self.name]
        mime_data.setData("application/x-circleguard-loadable", bytes(json.dumps(data), "utf-8"))
        self.drag.setMimeData(mime_data)
        self.drag.exec() # start the drag

    def check_required_fields(self):
        """
        Checks the required fields of this LoadableW. If any are empty, show
        a red border around them (see InputWidget#show_required) and return
        False. Otherwise, return True.
        """
        all_filled = True
        for input_widget in self.required_input_widgets:
            # don't count inputs with defaults as empty
            filled = input_widget.field.text() != "" or input_widget.field.placeholderText() != ""
            if not filled:
                input_widget.show_required()
                all_filled = False
        return all_filled

class ReplayMapW(LoadableW):
    """
    W standing for Widget.
    """
    def __init__(self):
        self.map_id_input = InputWidget("Map id", "", "id")
        self.user_id_input = InputWidget("User id", "", "id")
        self.mods_input = InputWidget("Mods (opt.)", "", "normal")

        super().__init__("Map Replay", [self.map_id_input, self.user_id_input])

        self.layout.addWidget(self.map_id_input, 1, 0, 1, 8)
        self.layout.addWidget(self.user_id_input, 2, 0, 1, 8)
        self.layout.addWidget(self.mods_input, 3, 0, 1, 8)


class ReplayPathW(LoadableW):
    def __init__(self):
        self.path_input = FolderChooser(".osr path", folder_mode=False, file_ending="osu! Replayfile (*.osr)")

        super().__init__("Local Replay", [self.path_input])

        self.layout.addWidget(self.path_input, 1, 0, 1, 8)

    def check_required_fields(self):
        all_filled = True
        for input_widget in self.required_input_widgets:
            filled = input_widget.changed
            if not filled:
                input_widget.show_required()
                all_filled = False
        return all_filled

class MapW(LoadableW):
    def __init__(self):

        self.map_id_input = InputWidget("Map id", "", "id")
        self.span_input = InputWidget("Span", "", "normal")
        self.span_input.field.setPlaceholderText("1-50")
        self.mods_input = InputWidget("Mods (opt.)", "", "normal")

        super().__init__("Map", [self.map_id_input, self.span_input])

        self.layout.addWidget(self.map_id_input, 1, 0, 1, 8)
        self.layout.addWidget(self.span_input, 2, 0, 1, 8)
        self.layout.addWidget(self.mods_input, 3, 0, 1, 8)

class UserW(LoadableW):
    def __init__(self):

        self.user_id_input = InputWidget("User id", "", "id")
        self.span_input = InputWidget("Span", "", "normal")
        self.mods_input = InputWidget("Mods (opt.)", "", "normal")

        super().__init__("User", [self.user_id_input, self.span_input])

        self.layout.addWidget(self.user_id_input, 1, 0, 1, 8)
        self.layout.addWidget(self.span_input, 2, 0, 1, 8)
        self.layout.addWidget(self.mods_input, 3, 0, 1, 8)

class MapUserW(LoadableW):
    def __init__(self):
        self.map_id_input = InputWidget("Map id", "", "id")
        self.user_id_input = InputWidget("User id", "", "id")
        self.span_input = InputWidget("Span", "", "normal")
        self.span_input.field.setPlaceholderText("all")

        super().__init__("All Map Replays by User", [self.map_id_input, self.user_id_input, self.span_input])

        self.layout.addWidget(self.map_id_input, 1, 0, 1, 8)
        self.layout.addWidget(self.user_id_input, 2, 0, 1, 8)
        self.layout.addWidget(self.span_input, 3, 0, 1, 8)



class ResultW(QFrame):
    """
    Stores the result of a comparison that can be replayed at any time.
    Contains a QLabel, QPushButton (visualize) and QPushButton (copy to clipboard).
    """

    def __init__(self, text, result, replays):
        super().__init__()
        self.result = result
        self.replays = replays
        self.label = QLabel(self)
        self.label.setText(text)

        self.button = QPushButton(self)
        self.button.setText("Visualize")

        self.button_clipboard = QPushButton(self)
        self.button_clipboard.setText("Copy Template")

        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.label, 0, 0, 1, 1)
        self.layout.addItem(SPACER, 0, 1, 1, 1)
        self.layout.addWidget(self.button, 0, 2, 1, 1)
        self.layout.addWidget(self.button_clipboard, 0, 3, 1, 1)

        self.setLayout(self.layout)

class RunWidget(QFrame):
    """
    A single run with QLabel displaying a state (either queued, finished,
    loading replays, comparing, or canceled), and a cancel QPushButton
    if not already finished or canceled.
    """

    def __init__(self, run):
        super().__init__()

        self.status = "Queued"
        self.label = QLabel(self)
        self.text = f"Run with {len(run.checks)} Checks"
        self.label.setText(self.text)

        self.status_label = QLabel(self)
        self.status_label.setText("<b>Status: " + self.status + "</b>")
        self.status_label.setTextFormat(Qt.RichText) # so we can bold it
        self.button = QPushButton(self)
        self.button.setText("Cancel")
        self.button.setFixedWidth(50)
        self.label.setFixedHeight(self.button.size().height()*0.75)

        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.label, 0, 0, 1, 1)
        self.layout.addWidget(self.status_label, 0, 1, 1, 1)
        # needs to be redefined because RunWidget is being called from a
        # different thread or something? get weird errors when not redefined
        SPACER = QSpacerItem(100, 0, QSizePolicy.Maximum, QSizePolicy.Minimum)
        self.layout.addItem(SPACER, 0, 2, 1, 1)
        self.layout.addWidget(self.button, 0, 3, 1, 1)
        self.setLayout(self.layout)

    def update_status(self, status):
        if status in ["Finished", "Invalid arguments"]:
            # not a qt function, pyqt's implementation of deleting a widget
            self.button.deleteLater()

        self.status = status
        self.status_label.setText("<b>Status: " + self.status + "</b>")

    def cancel(self):
        self.status = "Canceled"
        self.button.deleteLater()
        self.status_label.setText("<b>Status: " + self.status + "</b>")



class SliderBoxSetting(LinkableSetting, QFrame):
    """
    A container class of a QLabel, QSlider, and SpinBox, and links the slider
    and spinbox to a setting (ie the default values of the slider and spinbox
    will be the value of the setting, and changes made to the slider or
    spinbox will affect the setting).
    """

    def __init__(self, display, tooltip, setting, max_):
        LinkableSetting.__init__(self, setting)
        QFrame.__init__(self)

        label = QLabel(self)
        label.setText(display)
        label.setToolTip(tooltip)
        self.label = label

        slider = QSlider(Qt.Horizontal)
        slider.setFocusPolicy(Qt.ClickFocus)
        slider.setRange(0, max_)
        slider.setValue(self.setting_value)
        self.slider = slider

        spinbox = SpinBox(self)
        spinbox.setRange(0, max_)
        spinbox.setSingleStep(1)
        spinbox.setFixedWidth(100)
        spinbox.setValue(self.setting_value)
        spinbox.setAlignment(Qt.AlignCenter)
        self.spinbox = spinbox
        self.combined = WidgetCombiner(slider, spinbox)

        self.slider.valueChanged.connect(self.on_setting_changed_from_gui)
        self.spinbox.valueChanged.connect(self.on_setting_changed_from_gui)

        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(label, 0, 0, 1, 1)
        self.layout.addItem(SPACER, 0, 1, 1, 1)
        self.layout.addWidget(self.combined, 0, 2, 1, 3)

        self.setLayout(self.layout)

    def on_setting_changed(self, new_value):
        self.slider.setValue(new_value)
        self.spinbox.setValue(new_value)

class LineEditSetting(LinkableSetting, QFrame):
    """
    A container class of a QLabel and InputWidget that links the input widget
    to a setting (ie the default value of the widget will be the value of the
    setting, and changes made to the widget will affect the setting).
    """
    def __init__(self, display, tooltip, type_, setting):
        LinkableSetting.__init__(self, setting)
        QFrame.__init__(self)
        self.input_ = InputWidget(display, tooltip, type_=type_)
        self.input_.field.setText(self.setting_value)
        self.input_.field.textChanged.connect(self.on_setting_changed_from_gui)
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.input_)
        self.setLayout(self.layout)

    def on_setting_changed(self, new_value):
        self.input_.field.setText(new_value)

class WidgetCombiner(QFrame):
    def __init__(self, widget1, widget2):
        super(WidgetCombiner, self).__init__()
        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(widget1, 0, 0, 1, 1)
        self.layout.addWidget(widget2, 0, 1, 1, 1)
        self.setLayout(self.layout)


class FolderChooser(QFrame):
    path_signal = pyqtSignal(object) # an iterable if multiple_files is True, str otherwise

    def __init__(self, title, path=str(Path.home()), folder_mode=True, multiple_files=False, file_ending="osu! Replayfile (*.osr)", display_path=True, compact=False):
        super(FolderChooser, self).__init__()
        self.default_path = path
        self.path = path
        self.display_path = display_path
        self.folder_mode = folder_mode
        self.multiple_files = multiple_files
        self.file_ending = file_ending
        self.label = QLabel(self)
        self.label.setText(title + ":")

        self.file_chooser_button = QPushButton(self)
        type_ = "Folder" if self.folder_mode else "Files" if self.multiple_files else "File"
        self.file_chooser_button.setText("Choose " + type_)
        # if we didn't have this line only clicking on the label would unhighlight,
        # since the button steals the mouse clicked event
        self.file_chooser_button.clicked.connect(self.reset_highlight)
        self.file_chooser_button.clicked.connect(self.set_dir)

        self.file_chooser_button.setFixedWidth(100)

        self.path_label = QLabel(self)
        if self.display_path:
            self.path_label.setText(path)

        if compact:
            self.combined = WidgetCombiner(self.file_chooser_button, self.path_label)
        else:
            self.combined = WidgetCombiner(self.path_label, self.file_chooser_button)

        self.old_stylesheet = self.combined.styleSheet() # for mousePressedEvent / show_required

        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.label, 0, 0, 1, 1)
        if not compact:
            self.layout.addItem(SPACER, 0, 1, 1, 1)
        self.layout.addWidget(self.combined, 0, 2, 1, 3)
        self.setLayout(self.layout)
        self.switch_enabled(True)

    def set_dir(self):
        parent_path_old = self.path if self.folder_mode else str(Path(self.path[0]).parent)
        if self.folder_mode:
            options = QFileDialog.Option()
            options |= QFileDialog.ShowDirsOnly
            options |= QFileDialog.HideNameFilterDetails
            update_path = QFileDialog.getExistingDirectory(caption="Select Folder", directory=parent_path_old, options=options)
        elif self.multiple_files:
            paths = QFileDialog.getOpenFileNames(caption="Select Files", directory=parent_path_old, filter=self.file_ending)
            # qt returns a list of ([path, path, ...], filter) when we use a filter
            update_path = paths[0]
        else:
            paths = QFileDialog.getOpenFileName(caption="Select File", directory=parent_path_old, filter=self.file_ending)
            update_path = paths[0]

        # dont update path if cancel is pressed
        if update_path != [] and update_path != "":
            self.update_dir(update_path)

    def update_dir(self, path):
        self.path = path if path != "" else self.path
        self.changed = True if self.path != self.default_path else False
        if self.display_path:
            if self.multiple_files:
                label = str(Path(self.path).parent)
            elif self.folder_mode:
                label = str(self.path)
            else:
                label = str(ntpath.basename(self.path))
            label = label[:50] + '...' if len(label) > 50 else label
            self.path_label.setText(label)
        self.path_signal.emit(self.path)

    def switch_enabled(self, state):
        self.label.setStyleSheet("color:grey" if not state else "")
        self.path_label.setStyleSheet("color:grey" if not state else "")
        self.file_chooser_button.setEnabled(state)

    def show_required(self):
        self.combined.setStyleSheet(get_setting("required_style"))
        self.highlighted = True

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.reset_highlight()

    # separate function so we can call this method outside of mousePressEvent
    def reset_highlight(self):
        if self.highlighted:
            self.combined.setStyleSheet(self.old_stylesheet)
            self.highlighted = False


class ResetSettings(QFrame):
    def __init__(self):
        super(ResetSettings, self).__init__()
        self.label = QLabel(self)
        self.label.setText("Reset settings:")

        self.button = QPushButton(self)
        self.button.setText("Reset")
        self.button.clicked.connect(self.reset_settings)
        self.button.setFixedWidth(100)

        self.layout = QGridLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.label, 0, 0, 1, 1)
        self.layout.addItem(SPACER, 0, 1, 1, 1)
        self.layout.addWidget(self.button, 0, 2, 1, 1)
        self.setLayout(self.layout)

    def reset_settings(self):
        prompt = QMessageBox.question(self, "Reset settings",
                                      "Are you sure?\n"
                                      "This will reset all settings to their default value, "
                                      "and the application will quit.",
                                      buttons=QMessageBox.No | QMessageBox.Yes,
                                      defaultButton=QMessageBox.No)
        if prompt == QMessageBox.Yes:
            reset_defaults()
            QCoreApplication.quit()


class EntryWidget(QFrame):
    pressed_signal = pyqtSignal(object)
    """
    Represents a single entry of some kind of data, consisting of a title, a button and the data which is stored at self.data.
    When the button is pressed, pressed_signal is emitted with the data for ease of use.
    """
    def __init__(self, title, action_name, data=None):
        super().__init__()
        self.data = data
        self.button = QPushButton(action_name)
        self.button.setFixedWidth(100)
        self.button.clicked.connect(self.button_pressed)
        self.layout = QGridLayout()
        self.layout.addWidget(QLabel(title), 0, 0, 1, 1)
        self.layout.addWidget(self.button, 0, 1, 1, 1)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.layout)

    def button_pressed(self, _):
        self.pressed_signal.emit(self.data)


class VisualizerControls(QFrame):
    def __init__(self, speed):
        super().__init__()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setValue(0)
        self.slider.setFixedHeight(20)
        self.slider.setStyleSheet("outline: none;")

        self.play_reverse_button = QPushButton()
        self.play_reverse_button.setIcon(QIcon(str(resource_path("./resources/play_reverse.png"))))
        self.play_reverse_button.setFixedSize(20, 20)
        self.play_reverse_button.setToolTip("Plays visualization in reverse")

        self.play_normal_button = QPushButton()
        self.play_normal_button.setIcon(QIcon(str(resource_path("./resources/play_normal.png"))))
        self.play_normal_button.setFixedSize(20, 20)
        self.play_normal_button.setToolTip("Plays visualization in normally")

        self.next_frame_button = QPushButton()
        self.next_frame_button.setIcon(QIcon(str(resource_path("./resources/frame_next.png"))))
        self.next_frame_button.setFixedSize(20, 20)
        self.next_frame_button.setToolTip("Displays next frame")

        self.previous_frame_button = QPushButton()
        self.previous_frame_button.setIcon(QIcon(str(resource_path("./resources/frame_back.png"))))
        self.previous_frame_button.setFixedSize(20, 20)
        self.previous_frame_button.setToolTip("Displays previous frame")

        self.pause_button = QPushButton()
        self.pause_button.setIcon(QIcon(str(resource_path("./resources/pause.png"))))
        self.pause_button.setFixedSize(20, 20)
        self.pause_button.setToolTip("Pause visualization")

        self.speed_up_button = QPushButton()
        self.speed_up_button.setIcon(QIcon(str(resource_path("./resources/speed_up.png"))))
        self.speed_up_button.setFixedSize(20, 20)
        self.speed_up_button.setToolTip("Speed up")

        self.speed_down_button = QPushButton()
        self.speed_down_button.setIcon(QIcon(str(resource_path("./resources/speed_down.png"))))
        self.speed_down_button.setFixedSize(20, 20)
        self.speed_down_button.setToolTip("Speed down")

        self.speed_label = QLabel(str(speed) + "x")
        self.speed_label.setFixedSize(40, 20)
        self.speed_label.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)

        self.layout = QGridLayout()
        self.layout.addWidget(self.play_reverse_button, 16, 0, 1, 1)
        self.layout.addWidget(self.previous_frame_button, 16, 1, 1, 1)
        self.layout.addWidget(self.pause_button, 16, 2, 1, 1)
        self.layout.addWidget(self.next_frame_button, 16, 3, 1, 1)
        self.layout.addWidget(self.play_normal_button, 16, 4, 1, 1)
        self.layout.addWidget(self.slider, 16, 5, 1, 9)
        self.layout.addWidget(self.speed_label, 16, 14, 1, 1)
        self.layout.addWidget(self.speed_down_button, 16, 15, 1, 1)
        self.layout.addWidget(self.speed_up_button, 16, 16, 1, 1)
        self.layout.setContentsMargins(5, 0, 5, 5)
        self.setLayout(self.layout)
        self.setFixedHeight(25)


class CheckSelector(QFrame):
    def __init__(self):
        super().__init__()
        label = QLabel("Active Detects:")
        self.steal = OptionWidget("Steal/Remod", "TODO", compact=True)
        self.steal.box.setChecked(True)
        self.relax = OptionWidget("Relax", "TODO", compact=True)
        self.relax.box.setChecked(True)
        self.aim = OptionWidget("Aimcorrect", "TODO", compact=True)
        self.aim.box.setChecked(True)
        spacer = QSpacerItem(10, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.layout = QGridLayout()
        self.layout.setVerticalSpacing(10)
        self.layout.addWidget(label, 0, 0, 1, 2)
        self.layout.addItem(spacer, 1, 0, 1, 1)
        self.layout.addWidget(self.steal, 1, 1, 1, 1)
        self.layout.addItem(spacer, 1, 2, 1, 1)
        self.layout.addWidget(self.relax, 1, 3, 1, 1)
        self.layout.addItem(spacer, 1, 4, 1, 1)
        self.layout.addWidget(self.aim, 1, 5, 1, 1)
        self.layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.setLayout(self.layout)


class MapSettings(QFrame):
    def __init__(self):
        super(MapSettings, self).__init__()
        label = QLabel("Per Map Settings:")
        self.bm_range = InputWidget("Range", "", "normal", compact=True)
        self.bm_range.field.setPlaceholderText("1-50")

        self.layout = QGridLayout()
        self.layout.setVerticalSpacing(10)
        self.layout.setColumnMinimumWidth(0, 10)
        self.layout.addWidget(label, 0, 0, 1, 3)
        self.layout.addWidget(self.bm_range, 1, 1, 1, 2)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.setLayout(self.layout)


class CompareMode(QFrame):
    def __init__(self):
        super().__init__()
        label = QLabel("Compare Mode:")
        self.option = OptionWidget("Only compare against/check added replays", "TODO", compact=True)
        spacer = QSpacerItem(10, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.layout = QGridLayout()
        self.layout.setVerticalSpacing(10)
        self.layout.addWidget(label, 0, 0, 1, 2)
        self.layout.addItem(spacer, 1, 0, 1, 1)
        self.layout.addWidget(self.option, 1, 1, 1, 1)
        self.layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.setLayout(self.layout)


class EntryWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.delete_button = QPushButton(self)
        self.delete_button.setIcon(QIcon(str(resource_path("./resources/delete.png"))))
        self.delete_button.setMaximumWidth(30)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("QLabel{color:red;}")
        self.error_label.setHidden(True)

        spacer = QSpacerItem(25, 0, QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.layout = QGridLayout()
        self.layout.setHorizontalSpacing(10)
        self.layout.addItem(spacer, 0, 3, 1, 1)
        self.layout.addWidget(self.error_label, 0, 4, 1, 1)
        self.layout.addWidget(self.delete_button, 0, 5, 1, 1)

    def show_error(self, error_messages):
        self.error_label.setHidden(False)
        if len(error_messages) > 1:
            self.error_label.setText(f"{len(error_messages)} Errors found")
            self.error_label.setToolTip("\n".join(error_messages))
        else:
            self.error_label.setText(error_messages[0])
            self.error_label.setToolTip("")
        self.setStyleSheet("EntryWidget {border: 2px solid red;}")

    def hide_error(self):
        self.error_label.setHidden(True)
        self.setStyleSheet("")
        self.error_label.setToolTip("")


class UserWidget(EntryWidget):
    def __init__(self):
        super().__init__()
        self.user_id_input = InputWidget("User id", "", "id", compact=True)
        self.user_id_input.setFixedWidth(200)
        self.replay_mode = OptionWidget("All Replays", "TODO", end="?", compact=True)
        self.mods_input = InputWidget("Mods", "TODO", "normal", compact=True)
        self.mods_input.setFixedWidth(200)
        self.replay_mode.box.clicked.connect(self.toggle_mod_input)

        self.layout.addWidget(self.user_id_input, 0, 0, 1, 1)
        self.layout.addWidget(self.replay_mode, 0, 1, 1, 1)
        self.layout.addWidget(self.mods_input, 0, 2, 1, 1)

        self.setLayout(self.layout)

    def toggle_mod_input(self, state):
        self.mods_input.setDisabled(state)


class LocalWidget(LinkableSetting, EntryWidget):

    def __init__(self, map_id):
        LinkableSetting.__init__(self, "api_key")
        EntryWidget.__init__(self)
        self.map_id = map_id
        # buttons
        self.file_chooser_button = QPushButton("Select File")
        self.file_chooser_button.setFixedWidth(100)
        self.file_chooser_button.clicked.connect(self._get_file)
        self.folder_button = QPushButton("Select Folder")
        self.folder_button.setFixedWidth(100)
        self.folder_button.clicked.connect(self._get_folder)
        # label
        self.label = QLabel("No replay selected")
        # stuff
        cache_path = resource_path(get_setting("cache_dir") + "circleguard.db")
        self.cg = Circleguard(get_setting("api_key"), cache_path)
        self.replays = []

        self.layout.addWidget(self.file_chooser_button, 0, 0, 1, 1)
        self.layout.addWidget(self.folder_button, 0, 1, 1, 1)
        self.layout.addWidget(self.label, 0, 2, 1, 1)

        self.setLayout(self.layout)

    def set_map_id(self, map_id):
        self.map_id = map_id
        print("updated id")
        self._check_replays()

    def on_setting_changed(self, new_value):
        cache_path = resource_path(get_setting("cache_dir") + "circleguard.db")
        self.cg = Circleguard(new_value, cache_path)

    def _get_file(self):
        self._start(QFileDialog.getOpenFileName(caption="Select File", filter="osu! Replayfile (*.osr)")[0])

    def _get_folder(self):
        options = QFileDialog.Option()
        options |= QFileDialog.ShowDirsOnly
        options |= QFileDialog.HideNameFilterDetails
        self._start(QFileDialog.getExistingDirectory(caption="Select Folder", options=options))

    def _start(self, path):
        self.replays = []
        if path == "":
            return
        self.hide_error()
        self.label.setText("Loading...")
        Thread(target=self._parse, args=[path]).start()

    def _load_replay(self, path):
        replay = ReplayPath(path)
        self.cg.load(replay)
        self.replays.append(replay)

    def _parse(self, path):
        if path.endswith(".osr"):
            self._load_replay(path)
        else:
            for i, f in enumerate(os.listdir(path)):
                self.label.setText("Loading" + ("." * int(i % 4)))
                if f.endswith(".osr"):
                    self._load_replay(os.path.join(path, f))
        if len(self.replays) == 1:
            self.label.setText(f"{self.replays[0].username}'s replays")
        else:
            self.label.setText(f"{len(self.replays)} replays")
        self._check_replays()

    def _check_replays(self):
        if len(self.replays) == 0:
            return self.hide_error()
        if self.map_id == -1:
            return self.show_error(["No Map id set"])
        invalid_replays = [r for r in self.replays if r.map_id != self.map_id]
        errors = []
        if len(invalid_replays) == 0:
            return self.hide_error()
        elif len(invalid_replays) == 1:
            errors.append(f"incorrect Map id ({invalid_replays[0].map_id})")
        else:
            for r in invalid_replays:
                errors.append(f"{Path(r.path).name} has incorrect Map id ({r.map_id})")
        self.show_error(errors)


class ReplayAreaWidget(QWidget):
    map_id_signal = pyqtSignal(int)

    def __init__(self, local_only=False):
        super().__init__()
        # add buttons
        self.map_id = -1
        self.local_button = QPushButton("Add Local")
        self.local_button.clicked.connect(self._add_local)
        if not local_only:
            self.user_button = QPushButton("Add User")
            self.user_button.clicked.connect(self._add_user)
            self.buttons = WidgetCombiner(self.user_button, self.local_button)
        # replay area
        self.replay_area = QScrollArea(self)
        self.replay_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.replay_area.setWidget(ScrollableWidget())
        self.replay_area.setWidgetResizable(True)
        # arrays
        self.extra_replays = []
        self.layout = QGridLayout()
        self.layout.setVerticalSpacing(10)
        if not local_only:
            self.layout.addWidget(self.buttons, 0, 0, 1, 1)
        else:
            self.layout.addWidget(self.local_button, 0, 0, 1, 1)
        self.layout.addWidget(self.replay_area, 1, 0, 1, 1)
        self.layout.setAlignment(Qt.AlignTop)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.setLayout(self.layout)

    def _add_user(self):
        w = UserWidget()
        w.delete_button.clicked.connect(partial(self._remove_replay, w))
        self.replay_area.widget().layout.addWidget(w)
        self.extra_replays.append(w)

    def _add_local(self):
        w = LocalWidget(self.map_id)
        w.delete_button.clicked.connect(partial(self._remove_replay, w))
        self.replay_area.widget().layout.addWidget(w)
        self.extra_replays.append(w)
        self.map_id_signal.connect(w.set_map_id)

    def _remove_replay(self, w):
        self.replay_area.widget().layout.removeWidget(w)
        delete_widget(w)
        self.extra_replays.remove(w)

    def update_map_id(self, map_id):
        self.map_id = map_id
        self.map_id_signal.emit(map_id)


class Score(QFrame):
    def __init__(self, score_dict):
        super().__init__()
        self.layout = QGridLayout()
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setAlignment(Qt.AlignTop)
        self.setLayout(self.layout)
        if "error" in score_dict:
            self.error_label = QLabel(score_dict["error"])
            self.layout.addWidget(self.error_label, 0, 0, 1, 1)
            return
        self.score_dict = score_dict
        self.score_id = score_dict["score_id"]
        self.info_label = QLabel(f'{score_dict["beatmap_id"]} [????] ?.??*\n'                                 
                                 f'{score_dict["maxcombo"]}/????x | {score_dict["rank"]} ({score_to_acc(score_dict)}%)')
        self.date_label = QLabel(f'Set on {score_dict["date"]}')
        self.layout.addWidget(self.info_label, 0, 0, 1, 2)
        self.layout.addWidget(self.date_label, 1, 1, 1, 1, alignment=Qt.AlignRight)
        Thread(target=self._load).start()

    def _load(self):
        score_dict = self.score_dict
        beatmap = cached_beatmap_lookup(score_dict["beatmap_id"])
        self.info_label.setText(
            f'{beatmap["filename"][:-4]} ({round(float(beatmap["difficultyrating"]),2)}* NM)\n'                                 
            f'{score_dict["rank"]} | {score_to_acc(score_dict)}% | '
            f'{Mod(int(score_dict["enabled_mods"])).short_name()} | '
            f'{score_dict["maxcombo"]}x | {round(float(score_dict["pp"]),2)}pp ')


class UserScoresAreaWidget(QWidget):
    map_id_signal = pyqtSignal(int)
    _scores_signal = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.user_id = -1
        self.score_widgets = []
        # replay area
        self.plays_area = QScrollArea(self)
        self.plays_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.plays_area.setWidget(ScrollableWidget())
        self.plays_area.setWidgetResizable(True)

        self.layout = QGridLayout()
        self.layout.addWidget(self.plays_area, 1, 0, 1, 1)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.setLayout(self.layout)
        self._scores_signal.connect(self._reload_entries)

    def _do_web_request(self):
        self._clear_scores()
        api = ossapi(get_setting("api_key"))
        scores = retrying_request(api.get_user_best, {"u": self.user_id, "limit": 100})
        print(scores)
        scores = [s for s in scores if bool(int(s["replay_available"]))]
        self._scores_signal.emit(scores)

    def update_user_id(self, user_id):
        self.user_id = user_id
        Thread(target=self._do_web_request).start()

    def _clear_scores(self):
        for w in self.score_widgets:
            self.plays_area.widget().layout.removeWidget(w)
            delete_widget(w)
        self.score_widgets = []

    def _reload_entries(self, scores):
        self._clear_scores()
        if len(scores) == 0:
            w = QLabel("No replays available to download")
            self.score_widgets.append(w)
            self.plays_area.widget().layout.addWidget(w)
            return
        for score in scores:
            w = Score(score)
            self.score_widgets.append(w)
            self.plays_area.widget().layout.addWidget(w)
            pprint(score)
