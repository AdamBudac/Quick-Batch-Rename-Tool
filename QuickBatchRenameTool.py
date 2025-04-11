import sys
from pathlib import Path
from typing import List, Tuple, Optional, Union
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QPushButton, QLabel, QLineEdit,
                              QCheckBox, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, 
                              QFileDialog, QScrollArea, QTableView, QHeaderView, QStyledItemDelegate,
                              QStyle, QItemDelegate, QAbstractItemView, QStyleOptionViewItem)
from PySide6.QtCore import Qt, QAbstractTableModel, QAbstractItemModel, QModelIndex, Signal, QSortFilterProxyModel, QThread, QObject, QTimer
from PySide6.QtGui import QColor, QPainter, QDragEnterEvent, QDragMoveEvent, QDropEvent, QCloseEvent
import copy
from dataclasses import dataclass


# Colors
COLOR_STATUS_ERROR = "red"
COLOR_STATUS_WARNING = "orange" 
COLOR_STATUS_SUCCESS = "green"
COLOR_STATUS_INFO = "#0078D7"  # Light blue
COLOR_BG_SELECTED = "#ECECEC"  # Light gray
COLOR_BG_DUPLICATE = "#FFC7CE" # Light red

# UI dimensions and style
GROUP_WIDTH = 100
GROUP_HEIGHT = 190
WIDGET_BUTTON_WIDTH = 25
TEXT_PADDING = 4

# Time intervals
ANIMATION_INTERVAL = 300 # ms
BATCH_UPDATE_SIZE = 50   # files


# File dataclass
@dataclass
class FileData:
    path: Path
    original_fullname: str
    original_filename: str
    original_extension: str
    current_filename: str
    current_extension: str
    new_filename: str
    new_extension: str
    is_duplicate: bool = False
    
    @property
    def new_fullname(self) -> str:
        if self.new_extension:
            return f"{self.new_filename}.{self.new_extension}"
        return self.new_filename
    
    @classmethod
    def from_path(cls, file_path: Path) -> 'FileData':
        return cls(
            path=file_path,
            original_fullname=file_path.name,
            original_filename=file_path.stem,
            original_extension=file_path.suffix[1:] if file_path.suffix else '',
            current_filename=file_path.stem,
            current_extension=file_path.suffix[1:] if file_path.suffix else '',
            new_filename=file_path.stem,
            new_extension=file_path.suffix[1:] if file_path.suffix else '',
            is_duplicate=False
        )


# Delegate for editing filename and extension
class LineEditDelegate(QItemDelegate):
    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

        self.style_sheet = """
        QLineEdit:focus {
            background-color: white;
            border: 1px solid """ + COLOR_STATUS_INFO + """;
        }
        """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if option.state & QStyle.State_Selected:
            painter.save()
            painter.fillRect(option.rect, QColor(COLOR_BG_SELECTED))
            painter.restore()
            option = QStyleOptionViewItem(option)
            option.state &= ~QStyle.State_Selected
        super().paint(painter, option, index)

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex) -> QLineEdit:
        editor = QLineEdit(parent)
        editor.setStyleSheet(self.style_sheet)
        return editor

    def setEditorData(self, editor: QLineEdit, index: QModelIndex) -> None:
        value = index.model().data(index, Qt.EditRole)
        editor.setText(value)
        QTimer.singleShot(0, lambda: self.clearSelection(editor))

    def clearSelection(self, editor: QLineEdit) -> None:
        editor.deselect()
        editor.setCursorPosition(len(editor.text()))

    def setModelData(self, editor: QLineEdit, model: QAbstractItemModel, index: QModelIndex) -> None:
        model.setData(index, editor.text(), Qt.EditRole)

    def updateEditorGeometry(self, editor: QWidget, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        editor.setGeometry(option.rect)


# Delegate for displaying end of text in New column cell
class EndAlignedItemDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if option.state & QStyle.State_Selected:
            painter.save()
            painter.fillRect(option.rect, QColor(COLOR_BG_SELECTED))
            painter.restore()
            option = QStyleOptionViewItem(option)
            option.state &= ~QStyle.State_Selected

        text = index.data()
        if not text or painter.fontMetrics().horizontalAdvance(text) < option.rect.width() - 10:
            return super().paint(painter, option, index)

        # Longer text elided
        painter.save()
        painter.setPen(option.palette.text().color())
        text_rect = option.rect.adjusted(TEXT_PADDING, 0, -TEXT_PADDING, 0)
        elided_text = painter.fontMetrics().elidedText(text, Qt.ElideLeft, text_rect.width())
        painter.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, elided_text)
        painter.restore()


# Worker class for renaming files in a separate thread
class RenameWorker(QObject):
    # Signals for communication with the main thread
    progress = Signal(int, int)  # (current_count, total_count)
    finished = Signal()
    error = Signal(str, str)     # (error_type, error_message)

    def __init__(self, file_datastructure: List[FileData]):
        super().__init__()
        self.file_datastructure = file_datastructure

    def run(self) -> None:
        try:
            total_files = len(self.file_datastructure)
            
            # First pass: renaming to temporary names
            for i, data in enumerate(self.file_datastructure):
                temp_name = f"QBRT_temp_{data.new_fullname}"
                data.path.rename(data.path.parent / temp_name)
                data.path = data.path.parent / temp_name
                
                # UI update progress every 50 files
                if i % BATCH_UPDATE_SIZE == 0 or i == total_files - 1:
                    self.progress.emit(i + 1, total_files)

            # Second pass: renaming to final names
            for i, data in enumerate(self.file_datastructure):
                if data.new_extension:
                    new_fullname = f"{data.new_filename}.{data.new_extension}"
                else:
                    new_fullname = data.new_filename

                data.path.rename(data.path.parent / new_fullname)
                data.path = data.path.parent / new_fullname
                data.current_filename = data.new_filename
                data.current_extension = data.new_extension

                # UI update progress every 50 files
                if i % BATCH_UPDATE_SIZE == 0 or i == total_files - 1:
                    self.progress.emit(total_files + i + 1, total_files)

            self.finished.emit()

        except PermissionError as e:
            self.error.emit("permission", f"Permission denied: {str(e)}")
        except FileExistsError as e:
            self.error.emit("exists", f"File already exists: {str(e)}")
        except Exception as e:
            self.error.emit("other", f"Error: {str(e)}")


# Model
class FileTableModel(QAbstractTableModel):
    updatePreviewsEditor = Signal(QModelIndex, QModelIndex)
    duplicatesFound = Signal(bool, int, int)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.file_data = []
        self.headers = ["Original", "Filename", "Extension", "New"]
        self._duplicate_cache = {}


    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.file_data)


    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 4  # Original, Filename, Extension, New


    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Optional[Union[str, QColor]]:
        if not index.isValid() or not (0 <= index.row() < len(self.file_data)):
            return None

        match role:
            case Qt.DisplayRole:
                return self._get_display_data(index.column(), self.file_data[index.row()])
            case Qt.ToolTipRole:
                return self._get_tooltip_data(index.column(), self.file_data[index.row()])
            case Qt.BackgroundRole:
                return self._get_background_data(index.column(), self.file_data[index.row()])
            case Qt.EditRole:
                return self._get_edit_data(index.column(), self.file_data[index.row()])
            case _:
                return None


    def _get_display_data(self, col: int, item: FileData) -> Optional[str]:
        match col:
            case 0:  # Original
                return item.original_fullname
            case 1:  # Filename
                return item.new_filename
            case 2:  # Extension
                return item.new_extension
            case 3:  # New preview
                return item.new_fullname
            case _:
                return None


    def _get_tooltip_data(self, col: int, item: FileData) -> Optional[str]:
        match col:
            case 0:  # Original tooltip
                return item.original_fullname
            case 3:  # New preview tooltip
                return item.new_fullname
            case _:
                return None


    def _get_background_data(self, col: int, item: FileData) -> Optional[QColor]:
        if col == 3 and item.is_duplicate:
            return QColor(COLOR_BG_DUPLICATE)
        return None


    def _get_edit_data(self, col: int, item: FileData) -> Optional[str]:
        match col:
            case 1:  # Filename
                return item.new_filename
            case 2:  # Extension
                return item.new_extension
            case _:
                return None


    def setData(self, index: QModelIndex, value: str, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or role != Qt.EditRole:
            return False

        # Only columns 1 (Filename) and 2 (Extension) are editable
        if index.column() not in [1, 2]:
            return False

        # Update appropriate property based on column
        match index.column():
            case 1:  # Filename
                self.file_data[index.row()].new_filename = value
            case 2:  # Extension
                self.file_data[index.row()].new_extension = value

        # Update preview and check for duplicates
        preview_index = self.index(index.row(), 3)
        self.updatePreviewsEditor.emit(preview_index, preview_index)
        self._check_duplicates()
        return True


    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Optional[str]:
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]
        return None


    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags

        col = index.column()
        if col in [1, 2]:  # Filename and Extension are editable
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
        else:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable


    def setFiles(self, file_datastructure: List[FileData]) -> None:
        self.beginResetModel()
        self.file_data = file_datastructure
        self._duplicate_cache = {}  # Reset cache
        self.endResetModel()


    def find_duplicates(self) -> Tuple[bool, int, int]:
        # Call internal method to update duplicate state
        return self._check_duplicates()


    def _check_duplicates(self) -> Tuple[bool, int, int]:
        filename_counts = {}
        duplicate_names = set()

        # First pass - collect counts
        for data in self.file_data:
            full_name = data.new_fullname

            if full_name in filename_counts:
                duplicate_names.add(full_name)
                filename_counts[full_name] += 1
            else:
                filename_counts[full_name] = 1

        # Reset duplicate flags
        duplicate_files = 0
        changed_rows = []

        # Second pass - update duplicate flags
        for i, data in enumerate(self.file_data):
            old_state = data.is_duplicate
            new_state = data.new_fullname in duplicate_names

            if old_state != new_state:
                data.is_duplicate = new_state
                changed_rows.append(i)

            if new_state:
                duplicate_files += 1

        # Update UI only for changed rows
        for row in changed_rows:
            self.updatePreviewsEditor.emit(self.index(row, 3), self.index(row, 3))

        has_duplicates = bool(duplicate_names)
        duplicate_count = len(duplicate_names)
        self.duplicatesFound.emit(has_duplicates, duplicate_count, duplicate_files)

        return has_duplicates, duplicate_count, duplicate_files


class QuickBatchRenameTool(QMainWindow):
    def __init__(self):
        super().__init__()

        # Data structures
        self.original_files: List[Path] = [] # Original file paths (do not change)
        self.current_files: List[Path] = [] # Current file paths (update after renaming)
        self.file_datastructure: List[FileData] = [] # Data structure for efficient file work

        # UI components
        self.filename_entries: List[QLineEdit] = []
        self.extension_entries: List[QLineEdit] = []
        self.original_labels: List[QLabel] = []
        self.new_labels: List[QLabel] = []

        # Sorting
        self.sort_order = {"column": None, "ascending": True}

        # Status settings
        self.status = {
            "error": {"active": False, "message": "", "color": COLOR_STATUS_ERROR, "priority": 4},
            "warning": {"active": False, "message": "", "color": COLOR_STATUS_WARNING, "priority": 3},
            "success": {"active": False, "message": "", "color": COLOR_STATUS_SUCCESS, "priority": 2},
            "info": {"active": False, "message": "", "color": COLOR_STATUS_INFO, "priority": 1}
        }

        # Models
        self.file_model = FileTableModel(self)
        self.proxy_model = QSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.file_model)

        # UI setup
        self.setWindowTitle("Quick Batch Rename Tool")
        self.resize(1280, 720)
        #self.showMaximized()
        self.init_ui()
        self.setAcceptDrops(True)

        # Initialize variables for threading
        self.rename_thread = None
        self.rename_worker = None
        self.renaming_in_progress = False
        self.animation_dots = ""
        self.animation_dots_timer = None


    # Initialize UI
    def init_ui(self):
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setSpacing(2)
        self.create_layout()


    # Create layout
    def create_layout(self):
        self.create_top_layout()
        self.create_bottom_layout()


    def create_top_layout(self):
        self.top_layout = QHBoxLayout()
        self.top_layout.setSpacing(10)

        group_width = GROUP_WIDTH
        group_height = GROUP_HEIGHT
        actions_group = self.create_actions_group()
        actions_group.setMinimumWidth(group_width)
        actions_group.setMaximumWidth(group_width)
        actions_group.setMinimumHeight(group_height)
        actions_group.setMaximumHeight(group_height)
        info_group = self.create_info_group()
        info_group.setMinimumWidth(group_width*2)
        info_group.setMaximumWidth(group_width*2)
        info_group.setMinimumHeight(group_height)
        info_group.setMaximumHeight(group_height)
        settings_group = self.create_settings_group()
        settings_group.setMinimumHeight(group_height)
        settings_group.setMaximumHeight(group_height)

        self.top_layout.addWidget(actions_group)
        self.top_layout.addWidget(info_group)
        self.top_layout.addWidget(settings_group, 1)
        self.main_layout.addLayout(self.top_layout)


    def create_bottom_layout(self):
        self.bottom_layout = QVBoxLayout()
        self.create_editor_group()
        self.main_layout.addLayout(self.bottom_layout)


    # Create groups
    def create_actions_group(self):
        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setAlignment(Qt.AlignTop)

        self.load_button = QPushButton("Load", clicked=self.load_action)
        self.rename_button = QPushButton("Rename", clicked=self.rename_action)
        self.quit_button = QPushButton("Quit", clicked=QApplication.quit)

        self.rename_button.setEnabled(False)

        actions_layout.addWidget(self.load_button)
        actions_layout.addWidget(self.rename_button)
        actions_layout.addWidget(self.quit_button)

        return actions_group


    def create_info_group(self):
        info_group = QGroupBox("Info")
        info_layout = QVBoxLayout(info_group)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QScrollArea.NoFrame)

        self.status_label = QLabel("Program started")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        scroll_area.setWidget(self.status_label)
        info_layout.addWidget(scroll_area)

        return info_group


    def create_settings_group(self):
        settings_group = QGroupBox("Settings")
        settings_layout = QGridLayout(settings_group)

        # Settings column widths (-:3:2)
        settings_layout.setColumnStretch(1, 3) # Filename
        settings_layout.setColumnStretch(2, 2) # Extension

        # Headers
        settings_layout.addWidget(QLabel("Filename"), 0, 1)
        settings_layout.addWidget(QLabel("Extension"), 0, 2)
        settings_layout.addWidget(QLabel("Mask:"), 1, 0)
        settings_layout.addWidget(QLabel("Counter:"), 2, 0)
        settings_layout.addWidget(QLabel("Increment:"), 3, 0)
        settings_layout.addWidget(QLabel("Zerofill:"), 4, 0)
        settings_layout.addWidget(QLabel("Original:"), 5, 0)

        # Mask inputs
        self.filename_mask = QLineEdit()
        self.extension_mask = QLineEdit()
        settings_layout.addWidget(self.filename_mask, 1, 1)
        settings_layout.addWidget(self.extension_mask, 1, 2)

        # Counter controls
        self.counter_filename_checkbox = QCheckBox()
        self.counter_filename_checkbox.setChecked(True)
        container_filename, self.counter_filename = self.create_number_control(initial_value="1")
        counter_filename_layout = QHBoxLayout()
        counter_filename_layout.setContentsMargins(0, 0, 0, 0)
        counter_filename_layout.setSpacing(0)
        counter_filename_layout.setAlignment(Qt.AlignLeft)
        counter_filename_layout.addWidget(container_filename)
        counter_filename_layout.addWidget(self.counter_filename_checkbox)
        counter_filename_layout.addStretch(1)      
        counter_filename_container = QWidget()
        counter_filename_container.setLayout(counter_filename_layout)
        settings_layout.addWidget(counter_filename_container, 2, 1)

        self.counter_extension_checkbox = QCheckBox()
        self.counter_extension_checkbox.setChecked(False)
        container_extension, self.counter_extension = self.create_number_control(initial_value="1")
        counter_extension_layout = QHBoxLayout()
        counter_extension_layout.setContentsMargins(0, 0, 0, 0)
        counter_extension_layout.setSpacing(0)
        counter_extension_layout.setAlignment(Qt.AlignLeft)
        counter_extension_layout.addWidget(container_extension)
        counter_extension_layout.addWidget(self.counter_extension_checkbox)
        counter_extension_layout.addStretch(1)
        counter_extension_container = QWidget()
        counter_extension_container.setLayout(counter_extension_layout)
        settings_layout.addWidget(counter_extension_container, 2, 2)

        # Increment controls
        container_filename, self.increment_filename = self.create_number_control(initial_value="1")
        container_extension, self.increment_extension = self.create_number_control(initial_value="1")
        settings_layout.addWidget(container_filename, 3, 1)
        settings_layout.addWidget(container_extension, 3, 2)

        # Zerofill controls
        container_filename, self.zerofill_filename = self.create_number_control(initial_value="1")
        container_extension, self.zerofill_extension = self.create_number_control(initial_value="1")
        settings_layout.addWidget(container_filename, 4, 1)
        settings_layout.addWidget(container_extension, 4, 2)

        # Original controls
        self.original_filename_checkbox = QCheckBox()
        self.original_extension_checkbox = QCheckBox()
        self.original_filename_checkbox.setChecked(False)
        self.original_extension_checkbox.setChecked(True)
        settings_layout.addWidget(self.original_filename_checkbox, 5, 1)
        settings_layout.addWidget(self.original_extension_checkbox, 5, 2)

        # Connect signals for automatic preview update
        self.connect_update_signals()

        return settings_group


    def create_number_control(self, initial_value: str) -> Tuple[QWidget, QLineEdit]:
        def update_value(delta: int):
            try:
                current = int(entry.text())
                new_value = max(0, current + delta)
                entry.setText(str(new_value))
                self.status["info"]["active"] = True
                self.update_previews_settings()
            except ValueError:
                self.status["warning"]["active"] = True
                self.status["warning"]["message"] = "Invalid number input"
                self.update_status()

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        widget_width = WIDGET_BUTTON_WIDTH
        minus_btn = QPushButton("-")
        minus_btn.setFixedWidth(widget_width)
        entry = QLineEdit(initial_value)
        entry.setFixedWidth(widget_width*3)
        entry.setAlignment(Qt.AlignCenter)
        plus_btn = QPushButton("+")
        plus_btn.setFixedWidth(widget_width)

        minus_btn.clicked.connect(lambda: update_value(-1))
        plus_btn.clicked.connect(lambda: update_value(1))
        entry.textChanged.connect(self.update_previews_settings)

        layout.addWidget(minus_btn)
        layout.addWidget(entry)
        layout.addWidget(plus_btn)
        layout.addStretch(1)

        return container, entry


    def create_editor_group(self):
        editor_group = QGroupBox("Editor")
        editor_layout = QVBoxLayout(editor_group)

        self.file_table = QTableView()
        # Performance optimization settings
        self.file_table.setVerticalScrollMode(QTableView.ScrollPerPixel)  # Smooth scrolling
        self.file_table.horizontalHeader().setStretchLastSection(True)  # Reduce recalculations of size
        self.file_table.setShowGrid(True)  # Enable grid for consistent appearance
        # Edit trigger settings - one click
        self.file_table.setEditTriggers(QAbstractItemView.DoubleClicked | 
                                       QAbstractItemView.EditKeyPressed |
                                       QAbstractItemView.AnyKeyPressed |
                                       QAbstractItemView.CurrentChanged)
        # Set model and delegates
        self.file_table.setModel(self.proxy_model)
        self._setup_column_delegates()
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.file_table.horizontalHeader().sectionClicked.connect(self.handle_header_click)
        self.file_table.setSelectionBehavior(QTableView.SelectRows)
        self.file_table.setSelectionMode(QTableView.SingleSelection)

        # Connect signals
        self.file_model.updatePreviewsEditor.connect(self.update_previews_editor)
        self.file_model.duplicatesFound.connect(self.on_duplicates_found)

        editor_layout.addWidget(self.file_table)
        self.bottom_layout.addWidget(editor_group)


    # Connect signals
    def connect_update_signals(self):
        self.filename_mask.textChanged.connect(self.update_previews_settings)
        self.extension_mask.textChanged.connect(self.update_previews_settings)
        self.counter_filename_checkbox.stateChanged.connect(self.update_previews_settings)
        self.counter_extension_checkbox.stateChanged.connect(self.update_previews_settings)
        self.original_filename_checkbox.stateChanged.connect(self.update_previews_settings)
        self.original_extension_checkbox.stateChanged.connect(self.update_previews_settings)


    # Define actions
    def load_action(self):
        loaded_files = QFileDialog.getOpenFileNames(self, caption="Select files")[0]
        if not loaded_files:
            return
        self.process_loaded_files(loaded_files)


    def rename_action(self):
        if self.renaming_in_progress:
            return

        if not self.file_datastructure or len(self.file_datastructure) == 0:
            self.status["error"]["active"] = True
            self.status["error"]["message"] = "No files to rename"
            self.update_status()
            return

        has_duplicates, duplicate_names, duplicate_files = self.file_model.find_duplicates()
        if has_duplicates:
            self.status["error"]["active"] = True
            self.status["error"]["message"] = f"Found {duplicate_names} duplicate name(s) affecting {duplicate_files} files!"
            self.update_status()
            return

        # Copy data structure for worker
        data_copy = copy.deepcopy(self.file_datastructure)

        # Create worker and thread
        self.rename_thread = QThread()
        self.rename_worker = RenameWorker(data_copy)
        self.rename_worker.moveToThread(self.rename_thread)

        # Connect signals
        self.rename_thread.started.connect(self.rename_worker.run)
        self.rename_worker.progress.connect(self.update_rename_progress)
        self.rename_worker.error.connect(self.handle_rename_error)
        self.rename_worker.finished.connect(self.finish_renaming)

        self.renaming_in_progress = True
        self.rename_button.setEnabled(False)
        self.disable_editing_controls(True)

        # Start dots animation
        self.animation_dots = ""
        self.animation_dots_timer = QTimer(self)
        self.animation_dots_timer.timeout.connect(self.update_dot_animation)
        self.animation_dots_timer.start(ANIMATION_INTERVAL)

        self.status["info"]["active"] = True
        self.status["info"]["message"] = "Renaming in progress: 0/" + str(len(self.file_datastructure)) + " files"
        self.update_status()

        self.rename_thread.start()


    # Define event handlers
    def finish_renaming(self):
        if not self.renaming_in_progress:
            return

        # Keep reference to data
        self.file_datastructure = self.rename_worker.file_datastructure

        self._cleanup_rename_resources()

        # Update model
        self.file_model.setFiles(self.file_datastructure)

        self.status["success"]["active"] = True
        self.status["success"]["message"] = f"Successfully renamed {len(self.file_datastructure)} files!"
        self.update_status()


    def handle_rename_error(self, error_type: str, message: str):
        self._cleanup_rename_resources()
        self.status["error"]["active"] = True
        self.status["error"]["message"] = message
        self.update_status()


    def _cleanup_rename_resources(self):
        if self.rename_thread:
            self.rename_thread.quit()
            self.rename_thread.wait()

        if self.animation_dots_timer:
            self.animation_dots_timer.stop()

        self.rename_thread = None
        self.rename_worker = None

        # Reset UI
        self.renaming_in_progress = False
        self.rename_button.setEnabled(True)
        self.disable_editing_controls(False)


    def closeEvent(self, event: QCloseEvent):
        self._cleanup_rename_resources()
        super().closeEvent(event)


    def update_status(self):
        # Find the highest priority active status
        active_status = None
        for status_name, status in self.status.items():
            if status["active"]:
                if active_status is None or status["priority"] > active_status["priority"]:
                    active_status = status

        # Update the UI based on the active status
        if active_status:
            self.status_label.setText(active_status["message"])
            self.status_label.setStyleSheet(f"color: {active_status['color']}")
            # Reset active flag for this status
            active_status["active"] = False
        else:
            # If no active status, check current text
            current_text = self.status_label.text()
            if "sorted" in current_text:
                # If message contains "sorted", keep it
                pass
            else:
                # If no active status, set "Ready"
                self.status_label.setText("Ready")
                self.status_label.setStyleSheet("color: black")


    def _create_label(self, text: str) -> QLabel:
        label = QLabel(text)
        return label


    def _create_checkbox(self, checked: bool = False) -> QCheckBox:
        checkbox = QCheckBox()
        checkbox.setChecked(checked)
        return checkbox


    def _update_previews_core(self, update_status_message: bool = True) -> Tuple[bool, int, int]:
        try:
            counter_filename = int(self.counter_filename.text()) if self.counter_filename_checkbox.isChecked() else None
            counter_extension = int(self.counter_extension.text()) if self.counter_extension_checkbox.isChecked() else None
            increment_filename = int(self.increment_filename.text()) if counter_filename is not None else None
            increment_extension = int(self.increment_extension.text()) if counter_extension is not None else None
            zerofill_filename = int(self.zerofill_filename.text()) if counter_filename is not None else None
            zerofill_extension = int(self.zerofill_extension.text()) if counter_extension is not None else None
            keep_original_filename = self.original_filename_checkbox.isChecked()
            keep_original_extension = self.original_extension_checkbox.isChecked()
            mask_filename = self.filename_mask.text()
            mask_extension = self.extension_mask.text()

            # Initialize counters - always start from first value
            filename_counter = counter_filename
            extension_counter = counter_extension

            for i in range(len(self.file_datastructure)):
                data = self.file_datastructure[i]

                if keep_original_filename:
                    new_filename = data.original_filename
                else:
                    new_filename = mask_filename

                if keep_original_extension:
                    new_extension = data.original_extension
                else:
                    new_extension = mask_extension

                data.new_filename = new_filename
                data.new_extension = new_extension
            
            # Apply counter
            for row in range(self.proxy_model.rowCount()):
                source_row = self.proxy_model.mapToSource(self.proxy_model.index(row, 0)).row()
                data = self.file_datastructure[source_row]

                if counter_filename is not None and not keep_original_filename:
                    counter_str = str(filename_counter).zfill(zerofill_filename)
                    data.new_filename = data.new_filename + counter_str
                    filename_counter += increment_filename

                if counter_extension is not None and not keep_original_extension:
                    counter_str = str(extension_counter).zfill(zerofill_extension)
                    data.new_extension = data.new_extension + counter_str
                    extension_counter += increment_extension

            # Update model
            self.file_model.setFiles(self.file_datastructure)

            # Check for duplicates
            has_duplicates, duplicate_names, duplicate_files = self.file_model.find_duplicates()

            if has_duplicates:
                self.rename_button.setEnabled(False)
                if update_status_message:
                    self.status["error"]["active"] = True
                    self.status["error"]["message"] = f"Found {duplicate_names} duplicate name(s) affecting {duplicate_files} files!"
                    self.update_status()
            else:
                self.rename_button.setEnabled(True)
                if update_status_message:
                    # Set message about preview updates
                    self.status["info"]["active"] = True
                    self.status["info"]["message"] = "Previews updated"
                    self.update_status()

            return has_duplicates, duplicate_names, duplicate_files

        except ValueError:
            if update_status_message:
                self.status["warning"]["active"] = True
                self.status["warning"]["message"] = "Invalid number input"
                self.update_status()
            else:
                self.status["error"]["active"] = True
                self.status["error"]["message"] = "Invalid number input"
                self.update_status()

            return False, 0, 0


    def update_previews_settings(self):
        self._update_previews_core(update_status_message=True)


    def _update_previews_without_status_message(self):
        self._update_previews_core(update_status_message=False)


    def handle_header_click(self, column: int):
        if self.sort_order["column"] == column:
            self.sort_order["ascending"] = not self.sort_order["ascending"]
        else:
            self.sort_order["column"] = column
            self.sort_order["ascending"] = True

        order = Qt.AscendingOrder if self.sort_order["ascending"] else Qt.DescendingOrder
        self.proxy_model.sort(column, order)
        self._update_previews_without_status_message()
        sort_direction = "ascending" if self.sort_order["ascending"] else "descending"
        column_name = self.file_model.headerData(column, Qt.Horizontal)

        self.status_label.setText(f"Files sorted {sort_direction} by {column_name}")
        self.status_label.setStyleSheet(f"color: {self.status['info']['color']}")


    def update_previews_editor(self, topLeft: QModelIndex, bottomRight: QModelIndex):
        if topLeft.column() in [1, 2]:
            has_duplicates, duplicate_names, duplicate_files = self.file_model.find_duplicates()

            if has_duplicates:
                self.status["error"]["active"] = True
                self.status["error"]["message"] = f"Found {duplicate_names} duplicate name(s) affecting {duplicate_files} files!"
                self.rename_button.setEnabled(False)
                self.file_model.layoutChanged.emit()
            else:
                self.status["info"]["active"] = True
                self.status["info"]["message"] = "Previews updated"
                self.rename_button.setEnabled(True)

            self.update_status()


    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()


    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()


    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            file_paths = [url.toLocalFile() for url in urls]

            if self._validate_and_process_files(file_paths):
                event.accept()
            else:
                event.ignore()
        else:
            event.ignore()


    def calculate_optimal_zerofill(self, num_files: int) -> int:
        if num_files <= 0:
            return 1
        return len(str(num_files))


    def process_loaded_files(self, loaded_files: List[str]):
        if not loaded_files:
            return

        self.file_datastructure.clear()
        self.original_files = [Path(f) for f in loaded_files]
        self.current_files = self.original_files.copy()

        for file in self.original_files:
            self.file_datastructure.append(FileData.from_path(file))

        self.file_model.setFiles(self.file_datastructure)
        self.rename_button.setEnabled(True)

        try:
            self.zerofill_filename.textChanged.disconnect()
            self.zerofill_extension.textChanged.disconnect()
        except:
            pass

        optimal_zerofill = self.calculate_optimal_zerofill(len(loaded_files))
        self.zerofill_filename.setText(str(optimal_zerofill))
        self.zerofill_extension.setText(str(optimal_zerofill))
        self.zerofill_filename.textChanged.connect(self.update_previews_settings)
        self.zerofill_extension.textChanged.connect(self.update_previews_settings)

        self.update_previews_settings()
        self.status["success"]["active"] = True
        self.status["success"]["message"] = f"Loaded {len(loaded_files)} files"
        self.update_status()


    def disable_editing_controls(self, disabled: bool):
        self.filename_mask.setDisabled(disabled)
        self.extension_mask.setDisabled(disabled)
        self.counter_filename_checkbox.setDisabled(disabled)
        self.counter_extension_checkbox.setDisabled(disabled)
        self.counter_filename.setDisabled(disabled)
        self.counter_extension.setDisabled(disabled)
        self.increment_filename.setDisabled(disabled)
        self.increment_extension.setDisabled(disabled)
        self.zerofill_filename.setDisabled(disabled)
        self.zerofill_extension.setDisabled(disabled)
        self.original_filename_checkbox.setDisabled(disabled)
        self.original_extension_checkbox.setDisabled(disabled)
        self.load_button.setDisabled(disabled)

        for button in self.central_widget.findChildren(QPushButton):
            if button.text() in ["+", "-"] and button != self.rename_button:
                button.setDisabled(disabled)

        editable = not disabled
        if editable:
            self.file_table.setEditTriggers(QAbstractItemView.DoubleClicked | 
                                          QAbstractItemView.EditKeyPressed |
                                          QAbstractItemView.AnyKeyPressed |
                                          QAbstractItemView.CurrentChanged)
        else:
            self.file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)


    def update_rename_progress(self, current_files: int, total_files: int):
        if self.renaming_in_progress:

            if current_files <= total_files:
                # First phase - temporary names
                phase = "1/2"
                files_renamed = current_files
            else:
                # Second phase - final names
                phase = "2/2"
                files_renamed = current_files - total_files

            self.status["info"]["active"] = True
            self.status["info"]["message"] = f"Renaming in progress (phase {phase}): {files_renamed}/{total_files} files{self.animation_dots}"
            self.update_status()


    def update_dot_animation(self):
        if self.renaming_in_progress:
            if len(self.animation_dots) >= 5:
                self.animation_dots = ""
            else:
                self.animation_dots += "."

            current_text = self.status_label.text()
            if current_text.startswith("Renaming in progress:"):
                base_text = current_text.split("...")[0].rstrip(".")
                self.status_label.setText(f"{base_text}{self.animation_dots}")


    def on_duplicates_found(self, has_duplicates: bool, duplicate_names: int, duplicate_files: int):
        if has_duplicates:
            self.status["error"]["active"] = True
            self.status["error"]["message"] = f"Found {duplicate_names} duplicate name(s) affecting {duplicate_files} files!"
            self.rename_button.setEnabled(False)
        else:
            self.status["info"]["active"] = True
            self.status["info"]["message"] = "Previews updated"
            self.rename_button.setEnabled(True)

        self.update_status()


    def _validate_and_process_files(self, file_paths: List[str]) -> bool:
        if not file_paths:
            return False

        # Filter for existing files (not directories)
        valid_files = [file for file in file_paths if Path(file).is_file()]

        if valid_files:
            self.process_loaded_files(valid_files)
            return True
        else:
            self.status["warning"]["active"] = True
            self.status["warning"]["message"] = "No valid files were found"
            self.update_status()
            return False


    def _setup_column_delegates(self):
        self.file_table.setItemDelegateForColumn(0, LineEditDelegate(self.file_table))  # Original
        self.file_table.setItemDelegateForColumn(1, LineEditDelegate(self.file_table))  # Filename
        self.file_table.setItemDelegateForColumn(2, LineEditDelegate(self.file_table))  # Extension
        self.file_table.setItemDelegateForColumn(3, EndAlignedItemDelegate(self.file_table))  # New


# App start
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = QuickBatchRenameTool()
    window.show()
    sys.exit(app.exec())
