import os
import sys
import time
import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger("recorder.ui.window")

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, QByteArray, QDataStream, QEvent, QSize, QRect
from PySide6.QtGui import QFont, QDesktopServices, QIcon, QPixmap, QPainter, QColor, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QProgressBar, QListWidget,
    QListWidgetItem, QGroupBox, QMessageBox, QFrame,
    QSlider, QLineEdit, QTextEdit, QScrollArea, QFileDialog, QCheckBox,
    QSizePolicy, QDialog, QSystemTrayIcon, QMenu, QStackedWidget
)

from recorder.config import (
    SmartRecordState,
    RecordSourceMode,
    RECORDINGS_DIR,
    TRANSCRIPTIONS_DIR,
    SAMPLE_RATE,
    DEFAULT_AUTO_PAUSE_SEC,
    WHISPER_MODELS,
    save_user_settings,
    ASR_MODELS,
    get_default_model_id,
    PARAKEET_MODEL_ID,
    DEFAULT_WHISPER_MODEL,
    get_hardware_acceleration_info,
    get_recommended_profile,
    load_user_settings,
    get_custom_keywords,
    get_vad_speech_threshold,
    get_system_vad_speech_threshold,
    get_record_source_mode,
    get_loopback_device_index,
    get_target_app_filter,
    get_silence_alert_seconds,
    get_session_split_silence_sec,
    is_one_record_per_day,
    is_auto_check_updates_startup,
    is_always_on_top,
    is_minimize_to_tray_on_close,
    get_window_geometry,
    set_window_geometry
)
from recorder.audio.devices import (
    get_working_input_devices,
    get_working_loopback_devices,
    get_active_audio_apps
)
from recorder.core.vad import is_silero_available
from recorder.ui.theme import DARK_THEME_QSS, setup_dark_palette
from recorder.ui.settings_dialog import SettingsDialog
from recorder.ui.workers import (
    SmartAudioWorker,
    TranscriptionWorker,
    FileProcessingWorker
)
from recorder.core.rolling_transcriber import RollingTranscriptionWorker, RollingBlock
from recorder.core.speakers import (
    format_turns,
    parse_txt_to_turns
)
from recorder.core.session import (
    TranscriptionSession,
    get_session_path_for_txt,
    get_session_path_for_audio,
    find_existing_session_for_audio,
    extract_datetime_from_filename,
    get_turn_sync_id
)
from recorder.core.cloud_sync import CloudSyncManager
from recorder.flavor import APP_NAME


class SilenceToastBanner(QWidget):
    """
    Dyskretny, kompaktowy baner powiadomienia (Toast) w prawym dolnym rogu ekranu.
    Zaprojektowany w stylu Windows 11 Fluent: odtwarza dźwięk systemowy, nie kradnie fokusu z aktywnego okna
    i w przypadku braku reakcji przekazuje powiadomienie do Centrum Akcji Windows.
    """
    confirmed = Signal()
    inspect_requested = Signal()
    dismissed = Signal()
    timed_out = Signal()

    def __init__(self, parent=None, silence_sec: float = 600.0, source_mode: str = RecordSourceMode.HYBRID_DUAL, timeout_sec: int = 45):
        super().__init__(None)
        self.silence_sec = silence_sec
        self.source_mode = source_mode
        self.remaining_sec = timeout_sec

        # Odtworzenie natywnego dźwięku systemowego Windows (chime powiadomienia)
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setFixedWidth(380)

        card = QFrame(self)
        card.setObjectName("ToastCard")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # 1. Nagłówek aplikacji (Ikona + Nazwa + Zamknij)
        app_header = QHBoxLayout()
        app_header.setSpacing(6)
        from recorder.ui.windows_integration import get_app_icon_path
        png_icon = get_app_icon_path("png")
        if png_icon and os.path.exists(png_icon):
            lbl_app_logo = QLabel()
            pix = QPixmap(png_icon).scaled(15, 15, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            lbl_app_logo.setPixmap(pix)
            app_header.addWidget(lbl_app_logo)
        lbl_app_name = QLabel(APP_NAME)
        lbl_app_name.setObjectName("ToastAppName")
        lbl_app_name.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        app_header.addWidget(lbl_app_name, stretch=1)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("ToastCloseBtn")
        btn_close.setFixedSize(22, 22)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setToolTip("Zamknij powiadomienie")
        btn_close.clicked.connect(self._on_close_clicked)
        app_header.addWidget(btn_close)
        layout.addLayout(app_header)

        # 2. Treść monitu (Ikona ostrzeżenia + Tytuł i krótki opis)
        content_row = QHBoxLayout()
        content_row.setSpacing(10)

        lbl_icon = QLabel("⚠️")
        lbl_icon.setFont(QFont("Segoe UI Emoji", 18))
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_row.addWidget(lbl_icon)

        mins = int(silence_sec // 60)
        mins_str = f"{mins} min" if mins > 0 else f"{int(silence_sec)} s"

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        lbl_title = QLabel(f"Brak dźwięku od {mins_str}")
        lbl_title.setObjectName("ToastTitle")
        lbl_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        text_layout.addWidget(lbl_title)

        if source_mode == RecordSourceMode.SYSTEM_ONLY:
            desc_text = "Brak zarejestrowanego dźwięku z komputera."
        elif source_mode == RecordSourceMode.MIC_ONLY:
            desc_text = "Brak zarejestrowanej mowy z mikrofonu."
        else:
            desc_text = "Brak mowy w mikrofonie oraz dźwięku z systemu."

        lbl_desc = QLabel(desc_text)
        lbl_desc.setObjectName("ToastDesc")
        lbl_desc.setWordWrap(True)
        lbl_desc.setFont(QFont("Segoe UI", 8))
        text_layout.addWidget(lbl_desc)

        content_row.addLayout(text_layout, stretch=1)
        layout.addLayout(content_row)

        # 3. Pasek akcji (Licznik czasu + Dyskretne przyciski)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.lbl_timer = QLabel(f"Zniknie za {self.remaining_sec}s")
        self.lbl_timer.setObjectName("ToastTimer")
        self.lbl_timer.setFont(QFont("Segoe UI", 8))
        action_row.addWidget(self.lbl_timer, stretch=1)

        self.btn_ok = QPushButton("Wszystko gra")
        self.btn_ok.setObjectName("ToastOkBtn")
        self.btn_ok.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
        self.btn_ok.setFixedHeight(26)
        self.btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ok.clicked.connect(self._on_ok_clicked)
        action_row.addWidget(self.btn_ok)

        self.btn_err = QPushButton("Sprawdź dźwięk")
        self.btn_err.setObjectName("ToastActionBtn")
        self.btn_err.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_err.setFixedHeight(26)
        self.btn_err.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_err.clicked.connect(self._on_err_clicked)
        action_row.addWidget(self.btn_err)

        layout.addLayout(action_row)

        self._reposition()

        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(1000)
        self.auto_timer.timeout.connect(self._on_tick)
        self.auto_timer.start()

    def _reposition(self):
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            self.adjustSize()
            w = self.width()
            h = self.height()
            x = geom.right() - w - 24
            y = geom.bottom() - h - 24
            self.move(x, y)

    def _on_tick(self):
        self.remaining_sec -= 1
        if self.remaining_sec <= 0:
            self.auto_timer.stop()
            self.timed_out.emit()
            self.close()
        else:
            self.lbl_timer.setText(f"Zniknie za {self.remaining_sec}s")

    def _on_ok_clicked(self):
        self.auto_timer.stop()
        self.confirmed.emit()
        self.close()

    def _on_err_clicked(self):
        self.auto_timer.stop()
        self.inspect_requested.emit()
        self.close()

    def _on_close_clicked(self):
        self.auto_timer.stop()
        self.dismissed.emit()
        self.close()


class _ProgressProxy:
    """
    Zastępuje dawny pasek postępu transkrypcji: przechowuje wartość i opis,
    a każdą zmianę przekazuje do nagłówka arkusza (callback(value, text)).
    """

    def __init__(self, callback):
        self._cb = callback
        self._value = 0
        self._text = ""

    def setValue(self, value: int) -> None:
        self._value = int(value)
        self._cb(self._value, self._text)

    def value(self) -> int:
        return self._value

    def setFormat(self, text: str) -> None:
        self._text = text or ""
        self._cb(self._value, self._text)

    def format(self) -> str:
        return self._text

    def setRange(self, *_args) -> None:
        pass

    def setTextVisible(self, *_args) -> None:
        pass


class SmartDictaphoneWindow(QMainWindow):
    """
    Główne okno aplikacji EMANAGER Signal (Ambient AI & Recorder).
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        from recorder.ui.windows_integration import get_app_icon_path
        ico = get_app_icon_path("ico")
        if ico and os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))
        self.resize(920, 780)
        self.setMinimumSize(640, 520)
        self._force_quit = False
        self._last_tray_message_type: Optional[str] = None
        self._last_silence_source_mode: Optional[str] = None

        # Przywrócenie geometrii okna oraz flagi Always on Top
        self._restore_window_geometry()
        if is_always_on_top():
            self.set_always_on_top(True)

        self.recordings_dir = RECORDINGS_DIR
        self.transcriptions_dir = TRANSCRIPTIONS_DIR

        self.last_audio_save_path = None
        self.recorded_seconds = 0
        self._active_recorded_time = 0.0
        self._last_active_tick = None
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._on_timer_tick)

        self.live_transcription_worker = None
        self.live_plain_text_lines = []
        self.transcription_thread = None
        self.file_processing_worker = None

        # Stan mapowania mówców
        self.current_turns = []
        self.current_txt_path = None
        self.last_plain_text = ""
        self.current_meeting_id = None
        self.synced_segment_count = 0
        self._synced_turn_ids = set()
        self.last_processed_block_idx = 0
        self._active_threads = []
        self._finalize_pending = False       # Guard: blokuje Start gdy trwa finalizacja poprzedniej sesji
        self.session_start_time = None       # Realna godzina startu bieżącego nagrania (datetime)
        self._active_silence_dialog = None

        # Moduł Cloud Sync (Supabase / EMANAGER.PRO / Webhook)
        self.cloud_sync = CloudSyncManager()
        self.cloud_sync.signals.sync_started.connect(self._on_sync_started)
        self.cloud_sync.signals.sync_finished.connect(self._on_sync_finished)
        self.cloud_sync.signals.offline_queued.connect(self._on_offline_queued)
        self.cloud_sync.signals.live_session_started.connect(self._on_live_session_started)
        self.cloud_sync.signals.live_block_synced.connect(self._on_live_block_synced)
        self.cloud_sync.signals.live_session_finalized.connect(self._on_live_session_finalized)

        self.worker = SmartAudioWorker(samplerate=SAMPLE_RATE, auto_pause_sec=DEFAULT_AUTO_PAUSE_SEC)
        self.worker.audio_level_signal.connect(self._update_audio_level)
        self.worker.dual_audio_level_signal.connect(self._update_dual_audio_level)
        self.worker.vad_info_signal.connect(self._update_vad_info)
        self.worker.state_changed_signal.connect(self._on_worker_state_changed)
        self.worker.session_split_signal.connect(self._on_session_split_triggered)
        self.worker.silence_alert_signal.connect(self._on_silence_alert)
        self.worker.error_signal.connect(self._handle_audio_error)
        self.worker.set_session_split_silence_sec(get_session_split_silence_sec())

        self._init_ui()
        self._apply_theme()
        self._refresh_audio_devices()
        self._refresh_recordings_list()
        self._refresh_transcriptions_list()

        self._active_silence_toast = None
        self._active_silence_dialog = None
        self._setup_tray_icon()

        # Uruchomienie przetwarzania zaległej kolejki offline
        self.cloud_sync.process_offline_queue_async()

        # Oczekująca aktualizacja do zainstalowania przy wyjściu (Install on exit)
        self._pending_update_zip_path = None
        self._pending_update_version = None

        # Ciche sprawdzenie dostępności aktualizacji w tle przy starcie
        if is_auto_check_updates_startup():
            QTimer.singleShot(3500, self._start_silent_update_check)

    def set_always_on_top(self, enabled: bool) -> None:
        """
        Włącza lub wyłącza flagę WindowStaysOnTopHint z zachowaniem widoczności okna.
        """
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(enabled))
        if was_visible:
            self.show()
        if sys.platform == "win32":
            try:
                from recorder.ui.theme import set_window_titlebar_theme
                from recorder.config import get_theme
                th = get_theme()
                set_window_titlebar_theme(self, "dark" in th.lower())
            except Exception:
                pass

    def _restore_window_geometry(self) -> None:
        """
        Przywraca zapisaną geometrię okna z user_settings.json z bezpiecznym fallbackiem.
        """
        try:
            geom_hex = get_window_geometry()
            if not geom_hex:
                return
            ba = QByteArray.fromHex(geom_hex.encode("ascii"))
            if ba.isEmpty():
                return

            saved_w, saved_h = 0, 0
            try:
                ds = QDataStream(ba)
                magic = ds.readUInt32()
                if magic == 0x1D9D0CB:
                    _major = ds.readUInt16()
                    _minor = ds.readUInt16()
                    # Pomijamy frame rect (4x int32)
                    for _ in range(4):
                        ds.readInt32()
                    l2 = ds.readInt32()
                    t2 = ds.readInt32()
                    r2 = ds.readInt32()
                    b2 = ds.readInt32()
                    saved_w = r2 - l2 + 1
                    saved_h = b2 - t2 + 1
            except Exception:
                saved_w, saved_h = 0, 0

            restored = self.restoreGeometry(ba)
            if restored and saved_w >= 600 and saved_h >= 600:
                self.resize(max(self.width(), saved_w), max(self.height(), saved_h))
        except Exception as e:
            import logging
            logging.getLogger("recorder").warning(f"Błąd przywracania geometrii okna: {e}")

    def _init_ui(self):
        from recorder.ui.widgets import (
            IconButton, RecordDock, RecordButton, CloudToast, HistoryPanel
        )
        main_widget = QWidget()
        main_widget.setObjectName("MainContainerWidget")
        self.setCentralWidget(main_widget)

        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(24, 10, 24, 20)

        # PASEK GÓRNY: nazwa, aktywne źródła, ikony akcji
        top_bar = QHBoxLayout()
        top_bar.setSpacing(4)
        self.lbl_brand = QLabel(APP_NAME)
        self.lbl_brand.setObjectName("BrandLabel")
        top_bar.addWidget(self.lbl_brand)
        top_bar.addSpacing(10)

        self.btn_source_pill = QPushButton("")
        self.btn_source_pill.setObjectName("SourcePill")
        self.btn_source_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_source_pill.setToolTip("Podpisy mówców (np. Twoje imię zamiast „Mikrofon”) i źródła dźwięku")
        self.btn_source_pill.clicked.connect(self._open_speaker_names_popover)
        top_bar.addWidget(self.btn_source_pill)
        top_bar.addStretch(1)

        self.btn_upload = IconButton("upload", "Wgraj plik audio lub wideo do transkrypcji")
        self.btn_upload.clicked.connect(self._on_upload_file_clicked)
        self.btn_history = IconButton("history", "Historia nagrań i transkrypcji")
        self.btn_history.setCheckable(True)
        self.btn_history.clicked.connect(self._toggle_history_panel)
        self.btn_settings = IconButton("settings", "Ustawienia")
        self.btn_settings.clicked.connect(self._open_settings_dialog)
        top_bar.addWidget(self.btn_upload)
        top_bar.addWidget(self.btn_history)
        top_bar.addWidget(self.btn_settings)
        main_layout.addLayout(top_bar)

        # BANER AKTUALIZACJI (Domyślnie ukryty, pojawia się po cichym wykryciu aktualizacji w tle)
        self.banner_update = QFrame()
        self.banner_update.setObjectName("UpdateBanner")
        banner_layout = QHBoxLayout(self.banner_update)
        banner_layout.setContentsMargins(14, 6, 6, 6)
        banner_layout.setSpacing(6)

        self.lbl_update_banner_text = QLabel("Dostępna jest nowa wersja aplikacji")
        self.lbl_update_banner_text.setObjectName("UpdateBannerText")
        banner_layout.addWidget(self.lbl_update_banner_text, stretch=1)

        self.btn_update_banner_action = IconButton("download", "Pokaż aktualizację", size=32, icon_px=17)
        self.btn_update_banner_action.clicked.connect(lambda: self._open_settings_dialog(initial_tab="updates"))
        banner_layout.addWidget(self.btn_update_banner_action)

        btn_close_banner = IconButton("x", "Ukryj powiadomienie", size=32, icon_px=16)
        btn_close_banner.clicked.connect(self.banner_update.hide)
        banner_layout.addWidget(btn_close_banner)
        self._banner_close_btn = btn_close_banner

        self.banner_update.hide()
        main_layout.addWidget(self.banner_update)

        # ARKUSZ TRANSKRYPCJI
        self.sheet = QFrame()
        self.sheet.setObjectName("TranscriptSheet")
        sheet_layout = QVBoxLayout(self.sheet)
        sheet_layout.setContentsMargins(36, 24, 28, 18)
        sheet_layout.setSpacing(14)

        self.doc_header = QWidget()
        doc_header_layout = QHBoxLayout(self.doc_header)
        doc_header_layout.setContentsMargins(0, 0, 0, 0)
        doc_header_layout.setSpacing(2)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self.lbl_doc_title = QLabel("")
        self.lbl_doc_title.setObjectName("DocTitle")
        self.lbl_doc_meta = QLabel("")
        self.lbl_doc_meta.setObjectName("DocMeta")
        title_col.addWidget(self.lbl_doc_title)
        title_col.addWidget(self.lbl_doc_meta)
        doc_header_layout.addLayout(title_col, stretch=1)

        self.btn_copy_transcript = IconButton("copy", "Kopiuj transkrypcję do schowka", size=32, icon_px=17)
        self.btn_copy_transcript.clicked.connect(self._on_copy_transcript_clicked)
        self.btn_save_transcript = IconButton("download", "Zapisz transkrypcję jako plik .txt", size=32, icon_px=17)
        self.btn_save_transcript.clicked.connect(self._on_save_transcript_clicked)
        sync_target_name = self.cloud_sync.config.get("sync_target", "emanager").upper()
        self.btn_manual_sync = IconButton("cloud", f"Wyślij do {sync_target_name}", size=32, icon_px=17)
        self.btn_manual_sync.setEnabled(False)
        self.btn_manual_sync.clicked.connect(self._on_manual_sync_clicked)
        for b in (self.btn_copy_transcript, self.btn_save_transcript, self.btn_manual_sync):
            doc_header_layout.addWidget(b, alignment=Qt.AlignmentFlag.AlignTop)
        sheet_layout.addWidget(self.doc_header)

        self.body_stack = QStackedWidget()

        # Strona 0: stan gotowości
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(0, 0, 0, 24)
        empty_layout.setSpacing(14)
        empty_layout.addStretch(1)
        self._empty_btn_slot = QHBoxLayout()
        self._empty_btn_slot.addStretch(1)
        self._empty_btn_slot.addStretch(1)
        empty_layout.addLayout(self._empty_btn_slot)
        self.lbl_empty_title = QLabel("Gotowy do nagrywania")
        self.lbl_empty_title.setObjectName("EmptyTitle")
        self.lbl_empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty_hint = QLabel("Naciśnij przycisk, aby zacząć. Gotowy plik audio wgrasz ikoną u góry.")
        self.lbl_empty_hint.setObjectName("EmptyHint")
        self.lbl_empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty_hint.setWordWrap(True)
        empty_layout.addWidget(self.lbl_empty_title)
        empty_layout.addWidget(self.lbl_empty_hint)
        empty_layout.addStretch(1)
        self.body_stack.addWidget(self.empty_page)

        # Strona 1: tekst transkrypcji
        self.text_transcript = QTextEdit()
        self.text_transcript.setObjectName("TranscriptView")
        self.text_transcript.setReadOnly(True)
        self.text_transcript.setFrameShape(QFrame.Shape.NoFrame)
        self.text_transcript.setPlaceholderText("Tutaj pojawi się transkrypcja.")
        self.body_stack.addWidget(self.text_transcript)

        # Animacje podglądu na żywo: kropki „pisze…” w miejscu następnej wypowiedzi
        # oraz płynne pojawianie się nowych wierszy
        from recorder.ui.widgets import TypingDots, RowFadeIn
        self.typing_dots = TypingDots(self.text_transcript.viewport())
        self.row_fade = RowFadeIn(self.text_transcript.viewport())
        self._live_slot = False
        self._live_slot_pending = False
        self._last_is_speech = False
        self.text_transcript.document().contentsChanged.connect(self._schedule_live_slot)
        self.text_transcript.verticalScrollBar().valueChanged.connect(self._position_typing_dots)
        self.text_transcript.viewport().installEventFilter(self)
        sheet_layout.addWidget(self.body_stack, stretch=1)

        # Pasek nagrywania pod tekstem (pływający panel lub przycisk startu)
        self._dock_row = QHBoxLayout()
        self._dock_row.setContentsMargins(0, 0, 0, 0)
        self._dock_row.addStretch(1)
        self.dock = RecordDock()
        self.dock.pause_clicked.connect(self._on_pause_clicked)
        self.dock.stop_clicked.connect(self._on_stop_clicked)
        self.dock.mic_toggled.connect(self._toggle_mic_mute)
        self.dock.sys_toggled.connect(self._toggle_sys_mute)
        self._dock_row.addWidget(self.dock)
        self._dock_row.addStretch(1)
        sheet_layout.addLayout(self._dock_row)

        # Kompatybilność: przyciski sterujące z pływającego panelu
        self.btn_pause = self.dock.btn_pause
        self.btn_stop = self.dock.btn_stop
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)

        self.btn_start = RecordButton()
        self.btn_start.clicked.connect(self._on_start_clicked)

        main_layout.addWidget(self.sheet, stretch=1)

        # Postęp transkrypcji wyświetlany w nagłówku arkusza
        self.progress_transcription = _ProgressProxy(self._on_progress_changed)
        self._doc_base_meta = ""

        # Nakładki: historia i powiadomienie o chmurze
        self.history_panel = HistoryPanel(main_widget)
        self.history_panel.transcript_requested.connect(self._open_transcript_file)
        self.history_panel.audio_requested.connect(self._open_audio_file)
        self.history_panel.open_recordings_folder.connect(self._on_open_folder_clicked)
        self.history_panel.open_transcriptions_folder.connect(self._on_open_txt_folder_clicked)
        self.history_panel.closed.connect(lambda: self.btn_history.setChecked(False))
        self._history_dirty = True

        self.cloud_toast = CloudToast(main_widget)
        self.cloud_toast.retry_clicked.connect(self._on_cloud_retry_clicked)

        main_widget.installEventFilter(self)
        self._main_widget = main_widget

        self._show_idle_view()

    # ------------------------------------------------------------------
    # Widok arkusza: stan gotowości, nagrywanie, przetwarzanie
    # ------------------------------------------------------------------
    def is_recording(self) -> bool:
        """Czy trwa sesja nagrywania (także w pauzie)."""
        return getattr(self, "worker", None) is not None and self.worker.state != SmartRecordState.STOPPED

    def _has_transcript(self) -> bool:
        return bool(self.current_turns) or bool((self.last_plain_text or "").strip())

    def _place_start_button(self, in_empty_page: bool) -> None:
        """Przenosi przycisk nagrywania: duży na środek pustego arkusza albo mały pod tekst."""
        self._dock_row.removeWidget(self.btn_start)
        self._empty_btn_slot.removeWidget(self.btn_start)
        if in_empty_page:
            self.btn_start.set_diameter(64)
            self._empty_btn_slot.insertWidget(1, self.btn_start)
        else:
            self.btn_start.set_diameter(44)
            self._dock_row.insertWidget(1, self.btn_start)
        self.btn_start.show()

    def _show_idle_view(self, show_text: Optional[bool] = None) -> None:
        """Stan gotowości: przycisk nagrywania, bez panelu nagrywania."""
        self._set_live_slot(False)
        if show_text is None:
            show_text = self._has_transcript()
        self.dock.hide()
        if show_text:
            self.body_stack.setCurrentIndex(1)
            self.doc_header.show()
        else:
            self.body_stack.setCurrentIndex(0)
            self.doc_header.hide()
        self._place_start_button(in_empty_page=not show_text)

    def _show_recording_view(self) -> None:
        self.btn_start.hide()
        self.body_stack.setCurrentIndex(1)
        self.doc_header.show()
        self.dock.set_mode("recording")
        self.dock.reset_levels()
        self.dock.set_progress(None)
        self.dock.show()
        self._set_live_slot(True)

    def _show_processing_view(self, text: str) -> None:
        self.btn_start.hide()
        self.body_stack.setCurrentIndex(1)
        self.doc_header.show()
        self.dock.set_mode("processing", text)
        self.dock.show()
        self._set_live_slot(False)

    def _set_doc_header(self, title: str, base_meta: str = "") -> None:
        self.lbl_doc_title.setText(title)
        self._doc_base_meta = base_meta
        self.lbl_doc_meta.setText(base_meta)
        self.lbl_doc_meta.setVisible(bool(base_meta))

    def _on_progress_changed(self, value: int, text: str) -> None:
        """Postęp transkrypcji w tle pokazujemy jako procent w panelu nagrywania (pełny opis w podpowiedzi)."""
        from recorder.ui.settings_dialog import strip_leading_symbols
        clean = strip_leading_symbols(text or "")
        if not self.dock.isVisible():
            return
        if self.dock.mode() == "processing":
            self.dock.set_processing_text(clean or "Przetwarzanie…")
            return
        if value > 0:
            self.dock.set_progress(value, clean)
        else:
            self.dock.set_progress(None, clean)

    @staticmethod
    def _format_clock(seconds: int) -> str:
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def _model_short_name(self, model_id: Optional[str]) -> str:
        from recorder.ui.settings_dialog import strip_leading_symbols
        info = ASR_MODELS.get(model_id or "", {})
        label = strip_leading_symbols(info.get("label", "") or str(model_id or ""))
        return label.split(" (")[0] if label else ""

    # ------------------------------------------------------------------
    # Źródła dźwięku i model zapisane w ustawieniach (karta „Nagrywanie”)
    # ------------------------------------------------------------------
    def _resolve_mic_device(self):
        """Zwraca (indeks, etykieta) mikrofonu zapisanego w ustawieniach lub domyślnego."""
        saved_name = str(load_user_settings().get("mic_device_name", "")).strip()
        try:
            devices = get_working_input_devices(force_refresh=False) or []
        except Exception:
            devices = []
        chosen = None
        if saved_name:
            chosen = next((d for d in devices if d.get("name") == saved_name), None)
        if chosen is None:
            chosen = next((d for d in devices if d.get("is_default")), devices[0] if devices else None)
        if chosen is None:
            return None, ""
        return chosen.get("index"), chosen.get("label") or chosen.get("name", "")

    def _resolve_loopback_index(self):
        raw = str(get_loopback_device_index() or "").strip()
        return int(raw) if raw.isdigit() else None

    def _auto_pause_seconds(self) -> float:
        try:
            return max(1.0, float(load_user_settings().get("auto_pause_sec", DEFAULT_AUTO_PAUSE_SEC)))
        except Exception:
            return float(DEFAULT_AUTO_PAUSE_SEC)

    def _refresh_source_pill(self) -> None:
        from recorder.ui.settings_dialog import clean_device_label
        from recorder.ui.widgets import current_tokens, _combined_icon
        from recorder.ui.icons import make_icon
        st = load_user_settings()
        mode = get_record_source_mode()
        mic = clean_device_label(str(st.get("mic_device_label", "") or st.get("mic_device_name", ""))) or "Domyślny mikrofon"
        sysd = clean_device_label(str(st.get("loopback_device_label", ""))) or "Domyślne wyjście"

        def short(txt: str) -> str:
            txt = txt.replace("(Domyślne)", "").replace("(domyślne)", "").strip()
            if "(" in txt and txt.endswith(")"):
                inner = txt[txt.rfind("(") + 1:-1].strip()
                if inner:
                    txt = inner
            return txt if len(txt) <= 26 else txt[:25] + "…"

        t = current_tokens()
        if mode == RecordSourceMode.MIC_ONLY:
            text, icon = short(mic), make_icon("mic", t.text_secondary, 14)
        elif mode == RecordSourceMode.SYSTEM_ONLY:
            text, icon = short(sysd), make_icon("headphones", t.text_secondary, 14)
        else:
            text, icon = f"{short(mic)} · {short(sysd)}", _combined_icon(("mic", "headphones"), t.text_secondary)
        app_filter = get_target_app_filter()
        if app_filter and mode != RecordSourceMode.MIC_ONLY:
            text += f" · {app_filter}"
        self.btn_source_pill.setText(text)
        self.btn_source_pill.setIcon(icon)
        self.btn_source_pill.setIconSize(QSize(34, 14) if mode == RecordSourceMode.HYBRID_DUAL else QSize(14, 14))

    # ------------------------------------------------------------------
    # Podpisy mówców edytowane z paska źródeł (bez otwierania Ustawień)
    # ------------------------------------------------------------------
    _SPEAKER_NAME_KEYS = {"mic": "mic_name", "system": "system_name",
                          **{f"mic{i}": f"mic_name_{i}" for i in range(1, 5)}}

    def _speaker_name_channels(self):
        """Kanały widoczne w okienku podpisów: (kanał, opis źródła, nazwa domyślna, bieżąca nazwa)."""
        from recorder.config import (
            get_channel_speaker_name, is_mic_stereo_split, DEFAULT_MIC_NAME, DEFAULT_SYSTEM_NAME
        )
        from recorder.ui.workers import MAX_MIC_LANES
        mode = get_record_source_mode()
        rows = []
        if mode != RecordSourceMode.SYSTEM_ONLY:
            if is_mic_stereo_split():
                lanes = int(getattr(self.worker, "_mic_lanes", 1) or 1) if self.is_recording() else 1
                for i in range(1, (lanes if lanes > 1 else MAX_MIC_LANES) + 1):
                    rows.append((f"mic{i}", f"Kanał {i}", f"Osoba {i}", get_channel_speaker_name(f"mic{i}")))
            else:
                rows.append(("mic", "Mikrofon", DEFAULT_MIC_NAME, get_channel_speaker_name("mic")))
        if mode != RecordSourceMode.MIC_ONLY:
            rows.append(("system", "Dźwięk systemu", DEFAULT_SYSTEM_NAME, get_channel_speaker_name("system")))
        return rows

    def _open_speaker_names_popover(self) -> None:
        from recorder.ui.widgets import SpeakerNamesPopover
        hint = ("Zmiana obejmie też bieżące nagranie." if self.is_recording()
                else "Puste pole = nazwa domyślna.")
        pop = SpeakerNamesPopover(self._speaker_name_channels(), self, hint=hint)
        pop.names_saved.connect(self._apply_speaker_names)
        pop.settings_requested.connect(lambda: self._open_settings_dialog(initial_tab="recording"))
        self._speaker_names_popover = pop
        pop.show_below(self.btn_source_pill)

    def _apply_speaker_names(self, names: dict) -> None:
        """Zapisuje nowe podpisy kanałów i przemianowuje wypowiedzi bieżącego nagrania."""
        from recorder.config import get_channel_speaker_name
        names = {ch: v for ch, v in (names or {}).items() if ch in self._SPEAKER_NAME_KEYS}
        if not names:
            return
        before = {ch: get_channel_speaker_name(ch) for ch in names}
        save_user_settings({self._SPEAKER_NAME_KEYS[ch]: v for ch, v in names.items()})
        renamed = {}
        for ch in names:
            new_name = get_channel_speaker_name(ch)
            if new_name != before[ch]:
                renamed[ch] = (before[ch], new_name)
        if not renamed:
            return
        logger.info(f"[PODPISY] Zmieniono podpisy kanałów: {renamed}")
        if self.is_recording():
            self._relabel_live_turns(renamed)
        self._set_cloud_status("Zapisano podpisy mówców.", "info")

    def _relabel_live_turns(self, renamed: dict) -> None:
        """Podmienia nazwę mówcy w wypowiedziach bieżącej sesji, które nosiły dotychczasowy podpis kanału."""
        rw = getattr(self, "rolling_worker", None)
        seen = set()
        for turns in (getattr(rw, "all_turns", None) or [], self.current_turns or []):
            for t in list(turns):
                if id(t) in seen or not isinstance(t, dict):
                    continue
                seen.add(id(t))
                pair = renamed.get(t.get("channel", "mic"))
                if pair and t.get("speaker") == pair[0]:
                    t["speaker"] = pair[1]
        if rw is None:
            return
        try:
            html, plain, turns = rw._compile_full_transcript()
            rw._cached_html, rw._cached_plain = html, plain
            self.current_turns = turns
            self.last_plain_text = plain
            if turns:
                self.text_transcript.setHtml(html)
                self._scroll_transcript_view()
                self._apply_live_slot()
        except Exception as e:
            logger.warning(f"Nie udało się odświeżyć podglądu po zmianie podpisów: {e}")

    def _apply_source_mode_to_ui(self) -> None:
        mode = get_record_source_mode()
        self.dock.set_channels(mode != RecordSourceMode.SYSTEM_ONLY, mode != RecordSourceMode.MIC_ONLY)
        self._refresh_source_pill()

    # Zgodność ze starszym API
    _on_source_mode_changed = _apply_source_mode_to_ui

    def _refresh_audio_devices(self):
        """Odświeża opis źródeł dźwięku w pasku górnym i kanały w panelu nagrywania."""
        self._apply_source_mode_to_ui()

    # ------------------------------------------------------------------
    # Nakładki: historia i powiadomienia chmury
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Podgląd na żywo: miejsce na następną wypowiedź, kropki „pisze…”, pojawianie się wierszy
    # ------------------------------------------------------------------
    LIVE_SLOT_PX = 40

    def _newest_first(self) -> bool:
        from recorder.config import get_preview_order
        try:
            return get_preview_order() != "chronological"
        except Exception:
            return True

    def _set_live_slot(self, on: bool) -> None:
        self._live_slot = bool(on)
        if not on:
            self._set_typing(False)
        self._apply_live_slot()

    def _schedule_live_slot(self) -> None:
        # setHtml() odtwarza dokument, więc margines na następną wypowiedź trzeba przywrócić
        if not self._live_slot_pending:
            self._live_slot_pending = True
            QTimer.singleShot(0, self._apply_live_slot)

    def _apply_live_slot(self) -> None:
        self._live_slot_pending = False
        doc = self.text_transcript.document()
        root = doc.rootFrame()
        fmt = root.frameFormat()
        margin = doc.documentMargin()
        newest_first = self._newest_first()
        top = margin + (self.LIVE_SLOT_PX if self._live_slot and newest_first else 0)
        bottom = margin + (self.LIVE_SLOT_PX if self._live_slot and not newest_first else 0)
        if fmt.topMargin() != top or fmt.bottomMargin() != bottom:
            fmt.setTopMargin(top)
            fmt.setBottomMargin(bottom)
            root.setFrameFormat(fmt)
        self._position_typing_dots()

    def _transcript_table(self):
        from PySide6.QtGui import QTextTable
        for frame in self.text_transcript.document().rootFrame().childFrames():
            if isinstance(frame, QTextTable):
                return frame
        return None

    def _position_typing_dots(self, *_args) -> None:
        dots = getattr(self, "typing_dots", None)
        if dots is None or not dots.is_active():
            return
        doc = self.text_transcript.document()
        margin = doc.documentMargin()
        scroll = self.text_transcript.verticalScrollBar().value()
        x = int(margin)
        table = self._transcript_table()
        if table is not None and table.rows() > 0 and table.columns() >= 3:
            x = self.text_transcript.cursorRect(table.cellAt(0, 2).firstCursorPosition()).left()
        offset = (self.LIVE_SLOT_PX - dots.height()) / 2.0
        if self._newest_first():
            y = margin + offset - scroll
        else:
            y = doc.size().height() - margin - self.LIVE_SLOT_PX + offset - scroll
        dots.move(max(0, x - 4), int(y))

    def _set_typing(self, on: bool) -> None:
        on = bool(on) and self._live_slot
        self.typing_dots.set_active(on)
        if on:
            self._position_typing_dots()

    def _play_new_rows_fade(self, old_rows: int) -> None:
        table = self._transcript_table()
        if table is None:
            return
        rows = table.rows()
        added = rows - old_rows
        if added <= 0 or added > 6:
            return
        if self._newest_first():
            first, last = 0, added - 1
        else:
            first, last = rows - added, rows - 1
        top = self.text_transcript.cursorRect(table.cellAt(first, 0).firstCursorPosition()).top() - 6
        bottom = self.text_transcript.cursorRect(table.cellAt(last, 2).lastCursorPosition()).bottom() + 10
        vp = self.text_transcript.viewport()
        rect = QRect(0, top, vp.width(), max(0, bottom - top)).intersected(vp.rect())
        from recorder.ui.widgets import current_tokens
        self.row_fade.play(rect, current_tokens().bg_surface)
        self.typing_dots.raise_()

    def eventFilter(self, obj, event):
        if obj is getattr(self, "_main_widget", None) and event.type() == QEvent.Type.Resize:
            self._reposition_overlays()
        elif event.type() == QEvent.Type.Resize and hasattr(self, "typing_dots") and obj is self.text_transcript.viewport():
            self._position_typing_dots()
        return super().eventFilter(obj, event)

    def _reposition_overlays(self) -> None:
        if hasattr(self, "history_panel"):
            self.history_panel.reposition()
        if hasattr(self, "cloud_toast"):
            self.cloud_toast.set_anchor_top(self.sheet.y() + 14)

    def _toggle_history_panel(self, checked: bool = False) -> None:
        if self.history_panel.isVisible():
            self.history_panel.close_panel()
            return
        self._refresh_history()
        self.history_panel.open_panel()
        self.btn_history.setChecked(True)

    def _refresh_history(self) -> None:
        from recorder.ui.widgets import collect_history
        try:
            entries = collect_history(self.recordings_dir, self.transcriptions_dir)
        except Exception as e:
            logger.warning(f"Nie udało się odczytać historii nagrań: {e}")
            entries = []
        self.history_panel.populate(entries)
        self._history_dirty = False

    def _mark_history_dirty(self) -> None:
        self._history_dirty = True
        if self.history_panel.isVisible():
            self._refresh_history()

    def _open_audio_file(self, path: str) -> None:
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _show_cloud_problem(self, title: str, desc: str) -> None:
        self.cloud_toast.set_anchor_top(self.sheet.y() + 14)
        self.cloud_toast.show_message(title, desc, kind="error", retry=True)

    def _on_cloud_retry_clicked(self) -> None:
        self.cloud_toast.dismiss()
        try:
            self.cloud_sync.process_offline_queue_async()
        except Exception as e:
            logger.warning(f"Ponowna wysyłka kolejki offline nie powiodła się: {e}")

    def _flash_icon(self, btn, icon_name: str, restore: str) -> None:
        btn.set_icon_name(icon_name)
        QTimer.singleShot(1600, lambda: btn.set_icon_name(restore))

    def _apply_theme_extras(self) -> None:
        """Przerysowuje ikony i styl tekstu transkrypcji po zmianie motywu."""
        from recorder.ui.widgets import IconButton, current_tokens
        from recorder.config import get_font_size
        for btn in self.findChildren(IconButton):
            btn.apply_theme()
        self.dock.apply_theme()
        self.btn_start.update()
        t = current_tokens()
        body = max(13, get_font_size() + 3)
        from recorder.config import get_theme as _get_theme, get_speaker_colors as _get_speaker_colors
        lane_c = _get_speaker_colors(_get_theme())
        self.text_transcript.document().setDefaultStyleSheet(
            f"table.tr {{ margin: 0px; }}"
            f"td.t {{ color: {t.text_muted}; font-size: 11px; white-space: nowrap; padding: 6px 18px 12px 0px; }}"
            f"td.sm {{ color: {t.speaker_mic}; font-size: 10px; font-weight: 600; white-space: nowrap; padding: 6px 18px 12px 0px; }}"
            f"td.ss {{ color: {t.speaker_system}; font-size: 10px; font-weight: 600; white-space: nowrap; padding: 6px 18px 12px 0px; }}"
            f"td.sm1 {{ color: {lane_c.get('mic1', t.speaker_mic)}; font-size: 10px; font-weight: 600; white-space: nowrap; padding: 6px 18px 12px 0px; }}"
            f"td.sm2 {{ color: {lane_c.get('mic2', t.speaker_mic)}; font-size: 10px; font-weight: 600; white-space: nowrap; padding: 6px 18px 12px 0px; }}"
            f"td.sm3 {{ color: {lane_c.get('mic3', t.speaker_mic)}; font-size: 10px; font-weight: 600; white-space: nowrap; padding: 6px 18px 12px 0px; }}"
            f"td.sm4 {{ color: {lane_c.get('mic4', t.speaker_mic)}; font-size: 10px; font-weight: 600; white-space: nowrap; padding: 6px 18px 12px 0px; }}"
            f"td.x {{ color: {t.text_primary}; font-family: 'Source Serif 4', Georgia, 'Cambria', serif; font-size: {body}px; padding: 0px 0px 12px 0px; }}"
            f"p.hint {{ color: {t.text_secondary}; font-size: 13px; }}"
        )
        self._refresh_source_pill()
        self._refresh_current_transcript_view()

    def _open_settings_dialog(self, initial_tab=None):
        """Otwiera okno ustawień (źródła dźwięku, model, słownik, VAD, wygląd, chmura)."""
        dlg = SettingsDialog(self)
        if initial_tab is not None:
            dlg.select_tab(initial_tab)
        accepted = dlg.exec()
        # Motyw mógł zostać zmieniony podglądem lub zapisem: odśwież ikony i styl tekstu
        self._apply_theme_extras()
        if not accepted:
            return
        st = load_user_settings()
        # Czułość VAD w aktywnym detektorze
        new_vad = float(st.get("vad_speech_threshold", 0.35))
        if hasattr(self, "worker") and getattr(self.worker, "vad_detector", None):
            self.worker.vad_detector.speech_threshold = new_vad

        # Czas auto-pauzy, ostrzeżenie o ciszy i podział sesji
        if hasattr(self, "worker"):
            self.worker.set_auto_pause_sec(self._auto_pause_seconds())
            self.worker.set_silence_alert_seconds(get_silence_alert_seconds())
            self.worker.set_session_split_silence_sec(get_session_split_silence_sec())

        # Aplikację audio można przełączyć w locie, bez zatrzymywania nagrania
        if self.is_recording():
            try:
                self.worker.update_target_app_filter(get_target_app_filter())
            except Exception as e:
                logger.warning(f"Nie udało się przełączyć aplikacji audio w locie: {e}")
        else:
            self._apply_source_mode_to_ui()
        self._refresh_source_pill()

        # Odświeżenie podglądu transkrypcji (kolejność / format) i flagi Always on Top
        self._refresh_current_transcript_view()
        self.set_always_on_top(is_always_on_top())

    def _start_silent_update_check(self):
        """Cicho sprawdza w tle na GitHubie dostępność nowszej wersji programu."""
        try:
            from recorder.core.updater import CheckUpdateWorker
            st = load_user_settings()
            inc_pre = bool(st.get("check_prereleases", True))
            self._startup_update_worker = CheckUpdateWorker(include_prereleases=inc_pre)
            self._active_threads.append(self._startup_update_worker)
            self._startup_update_worker.update_checked_signal.connect(self._on_startup_update_result)
            self._startup_update_worker.finished.connect(
                lambda: self._active_threads.remove(self._startup_update_worker) if hasattr(self, "_active_threads") and hasattr(self, "_startup_update_worker") and self._startup_update_worker in self._active_threads else None
            )
            self._startup_update_worker.start()
        except Exception as e:
            print(f"[UPDATER] Ciche sprawdzenie aktualizacji pominięte: {e}")

    def _on_startup_update_result(self, result):
        """Obsługuje wynik cichego sprawdzania aktualizacji przy starcie."""
        if result and result.get("has_update"):
            latest_v = result.get("latest_version", "")
            self.lbl_update_banner_text.setText(f"Dostępna jest nowa wersja aplikacji: <b>{latest_v}</b>")
            self.banner_update.show()

    def set_pending_update(self, zip_path: str, version: str):
        """Ustawia paczkę aktualizacji do zainstalowania przy zamknięciu programu."""
        self._pending_update_zip_path = zip_path
        self._pending_update_version = version

    def _scroll_transcript_view(self):
        """Automatycznie ustawia pozycję paska przewijania w oknie transkrypcji."""
        from recorder.config import get_preview_order, is_auto_scroll_chronological
        order = get_preview_order()
        auto_scroll = is_auto_scroll_chronological()

        def do_scroll():
            sb = self.text_transcript.verticalScrollBar()
            if order == "chronological":
                if auto_scroll:
                    sb.setValue(sb.maximum())
            else:
                sb.setValue(0)

        do_scroll()
        QTimer.singleShot(25, do_scroll)

    def _refresh_current_transcript_view(self):
        """Odświeża wyświetlanie bieżącej transkrypcji zgodnie z aktualną konfiguracją (kolejność, timestampy)."""
        if not self.current_turns:
            return

        session_dt = getattr(self, "session_start_time", None) or getattr(self, "current_session_start_time", None)
        if not session_dt and self.current_txt_path:
            session_dt = extract_datetime_from_filename(self.current_txt_path)
        if not session_dt and self.last_audio_save_path:
            session_dt = extract_datetime_from_filename(self.last_audio_save_path)

        if self.current_txt_path:
            json_path = get_session_path_for_txt(self.current_txt_path)
            if os.path.exists(json_path):
                sess = TranscriptionSession.load_from_json(json_path)
                if sess and sess.turns:
                    html_content = sess.export_to_html(session_start_time=session_dt)
                    self.text_transcript.setHtml(html_content)
                    self._scroll_transcript_view()
                    return

        html_content, _ = format_turns(self.current_turns, session_start_time=session_dt)
        self.text_transcript.setHtml(html_content)
        self._scroll_transcript_view()

    def _on_copy_transcript_clicked(self):
        """Kopiuje bieżącą transkrypcję do schowka systemowego."""
        text = self.last_plain_text or self.text_transcript.toPlainText()
        if not text or not text.strip():
            QMessageBox.information(self, "Brak transkrypcji", "Nie ma jeszcze żadnej transkrypcji do skopiowania.")
            return
        QApplication.clipboard().setText(text)
        self._flash_icon(self.btn_copy_transcript, "check", "copy")

    def _on_save_transcript_clicked(self):
        """Otwiera dialog 'Zapisz jako' i eksportuje transkrypcję do wybranego pliku .txt."""
        text = self.last_plain_text or self.text_transcript.toPlainText()
        if not text or not text.strip():
            QMessageBox.information(self, "Brak transkrypcji", "Nie ma jeszcze żadnej transkrypcji do zapisania.")
            return

        # Propozycja nazwy pliku na podstawie bieżącego timestampu lub aktualnej transkrypcji
        if hasattr(self, "current_live_timestamp") and self.current_live_timestamp:
            default_name = f"transkrypcja_{self.current_live_timestamp}.txt"
        elif self.current_txt_path:
            default_name = os.path.basename(self.current_txt_path)
        else:
            default_name = f"transkrypcja_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Zapisz transkrypcję jako...",
            os.path.join(os.path.expanduser("~"), "Desktop", default_name),
            "Plik tekstowy (*.txt);;Wszystkie pliki (*.*)"
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(text)
            self._flash_icon(self.btn_save_transcript, "check", "download")
        except Exception as e:
            QMessageBox.critical(self, "Błąd zapisu", f"Nie udało się zapisać pliku:\n{e}")

    def _apply_theme(self):
        """Deleguje aplikację stylów do scentralizowanego silnika theme.py."""
        from recorder.ui.theme import apply_theme
        from recorder.config import get_theme, get_font_size
        apply_theme(
            app=QApplication.instance(),
            theme_id=get_theme(),
            font_size=get_font_size(),
            window=self
        )
        if hasattr(self, "dock"):
            self._apply_theme_extras()

    def _set_cloud_status(self, text: str, status: str = "info") -> None:
        """Zapamiętuje ostatni komunikat stanu (log). Błędy chmury pokazuje _show_cloud_problem()."""
        self._last_status_text = text
        self._last_status_kind = status
        logger.info(f"[STATUS] {text}")

    def _toggle_mic_mute(self):
        """Wycisza lub przywraca nasłuch z mikrofonu w locie."""
        new_state = not getattr(self, "_mic_is_muted", False)
        self._mic_is_muted = new_state
        self.dock.ch_mic.set_muted(new_state)
        if hasattr(self, "worker") and self.worker is not None:
            self.worker.set_mic_muted(new_state)
        self._set_cloud_status("Wyciszono mikrofon." if new_state else "Włączono mikrofon.", "info")

    def _toggle_sys_mute(self):
        """Wycisza lub przywraca nasłuch dźwięku systemu w locie."""
        new_state = not getattr(self, "_sys_is_muted", False)
        self._sys_is_muted = new_state
        self.dock.ch_sys.set_muted(new_state)
        if hasattr(self, "worker") and self.worker is not None:
            self.worker.set_sys_muted(new_state)
        self._set_cloud_status("Wyciszono dźwięk systemu." if new_state else "Włączono dźwięk systemu.", "info")

    def _update_dual_audio_level(self, mic_lvl: float, sys_lvl: float):
        """Aktualizacja wskaźników poziomu mikrofonu i dźwięku systemu w panelu nagrywania."""
        try:
            m_val = 0 if getattr(self, "_mic_is_muted", False) else int(max(0, min(100, mic_lvl)))
            s_val = 0 if getattr(self, "_sys_is_muted", False) else int(max(0, min(100, sys_lvl)))
            self.dock.set_levels(m_val, s_val)
        except Exception:
            pass

    def _refresh_recordings_list(self):
        """Oznacza historię do odświeżenia (pliki .wav)."""
        self._mark_history_dirty()

    def _on_start_clicked(self):
        selected_mode = get_record_source_mode()
        selected_mic, mic_label = self._resolve_mic_device()
        selected_loopback = self._resolve_loopback_index()

        if selected_mode == RecordSourceMode.MIC_ONLY and selected_mic is None:
            QMessageBox.warning(
                self,
                "Brak Mikrofonu",
                "W systemie Windows nie wykryto aktywnego mikrofonu.\n\n"
                "Upewnij się, że mikrofon jest podłączony i włączony w:\n"
                "Ustawienia Windows -> System -> Dźwięk (Wejście),\n"
                "a następnie wybierz go w Ustawienia → Nagrywanie."
            )
            return

        # Guard: nie pozwól na start jeśli poprzednia sesja jeszcze nie zakończyła finalizacji
        if self._finalize_pending:
            QMessageBox.information(
                self,
                "Finalizacja w toku",
                "Poprzednie nagranie jest jeszcze finalizowane (zapis do chmury).\n"
                "Poczekaj chwilę i spróbuj ponownie."
            )
            return

        self.history_panel.close_panel()
        self.recorded_seconds = 0
        self._active_recorded_time = 0.0
        self._last_active_tick = None
        self.last_processed_block_idx = 0
        self.dock.set_time("00:00")

        self.live_plain_text_lines = []
        self.current_turns = []
        self.last_plain_text = ""
        self.current_txt_path = None
        self.text_transcript.clear()
        self.progress_transcription.setValue(0)

        # Jedno nagranie na dzień: Start po Stop tego samego dnia kontynuuje dzisiejsze nagranie
        now = datetime.now()
        day = self._load_day_record(now) if is_one_record_per_day() else None
        self.synced_segment_count = 0
        self._synced_turn_ids = set()
        self.current_meeting_id = None
        if day is not None:
            timestamp = day["timestamp"]
            self.session_start_time = day["start"]
            self.current_live_timestamp = timestamp
            self.current_live_txt_path = day["txt_path"]
            self.current_live_wav_path = day["wav_path"]
            self.current_meeting_id = day["meeting_id"]
            self.current_turns = day["turns"]
            self._synced_turn_ids = {get_turn_sync_id(t) for t in day["turns"]}
            self.synced_segment_count = len(self._synced_turn_ids)
            # Stoper pokazuje łączny czas nagrania z całego dnia
            self._active_recorded_time = float(day["offset_sec"])
            self.recorded_seconds = int(day["offset_sec"])
            self.dock.set_time(self._format_clock(self.recorded_seconds))
            logger.info(f"[SESJA START] Kontynuacja dzisiejszego nagrania {timestamp} "
                        f"(wypowiedzi: {len(day['turns'])}, od {day['offset_sec']:.0f} s)")
        else:
            # Timestamp z mikrosekundami — zapobiega kolizji UUID5 przy szybkim Stop→Start w tej samej sekundzie
            self.session_start_time = now
            timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
            self.current_live_timestamp = timestamp
            self.current_live_txt_path = os.path.join(self.transcriptions_dir, f"transkrypcja_{timestamp}.txt")
            self.current_live_wav_path = os.path.join(self.recordings_dir, f"inteligentne_nagranie_{timestamp}.wav")
            try:
                with open(self.current_live_txt_path, 'w', encoding='utf-8') as f:
                    f.write(f"=== TRANSKRYPCJA NA ŻYWO (Start: {now.strftime('%Y-%m-%d %H:%M:%S')}) ===\n\n")
                self._refresh_transcriptions_list()
            except Exception:
                pass

        selected_model = get_default_model_id()
        self._active_model_id = selected_model

        # Inicjalizacja sesji w Supabase dla transmisji na żywo do CRM
        if self.cloud_sync.config.get("live_streaming") and self.cloud_sync.config.get("auto_sync"):
            self.current_meeting_id = self.cloud_sync.start_live_session_async(
                title=f"Spotkanie biurowe {self.session_start_time.strftime('%Y-%m-%d %H:%M')}",
                meeting_id=self.current_meeting_id
            )
            target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
            self._set_cloud_status(f"Transmisja na żywo do {target_name} aktywna", "info")

        selected_target_app = get_target_app_filter()
        logger.info(
            f"[SESJA START] Rozpoczęto nagrywanie: tryb='{selected_mode}', "
            f"mikrofon='{mic_label}' ({selected_mic}), loopback='{selected_loopback}', aplikacja='{selected_target_app}', "
            f"model='{selected_model}', meeting_id='{self.current_meeting_id}'"
        )

        from recorder.ui.widgets import polish_date_title
        self._set_doc_header(polish_date_title(self.session_start_time))
        if day is not None and self.current_turns:
            html, _plain = format_turns(self.current_turns, session_start_time=self.session_start_time)
            self.last_plain_text = _plain
            self.text_transcript.setHtml(html)
            self._scroll_transcript_view()
        else:
            self.text_transcript.setHtml(
                "<p class='hint'>Słucham. Pierwsze zdania pojawią się tutaj po kilku sekundach mowy.</p>"
            )

        # Zabezpieczenie: zatrzymanie i wyczyszczenie poprzedniego wątku rolling_worker
        if getattr(self, "rolling_worker", None) is not None:
            try:
                self.rolling_worker.blockSignals(True)
                if self.rolling_worker.isRunning():
                    self.rolling_worker.stop()
                    self.rolling_worker.wait(1500)
                if self.rolling_worker in self._active_threads:
                    self._active_threads.remove(self.rolling_worker)
            except Exception:
                pass

        self._asr_error_shown = False
        # Uruchomienie silnika asynchronicznego przetwarzania bloków w tle (Rolling Background Transcriber)
        self.rolling_worker = RollingTranscriptionWorker(
            model_size=selected_model,
            txt_save_path=self.current_live_txt_path,
            session_start_time=self.session_start_time
        )
        self._active_threads.append(self.rolling_worker)
        self.rolling_worker.block_processed_signal.connect(self._on_rolling_block_processed)
        self.rolling_worker.status_signal.connect(self._on_rolling_status)
        self.rolling_worker.finished_signal.connect(self._on_rolling_finished)
        self.rolling_worker.error_signal.connect(self._on_rolling_error)
        if day is not None:
            self.rolling_worker.preload_session(day["turns"], day["words"], offset_sec=day["offset_sec"])
        self.rolling_worker.start()

        self.worker.rolling_block_ready_signal.connect(self.rolling_worker.add_block)

        self.worker.set_auto_pause_sec(self._auto_pause_seconds())
        self.worker.set_session_split_silence_sec(get_session_split_silence_sec())
        self.worker.set_block_profile_for_model(selected_model)
        self._apply_source_mode_to_ui()
        self.worker.start_recording(
            device_index=selected_mic,
            loopback_device_index=selected_loopback,
            source_mode=selected_mode,
            target_app_filter=selected_target_app,
            save_wav_path=self.current_live_wav_path,
            mic_muted=getattr(self, "_mic_is_muted", False),
            sys_muted=getattr(self, "_sys_is_muted", False),
            append_wav=day is not None
        )
        # Dopisywanie w innym formacie (zmiana trybu nagrywania) trafia do pliku „_czN.wav”
        self.current_live_wav_path = getattr(self.worker, "save_wav_path", None) or self.current_live_wav_path
        self.timer.start()

        self.btn_start.setEnabled(False)
        self.btn_upload.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self._show_recording_view()

    def _on_rolling_block_processed(self, block_idx, proc_sec, tot_sec, all_turns, full_plain, full_html):
        """Odebranie przetworzonego w tle bloku mowy z pełnymi word-level timestampami i synchronizacja na żywo."""
        self.current_turns = all_turns or []
        self.last_plain_text = full_plain
        if full_html:
            table = self._transcript_table()
            old_rows = table.rows() if table is not None else 0
            self.text_transcript.setHtml(full_html)
            self._scroll_transcript_view()
            self._apply_live_slot()
            if not self._last_is_speech:
                self._set_typing(False)
            QTimer.singleShot(30, lambda: self._play_new_rows_fade(old_rows))


        # Transmisja na żywo nowych segmentów do Supabase / CRM
        if self.cloud_sync.config.get("live_streaming") and self.cloud_sync.config.get("auto_sync") and self.current_meeting_id:
            new_segments = [t for t in (all_turns or []) if get_turn_sync_id(t) not in self._synced_turn_ids]
            if new_segments:
                for t in new_segments:
                    self._synced_turn_ids.add(get_turn_sync_id(t))
                self.synced_segment_count = len(self._synced_turn_ids)
                spk_cnt = len(set(t.get("speaker", "Mówca") for t in (all_turns or []) if t.get("speaker")))
                logger.info(
                    f"[LIVE SYNC] Blok #{block_idx}: +{len(new_segments)} nowych segmentów "
                    f"(łącznie w sesji: {self.synced_segment_count}, unikalni mówcy: {spk_cnt})"
                )
                self.cloud_sync.append_live_segments_async(
                    meeting_id=self.current_meeting_id,
                    new_segments=new_segments,
                    full_transcript=full_plain,
                    duration_seconds=tot_sec,
                    speaker_count=max(1, spk_cnt)
                )

        # Aktualizacja paska postępu
        self.last_processed_block_idx = block_idx
        pct = int(min(98, max(5, (proc_sec / max(1.0, tot_sec)) * 100)))
        p_min, p_sec = int(proc_sec // 60), int(proc_sec % 60)
        t_min, t_sec = int(tot_sec // 60), int(tot_sec % 60)
        self.progress_transcription.setValue(pct)
        self.progress_transcription.setFormat(f"🟢 Przetworzono w tle: {p_min:02d}:{p_sec:02d} / {t_min:02d}:{t_sec:02d} ({pct}% · blok #{block_idx})")

    def _on_rolling_status(self, text):
        if self.worker.state in [SmartRecordState.RECORDING_SPEECH, SmartRecordState.RECORDING_SILENCE_COUNTDOWN]:
            self.progress_transcription.setFormat(f"Transkrypcja w tle: {text}")

    def _on_rolling_error(self, err_msg):
        if sys.stderr:
            print(f"Błąd transkrypcji w tle: {err_msg}", file=sys.stderr)
        logger.error(f"[TRANSKRYPCJA W TLE] {err_msg}")
        self.progress_transcription.setFormat("🔴 Silnik rozpoznawania mowy nie działa - zobacz komunikat błędu")

        # Jeden komunikat na sesję nagrywania (kolejne błędy trafiają tylko do logu)
        if getattr(self, "_asr_error_shown", False):
            return
        self._asr_error_shown = True
        hint = ""
        if getattr(self, "_active_model_id", None) == PARAKEET_MODEL_ID:
            hint = (
                "\n\nPrzy pierwszym uruchomieniu Parakeet pobiera model z internetu. Jeśli komputer nie ma dostępu "
                "do sieci, pobierz model ręcznie i wskaż jego folder w Ustawienia → Słownik i AI → "
                "'Lokalny model Parakeet'."
            )
        QMessageBox.warning(self, "Silnik rozpoznawania mowy", f"Nie udało się uruchomić transkrypcji:\n{err_msg}{hint}")

    def _on_pause_clicked(self):
        self.worker.toggle_manual_pause()

    def _load_day_record(self, now: datetime):
        """
        Dane dzisiejszego nagrania do kontynuacji (ścieżki, wypowiedzi, słowa, meeting_id, przesunięcie osi czasu)
        albo None, gdy dziś jeszcze nic nie nagrano.
        """
        from recorder.core.day_record import find_day_record, timestamp_to_datetime
        from recorder.audio.capture import wav_duration_seconds
        ts = find_day_record(self.transcriptions_dir, now.date())
        if not ts:
            return None
        txt_path = os.path.join(self.transcriptions_dir, f"transkrypcja_{ts}.txt")
        wav_path = os.path.join(self.recordings_dir, f"inteligentne_nagranie_{ts}.wav")
        sess = None
        try:
            sess = TranscriptionSession.load_from_json(get_session_path_for_txt(txt_path))
        except Exception as e:
            logger.warning(f"Nie udało się wczytać sesji dzisiejszego nagrania {ts}: {e}")
        turns = [dict(t) for t in (getattr(sess, "turns", None) or []) if isinstance(t, dict)]
        words = list(getattr(sess, "words", None) or [])
        offset = wav_duration_seconds(wav_path) if os.path.exists(wav_path) else 0.0
        # Nagranie mogło trafić też do plików „_czN.wav” - oś czasu liczymy od końca ostatniej wypowiedzi
        try:
            offset = max(offset, max((float(t.get("end", 0.0)) for t in turns), default=0.0))
        except Exception:
            pass
        return {
            "timestamp": ts,
            "start": timestamp_to_datetime(ts) or now,
            "txt_path": txt_path,
            "wav_path": wav_path,
            "turns": turns,
            "words": words,
            "meeting_id": getattr(sess, "meeting_id", None) or None,
            "offset_sec": round(offset, 2),
        }

    def _on_stop_clicked(self):
        self.timer.stop()
        self.worker.stop_recording()
        self.worker.wait()

        # Odłączenie sygnału bloków
        try:
            self.worker.rolling_block_ready_signal.disconnect(self.rolling_worker.add_block)
        except Exception:
            pass

        timestamp = getattr(self, "current_live_timestamp", datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        filename = f"inteligentne_nagranie_{timestamp}.wav"
        save_path = getattr(self, "current_live_wav_path", None) or os.path.join(self.recordings_dir, filename)
        self._finishing_live_txt_path = getattr(self, "current_live_txt_path", None)

        saved = self.worker.save_wav(save_path)
        self.last_audio_save_path = save_path if saved else None

        self.dock.reset_levels()
        self.dock.set_silence_fraction(0.0)

        # Blokada przycisku Start do czasu zakończenia finalizacji
        self._finalize_pending = True
        self.btn_start.setEnabled(False)
        self.btn_upload.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)

        if getattr(self, "_mic_is_muted", False):
            self._toggle_mic_mute()
        if getattr(self, "_sys_is_muted", False):
            self._toggle_sys_mute()

        if saved:
            self._refresh_recordings_list()
            self._refresh_transcriptions_list()

            # Pobranie wszystkich zaległych bloków audio (mikrofon + loopback)
            final_block = None
            if hasattr(self.worker, "get_remaining_blocks"):
                rem_blocks = self.worker.get_remaining_blocks()
                if rem_blocks:
                    for b_idx, b_st, b_en, b_arr, b_ch in rem_blocks[:-1]:
                        if getattr(self, "rolling_worker", None) is not None:
                            self.rolling_worker.add_block(b_idx, b_st, b_en, b_arr, channel_source=b_ch)
                    last_idx, last_st, last_en, last_arr, last_ch = rem_blocks[-1]
                    final_block = RollingBlock(last_idx, last_st, last_en, last_arr, channel_source=last_ch)
            elif hasattr(self.worker, "get_remaining_block"):
                remaining = self.worker.get_remaining_block()
                if remaining:
                    r_idx, r_start, r_end, r_audio = remaining
                    final_block = RollingBlock(r_idx, r_start, r_end, r_audio)

            self._show_processing_view("Kończę transkrypcję…")
            self.progress_transcription.setFormat("Kończę transkrypcję ostatniego fragmentu")
            self.progress_transcription.setValue(95)

            # Przekazanie ostatniego fragmentu do finalizacji
            if getattr(self, "rolling_worker", None) is not None:
                self.rolling_worker.stop_and_finalize(final_block)
        else:
            self._finalize_pending = False
            self.btn_start.setEnabled(True)
            self._show_idle_view()
            QMessageBox.warning(self, "Brak Nagrania", "Nie zarejestrowano mowy do zapisu.")

    def _on_rolling_finished(self, final_html: str, final_plain: str, all_turns: list):
        """Zakończenie przetwarzania w tle po kliknięciu Stop."""
        words = []
        if hasattr(self, "rolling_worker") and self.rolling_worker:
            if hasattr(self.rolling_worker, "get_all_words"):
                words = self.rolling_worker.get_all_words()
            if self.rolling_worker in self._active_threads:
                self._active_threads.remove(self.rolling_worker)

        # Fallback pobrania słów z sesji JSON jeśli rolling_worker był już wyczyszczony
        if not words and hasattr(self, "current_live_txt_path") and self.current_live_txt_path:
            try:
                j_path = get_session_path_for_txt(self.current_live_txt_path)
                if os.path.exists(j_path):
                    s = TranscriptionSession.load_from_json(j_path)
                    if s and s.words:
                        words = s.words
            except Exception:
                pass

        self.current_turns = all_turns or []
        self.last_plain_text = final_plain
        self.text_transcript.setHtml(final_html)
        self._scroll_transcript_view()

        self._on_transcription_finished(final_html, final_plain, self.current_turns)

    def _on_upload_file_clicked(self):
        """
        Obsługa wgrywania zewnętrznego pliku audio/wideo (WAV, MP3, M4A, FLAC, OGG, AAC, MP4, MKV)
        bez konieczności posiadania zewnętrznego narzędzia FFmpeg w systemie Windows.
        """
        file_filter = (
            "Wszystkie Obsługiwane (*.mp4 *.mkv *.mov *.webm *.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma);;"
            "Nagrania Wideo (*.mp4 *.mkv *.mov *.webm);;"
            "Nagrania Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma);;"
            "Wszystkie pliki (*.*)"
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz plik audio do transkrypcji",
            "",
            file_filter
        )

        if not file_path:
            return

        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            QMessageBox.warning(self, "Niepoprawny Plik", "Wybrany plik jest pusty lub nie istnieje na dysku.")
            return

        filename = os.path.basename(file_path)
        selected_model = get_default_model_id()
        self._active_model_id = selected_model

        # Blokowanie kontrolek na czas przetwarzania pliku
        self.btn_start.setEnabled(False)
        self.btn_upload.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.history_panel.close_panel()

        self.current_turns = []
        self.last_plain_text = ""
        self.text_transcript.clear()
        self.text_transcript.setHtml(
            "<p class='hint'>Przetwarzam plik. Tekst pojawi się tutaj, gdy będą gotowe pierwsze fragmenty.</p>"
        )
        self._set_doc_header(filename)
        self._show_processing_view("Przygotowuję plik…")
        self.progress_transcription.setValue(0)
        self.progress_transcription.setFormat(f"Przygotowuję plik {filename}")

        self.file_processing_worker = FileProcessingWorker(
            input_file_path=file_path,
            recordings_dir=self.recordings_dir,
            model_size=selected_model
        )
        self._active_threads.append(self.file_processing_worker)
        self.file_processing_worker.progress_signal.connect(self._on_file_progress)
        self.file_processing_worker.preliminary_signal.connect(self._on_file_preliminary_transcript)
        self.file_processing_worker.finished_signal.connect(self._on_file_finished)
        self.file_processing_worker.error_signal.connect(self._on_file_error)
        self.file_processing_worker.start()

    def _on_preliminary_transcript(self, html_text: str, plain_text: str, turns: list = None):
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()
        self.current_turns = turns or []

    def _on_file_preliminary_transcript(self, html_text: str, plain_text: str, prepared_wav_path: str, turns: list = None):
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()
        base_name = os.path.basename(prepared_wav_path)
        file_stem = os.path.splitext(base_name)[0]
        txt_filename = f"transkrypcja_{file_stem}.txt"
        self.current_txt_path = os.path.join(self.transcriptions_dir, txt_filename)
        self.current_turns = turns or []
        self._refresh_transcriptions_list()

    def _on_file_progress(self, value: int, text: str):
        self.progress_transcription.setValue(value)
        self.progress_transcription.setFormat(f"{value}% - {text}")

    def _on_file_finished(self, html_text: str, plain_text: str, prepared_wav_path: str, turns: list = None):
        if hasattr(self, "file_processing_worker") and self.file_processing_worker in self._active_threads:
            self._active_threads.remove(self.file_processing_worker)

        self.progress_transcription.setValue(100)
        self.progress_transcription.setFormat("Przetwarzanie pliku zakończone!")
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()

        # Odblokowanie kontrolek
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self._show_idle_view(show_text=True)

        self.last_audio_save_path = prepared_wav_path
        self._refresh_recordings_list()

        # Zapis transkrypcji do pliku TXT
        base_name = os.path.basename(prepared_wav_path)
        file_stem = os.path.splitext(base_name)[0]
        txt_filename = f"transkrypcja_{file_stem}.txt"
        txt_path = os.path.join(self.transcriptions_dir, txt_filename)
        self.current_txt_path = txt_path
        self.current_turns = turns or []

        # Wypełnienie panelu mapowania mówców

        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(plain_text)

            # Zapis / Aktualizacja pliku sesji JSON
            json_path = get_session_path_for_txt(txt_path)
            try:
                session = TranscriptionSession.load_from_json(json_path) or TranscriptionSession()
                session.has_transcription = True
                session.prepared_wav = prepared_wav_path
                session.source_audio = prepared_wav_path
                session.turns = self.current_turns
                session.save_to_json(json_path)
            except Exception:
                pass

            self._refresh_transcriptions_list()
        except Exception as e:
            if sys.stderr:
                print(f"Błąd zapisu pliku TXT: {e}", file=sys.stderr)

        self.last_plain_text = plain_text
        self.btn_manual_sync.setEnabled(True)

        # Automatyczna synchronizacja z chmurą / EMANAGER.PRO
        if self.cloud_sync.config.get("auto_sync"):
            self._trigger_cloud_sync(
                plain_text=plain_text,
                turns=self.current_turns,
                audio_path=prepared_wav_path,
                title=f"Plik: {base_name}",
                silent=True
            )

        QMessageBox.information(
            self,
            "Plik Przetworzony",
            f"Pomyślnie przetworzono plik audio!\n\n"
            f"Zapisano audio 16kHz:\n{os.path.basename(prepared_wav_path)}\n\n"
            f"Zapisano transkrypcję:\n{txt_filename}"
        )

    def _on_file_error(self, err_msg: str):
        self.progress_transcription.setValue(0)
        self.progress_transcription.setFormat("Błąd przetwarzania pliku!")
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self._show_idle_view()
        QMessageBox.critical(self, "Błąd Przetwarzania Pliku", f"Wystąpił błąd podczas przetwarzania pliku audio:\n\n{err_msg}")

    def _on_transcription_progress(self, value, text):
        self.progress_transcription.setValue(value)
        self.progress_transcription.setFormat(f"{value}% - {text}")

    def _on_transcription_finished(self, html_text: str, plain_text: str, turns: list = None):
        if hasattr(self, "transcription_thread") and self.transcription_thread in self._active_threads:
            self._active_threads.remove(self.transcription_thread)

        self.progress_transcription.setValue(100)
        self.progress_transcription.setFormat("Transkrypcja zakończona!")
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self._show_idle_view(show_text=True)

        live_txt = getattr(self, "_finishing_live_txt_path", None)
        self._finishing_live_txt_path = None
        if live_txt:
            # Nagranie na żywo: transkrypcja zostaje w pliku sesji (także przy kontynuacji nagrania z tego dnia)
            txt_filename = os.path.basename(live_txt)
        elif self.last_audio_save_path:
            base_name = os.path.basename(self.last_audio_save_path)
            file_stem = os.path.splitext(base_name)[0]
            txt_filename = f"transkrypcja_{file_stem.replace('inteligentne_nagranie_', '')}.txt"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            txt_filename = f"transkrypcja_{timestamp}.txt"

        txt_path = os.path.join(self.transcriptions_dir, txt_filename)
        self.current_txt_path = txt_path
        self.current_turns = turns or []

        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(plain_text)

            # Zapis / Aktualizacja pliku sesji JSON
            json_path = get_session_path_for_txt(txt_path)
            try:
                session = TranscriptionSession.load_from_json(json_path) or TranscriptionSession()
                session.has_transcription = True
                if self.last_audio_save_path:
                    session.prepared_wav = self.last_audio_save_path
                    session.source_audio = self.last_audio_save_path
                session.turns = self.current_turns
                session.save_to_json(json_path)
            except Exception:
                pass

            self._refresh_transcriptions_list()
        except Exception as e:
            if sys.stderr:
                print(f"Błąd zapisu pliku TXT: {e}", file=sys.stderr)

        self.last_plain_text = plain_text
        self.btn_manual_sync.setEnabled(True)

        # Zakończenie finalizacji — odblokowanie przycisku Start
        self._finalize_pending = False
        self.btn_start.setEnabled(True)

        # Automatyczna synchronizacja z chmurą / EMANAGER.PRO
        if self.cloud_sync.config.get("auto_sync"):
            self._trigger_cloud_sync(
                plain_text=plain_text,
                turns=self.current_turns,
                audio_path=self.last_audio_save_path,
                title=f"Nagranie: {txt_filename}",
                silent=True
            )

    def _trigger_cloud_sync(self, plain_text: str, turns: list, audio_path: Optional[str] = None, title: Optional[str] = None, silent: bool = False):
        """Wysyła sesję do menedżera synchronizacji CloudSyncManager."""
        if not plain_text or not plain_text.strip():
            if not silent:
                QMessageBox.warning(self, "Brak Treści", "Brak tekstu transkrypcji do wysłania.")
            return

        # Rzeczywista długość nagrania: priorytet 1 to plik audio, priorytet 2 stoper, priorytet 3 ostatni segment
        duration = 0.0
        if audio_path and os.path.exists(audio_path):
            try:
                import soundfile as sf
                info = sf.info(audio_path)
                duration = float(info.duration)
            except Exception:
                pass

        if duration <= 0.0 and self.recorded_seconds > 0:
            duration = float(self.recorded_seconds)

        if duration <= 0.0 and turns:
            try:
                duration = max(float(t.get("end", 0.0)) for t in turns)
            except Exception:
                pass

        # Przekształć turns na segmenty dla API
        segments = []
        for t in (turns or []):
            segments.append({
                "speaker": t.get("speaker", "Mówca"),
                "start": t.get("start", 0.0),
                "end": t.get("end", 0.0),
                "text": t.get("text", "")
            })

        self.current_meeting_id = self.cloud_sync.sync_meeting_async(
            title=title or f"Spotkanie {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            transcript_text=plain_text,
            segments=segments,
            duration_seconds=duration,
            audio_path=audio_path,
            context_type="general",
            meeting_id=self.current_meeting_id
        )

        # Zapisz meeting_id do pliku sesji JSON, aby późniejsze operacje aktualizowały dokładnie ten sam rekord
        if getattr(self, "current_txt_path", None):
            try:
                json_path = get_session_path_for_txt(self.current_txt_path)
                sess = TranscriptionSession.load_from_json(json_path) or TranscriptionSession()
                sess.meeting_id = self.current_meeting_id
                sess.save_to_json(json_path)
            except Exception:
                pass

    def _on_manual_sync_clicked(self):
        """Ręczne wywołanie wysyłki z przycisku w UI."""
        if not self.last_plain_text:
            QMessageBox.warning(self, "Brak Transkrypcji", "Wykonaj nagranie lub wczytaj transkrypcję przed wysłaniem.")
            return

        self._trigger_cloud_sync(
            plain_text=self.last_plain_text,
            turns=self.current_turns,
            audio_path=self.last_audio_save_path,
            title=f"Ręczny Eksport: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            silent=False
        )

    def _cloud_target_name(self) -> str:
        target = str(self.cloud_sync.config.get("sync_target", "emanager") or "emanager")
        return "Supabase" if target.lower() == "supabase" else target.upper()

    def _on_sync_started(self, meeting_id: str):
        self._set_cloud_status(f"Synchronizacja z {self._cloud_target_name()} w toku", "warning")
        self.btn_manual_sync.setEnabled(False)

    def _on_sync_finished(self, meeting_id: str, success: bool, message: str):
        self.btn_manual_sync.setEnabled(True)
        if success:
            self._set_cloud_status(f"Zsynchronizowano z {self._cloud_target_name()}", "success")
            self.cloud_toast.dismiss()
            self._flash_icon(self.btn_manual_sync, "check", "cloud")
        else:
            self._set_cloud_status("Zapisano lokalnie (kolejka offline)", "warning")
            self._show_cloud_problem(
                f"Nie udało się wysłać do {self._cloud_target_name()}",
                "Transkrypcja czeka w kolejce i zostanie wysłana, gdy połączenie wróci."
            )

    def _on_offline_queued(self, meeting_id: str, message: str):
        self._set_cloud_status("Zapisano w kolejce offline", "warning")
        self.btn_manual_sync.setEnabled(True)
        self._show_cloud_problem(
            f"Nie udało się wysłać do {self._cloud_target_name()}",
            "Brak połączenia. Fragmenty czekają w kolejce i zostaną wysłane automatycznie."
        )

    def _on_live_session_started(self, meeting_id: str):
        target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
        self._set_cloud_status(f"🟢 Transmisja na żywo do {target_name} aktywna (ID: {meeting_id[:8]}...)", "success")

    def _on_live_block_synced(self, meeting_id: str, count: int):
        target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
        self._set_cloud_status(f"🟢 Transmisja do {target_name}: +{count} wypowiedzi na żywo", "success")

    def _on_live_session_finalized(self, meeting_id: str, success: bool, msg: str):
        target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
        if success:
            self._set_cloud_status(f"Zakończono sesję w {target_name}", "success")
        else:
            self._set_cloud_status("Sesja zapisana lokalnie (kolejka offline)", "warning")
            self._show_cloud_problem(
                f"Nie udało się zamknąć sesji w {self._cloud_target_name()}",
                "Sesja jest zapisana lokalnie i zostanie wysłana, gdy połączenie wróci."
            )

    def _on_session_split_triggered(self, reason: str):
        """
        Automatycznie zamyka bieżące spotkanie (upload audio do Storage, status='completed')
        i rozpoczyna nowe spotkanie w Supabase bez przerywania ciągłego nasłuchu mikrofonu.
        """
        print(f"[SMART SESSION] Podział sesji wywołany przez: {reason}")
        start_dt = getattr(self, "session_start_time", None)
        if is_one_record_per_day() and start_dt is not None and start_dt.date() == datetime.now().date():
            print("[SMART SESSION] Jedno nagranie na dzień: długa cisza nie zaczyna nowego nagrania.")
            return
        
        # 1. Zachowaj metadane zamykanej sesji
        old_meeting_id = self.current_meeting_id
        old_plain_text = self.last_plain_text
        old_recorded_sec = float(self.recorded_seconds)
        old_wav_path = getattr(self, "current_live_wav_path", None)
        old_turns = self.current_turns
        old_timestamp = getattr(self, 'current_live_timestamp', '')

        # 2. Generowanie nowych ścieżek dla kolejnego spotkania
        split_now = datetime.now()
        new_timestamp = split_now.strftime("%Y%m%d_%H%M%S_%f")
        self.session_start_time = split_now
        self.current_live_timestamp = new_timestamp
        self.current_live_wav_path = os.path.join(self.recordings_dir, f"inteligentne_nagranie_{new_timestamp}.wav")
        self.current_live_txt_path = os.path.join(self.transcriptions_dir, f"transkrypcja_{new_timestamp}.txt")
        try:
            with open(self.current_live_txt_path, 'w', encoding='utf-8') as f:
                f.write(f"=== NOWE SPOTKANIE BIUROWE (Start: {split_now.strftime('%Y-%m-%d %H:%M:%S')}) ===\n\n")
            self._refresh_transcriptions_list()
        except Exception:
            pass

        # 3. Rotacja rejestratora audio (flushez i zamyka stary WAV na dysku!) i transkrypcji w tle
        self.worker.rotate_session_file(self.current_live_wav_path)
        self._refresh_recordings_list()
        if hasattr(self, "rolling_worker") and self.rolling_worker is not None:
            self.rolling_worker.reset_for_new_session(self.current_live_txt_path, session_start_time=split_now)

        # 4. Finalizacja poprzedniej sesji w Supabase (gdy stary WAV jest już w 100% zamknięty na dysku)
        if old_meeting_id and old_plain_text:
            self.cloud_sync.finalize_live_session_async(
                meeting_id=old_meeting_id,
                final_transcript=old_plain_text,
                duration_seconds=old_recorded_sec,
                audio_path=old_wav_path,
                turns=old_turns,
                title=f"Spotkanie biurowe {old_timestamp}"
            )

        self.synced_segment_count = 0
        self._synced_turn_ids = set()
        self.current_turns = []
        self.last_plain_text = ""
        self.recorded_seconds = 0
        self._active_recorded_time = 0.0
        self._last_active_tick = None
        self.dock.set_time("00:00")
        from recorder.ui.widgets import polish_date_title
        self._set_doc_header(polish_date_title(split_now))
        self.text_transcript.setHtml("<p class='hint'>Rozpoczęto nowe spotkanie. Poprzednia sesja została zapisana automatycznie.</p>")

        # 5. Start nowej sesji w Supabase
        if self.cloud_sync.config.get("live_streaming") and self.cloud_sync.config.get("auto_sync"):
            self.current_meeting_id = self.cloud_sync.start_live_session_async(
                title=f"Spotkanie biurowe {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

        target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
        self._set_cloud_status(f"🟢 Nowa sesja spotkania w {target_name} ({reason})", "success")

    def _refresh_transcriptions_list(self):
        """Oznacza historię do odświeżenia (pliki .txt)."""
        self._mark_history_dirty()

    def _on_transcription_double_clicked(self, item):
        """Zgodność ze starszym API listy: otwiera transkrypcję wskazaną przez element listy."""
        self._open_transcript_file(item.data(Qt.ItemDataRole.UserRole))

    def _open_transcript_file(self, file_path):
        if self.is_recording():
            QMessageBox.information(self, "Trwa nagrywanie", "Zakończ nagrywanie, aby otworzyć inną transkrypcję.")
            return
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Odczytaj meeting_id z sesji JSON lub wylicz deterministyczny identyfikator
                json_path = get_session_path_for_txt(file_path)
                sess = TranscriptionSession.load_from_json(json_path) if os.path.exists(json_path) else None

                session_dt = None
                if sess and sess.created_at:
                    try:
                        session_dt = datetime.fromisoformat(sess.created_at)
                    except Exception:
                        pass
                if not session_dt:
                    session_dt = extract_datetime_from_filename(file_path)

                if sess and getattr(sess, "meeting_id", None):
                    self.current_meeting_id = sess.meeting_id
                elif sess and getattr(sess, "prepared_wav", None):
                    self.last_audio_save_path = sess.prepared_wav
                    stem = os.path.splitext(os.path.basename(sess.prepared_wav))[0].replace("inteligentne_nagranie_", "")
                    self.current_meeting_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"recorder67_{stem}"))
                else:
                    txt_stem = os.path.splitext(os.path.basename(file_path))[0].replace("transkrypcja_", "")
                    self.current_meeting_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"recorder67_{txt_stem}"))

                self.current_txt_path = file_path
                # 1. Preferuj oryginalne turns z pliku sesji JSON
                if sess and sess.turns:
                    self.current_turns = sess.turns
                    html_content = sess.export_to_html(session_start_time=session_dt)
                    self.text_transcript.setHtml(html_content)
                    self._scroll_transcript_view()
                else:
                    turns = parse_txt_to_turns(content, session_start_time=session_dt)
                    self.current_turns = turns or []
                    if turns:
                        html_content, _ = format_turns(turns, session_start_time=session_dt)
                        self.text_transcript.setHtml(html_content)
                        self._scroll_transcript_view()
                    else:
                        from html import escape
                        from recorder.config import get_preview_order
                        lines = [escape(l) for l in content.split("\n") if l.strip()]
                        if get_preview_order() == "newest_first":
                            lines = list(reversed(lines))
                        html_content = "<br><br>".join(lines)
                        self.text_transcript.setHtml(html_content)
                        self._scroll_transcript_view()

                if self.current_turns:
                    _, self.last_plain_text = format_turns(self.current_turns, session_start_time=session_dt, reverse_order=False)
                else:
                    self.last_plain_text = content

                from recorder.ui.widgets import polish_date_title
                title = polish_date_title(session_dt) if session_dt else os.path.basename(file_path)
                count = len(self.current_turns)
                self._set_doc_header(title, f"{count} wypowiedzi · {os.path.basename(file_path)}" if count else os.path.basename(file_path))
                self.btn_manual_sync.setEnabled(True)
                self.history_panel.close_panel()
                self._show_idle_view(show_text=True)
                self._set_cloud_status(f"Wczytano plik: {os.path.basename(file_path)}", "info")
            except Exception as e:
                QMessageBox.warning(self, "Błąd Odczytu", f"Nie udało się otworzyć pliku:\n{e}")

    def _on_open_txt_folder_clicked(self):
        if os.path.exists(self.transcriptions_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.transcriptions_dir))

    def _on_transcription_error(self, err_msg):
        self.progress_transcription.setValue(0)
        self.progress_transcription.setFormat("Błąd transkrypcji!")
        QMessageBox.critical(self, "Błąd AI", f"Wystąpił błąd podczas przetwarzania:\n{err_msg}")
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self._show_idle_view()

    def _on_timer_tick(self):
        # Precyzyjny czas nagrania (monotoniczny, bez dryfu i bez przeskakiwania sekund)
        is_active_recording = self.worker.state in [SmartRecordState.RECORDING_SPEECH, SmartRecordState.RECORDING_SILENCE_COUNTDOWN]
        now = time.monotonic()
        if is_active_recording:
            if getattr(self, "_last_active_tick", None) is not None:
                dt = now - self._last_active_tick
                if 0.0 < dt < 2.0:
                    self._active_recorded_time += dt
            self._last_active_tick = now
            self.recorded_seconds = int(self._active_recorded_time)

            self.dock.set_time(self._format_clock(self.recorded_seconds))

            if getattr(self, "rolling_worker", None) is not None:
                self.rolling_worker.update_session_time(self.recorded_seconds)
                proc_sec = self.rolling_worker.total_processed_seconds
                if proc_sec > 0:
                    disp_proc_sec = min(float(self.recorded_seconds), proc_sec)
                    pct = int(min(98, max(5, (disp_proc_sec / max(1.0, float(self.recorded_seconds))) * 100)))
                    p_min, p_sec = int(disp_proc_sec // 60), int(disp_proc_sec % 60)
                    t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
                    self.progress_transcription.setValue(pct)
                    blk_str = f" · blok #{self.last_processed_block_idx}" if self.last_processed_block_idx > 0 else ""
                    self.progress_transcription.setFormat(f"🟢 Przetworzono w tle: {p_min:02d}:{p_sec:02d} / {t_min:02d}:{t_sec:02d} ({pct}%{blk_str})")
                else:
                    # Informacja o aktywnym zbieraniu i buforowaniu mowy przed pierwszym blokiem
                    t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
                    if self.recorded_seconds < 12:
                        pct = int(min(80, max(5, (self.recorded_seconds / 12.0) * 80)))
                        self.progress_transcription.setValue(pct)
                        self.progress_transcription.setFormat(f"🎙️ Zbieranie mowy do pierwszego bloku: {t_min:02d}:{t_sec:02d}...")
                    else:
                        pct = min(92, 80 + int((self.recorded_seconds - 12) * 2))
                        self.progress_transcription.setValue(pct)
                        self.progress_transcription.setFormat(f"⚡ Przetwarzanie pierwszego fragmentu w tle: {t_min:02d}:{t_sec:02d}...")
        elif self.worker.state == SmartRecordState.AUTO_PAUSED:
            self._last_active_tick = None
            t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
            proc_sec = getattr(self.rolling_worker, "total_processed_seconds", 0.0) if getattr(self, "rolling_worker", None) else 0.0
            if proc_sec > 0:
                disp_proc_sec = min(float(self.recorded_seconds), proc_sec)
                p_min, p_sec = int(disp_proc_sec // 60), int(disp_proc_sec % 60)
                blk_str = f" · blok #{self.last_processed_block_idx}" if self.last_processed_block_idx > 0 else ""
                self.progress_transcription.setFormat(f"⏸️ Auto-Pauza (Cisza): {p_min:02d}:{p_sec:02d} / {t_min:02d}:{t_sec:02d}{blk_str}")
            else:
                self.progress_transcription.setFormat(f"⏸️ Auto-Pauza (Cisza): {t_min:02d}:{t_sec:02d}")
        elif self.worker.state == SmartRecordState.MANUAL_PAUSED:
            self._last_active_tick = None
            t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
            proc_sec = getattr(self.rolling_worker, "total_processed_seconds", 0.0) if getattr(self, "rolling_worker", None) else 0.0
            if proc_sec > 0:
                disp_proc_sec = min(float(self.recorded_seconds), proc_sec)
                p_min, p_sec = int(disp_proc_sec // 60), int(disp_proc_sec % 60)
                blk_str = f" · blok #{self.last_processed_block_idx}" if self.last_processed_block_idx > 0 else ""
                self.progress_transcription.setFormat(f"⏸️ Wstrzymano ręcznie: {p_min:02d}:{p_sec:02d} / {t_min:02d}:{t_sec:02d}{blk_str}")
            else:
                self.progress_transcription.setFormat(f"⏸️ Wstrzymano ręcznie: {t_min:02d}:{t_sec:02d}")

    def _update_audio_level(self, level):
        pass

    def _update_vad_info(self, is_speech, speech_prob, current_silence_sec):
        """Cisza odliczana do auto-pauzy wypełnia pierścień wokół kropki nagrywania."""
        state = self.worker.state
        if state in (SmartRecordState.MANUAL_PAUSED, SmartRecordState.AUTO_PAUSED, SmartRecordState.STOPPED):
            return
        self._last_is_speech = bool(is_speech)
        if is_speech and not self.typing_dots.is_active():
            self._set_typing(True)
        try:
            threshold = self._auto_pause_seconds()
            if current_silence_sec is None or current_silence_sec != current_silence_sec:
                current_silence_sec = 0.0
            frac = 0.0 if is_speech else max(0.0, min(1.0, float(current_silence_sec) / threshold))
            # Pierścień pokazujemy dopiero po chwili ciszy, żeby nie migał między słowami
            self.dock.set_silence_fraction(frac if frac >= 0.15 else 0.0)
        except Exception:
            self.dock.set_silence_fraction(0.0)

    def _on_worker_state_changed(self, state):
        if state == SmartRecordState.STOPPED:
            self._update_tray_tooltip("Gotowy")
            return
        if state in (SmartRecordState.RECORDING_SPEECH, SmartRecordState.RECORDING_SILENCE_COUNTDOWN):
            self.dock.set_mode("recording")
            self.dock.setToolTip("")
            self._update_tray_tooltip("Nagrywanie trwa")
        elif state == SmartRecordState.AUTO_PAUSED:
            self._set_typing(False)
            self.dock.set_mode("autopaused")
            self.dock.setToolTip(f"Auto-pauza: brak mowy dłużej niż {self._auto_pause_seconds():.0f} s")
            self._update_tray_tooltip("Wstrzymano (cisza)")
        elif state == SmartRecordState.MANUAL_PAUSED:
            self._set_typing(False)
            self.dock.set_mode("manualpaused")
            self.dock.setToolTip("Nagrywanie wstrzymane")
            self._update_tray_tooltip("Wstrzymano ręcznie")
            t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
            self.progress_transcription.setFormat(f"Wstrzymano: {t_min:02d}:{t_sec:02d}")

    def _setup_tray_icon(self):
        """Inicjalizuje ikonę zasobnika systemowego Windows dla dyskretnych powiadomień."""
        try:
            self.tray_icon = QSystemTrayIcon(self)
            from recorder.ui.windows_integration import get_app_icon_path
            ico_path = get_app_icon_path("ico")
            if ico_path and os.path.exists(ico_path):
                icon = QIcon(ico_path)
            else:
                icon = self.windowIcon()
            if icon.isNull():
                pix = QPixmap(32, 32)
                pix.fill(Qt.GlobalColor.transparent)
                p = QPainter(pix)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setBrush(QColor("#4361ee"))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(2, 2, 28, 28, 6, 6)
                p.setPen(QColor("#ffffff"))
                p.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
                p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "🎙")
                p.end()
                icon = QIcon(pix)
            self.tray_icon.setIcon(icon)
            self.tray_icon.setToolTip(f"{APP_NAME} — Gotowy")
            self.tray_icon.messageClicked.connect(self._on_tray_message_clicked)
            self.tray_icon.activated.connect(self._on_tray_icon_activated)

            # Menu podręczne pod prawym przyciskiem myszy
            tray_menu = QMenu(self)
            act_restore = tray_menu.addAction("🎙️ Otwórz okno")
            act_restore.triggered.connect(self._restore_from_tray)

            act_settings = tray_menu.addAction("⚙️ Ustawienia...")
            act_settings.triggered.connect(self._open_settings_dialog)

            tray_menu.addSeparator()
            act_quit = tray_menu.addAction("❌ Zakończ")
            act_quit.triggered.connect(self._on_tray_quit)

            self.tray_icon.setContextMenu(tray_menu)
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray_icon.show()
        except Exception as e:
            print(f"[Tray] Nie udało się zainicjalizować ikony zasobnika: {e}")
            self.tray_icon = None

    def _on_tray_quit(self) -> None:
        """Wymusza zamknięcie aplikacji z menu zasobnika systemowego."""
        self._force_quit = True
        self.close()

    def _on_tray_icon_activated(self, reason):
        """Obsługa kliknięcia ikony w zasobniku systemowym."""
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        """Przywraca i aktywuje okno główne z zasobnika."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _update_tray_tooltip(self, state_text: str = ""):
        """Aktualizuje opis ikony w zasobniku systemowym."""
        if getattr(self, "tray_icon", None) is not None:
            if state_text:
                self.tray_icon.setToolTip(f"EMANAGER Signal — {state_text}")
            else:
                self.tray_icon.setToolTip("EMANAGER Signal")

    def _on_tray_message_clicked(self):
        """Obsługuje kliknięcie w dymek powiadomienia (balloon) w zasobniku systemowym."""
        self._restore_from_tray()
        msg_type = getattr(self, "_last_tray_message_type", None)
        self._last_tray_message_type = None
        if msg_type == "silence_alert":
            source_mode = getattr(self, "_last_silence_source_mode", None) or get_record_source_mode()
            self._show_audio_inspection_dialog(source_mode)

    def _show_audio_inspection_dialog(self, source_mode: str):
        # 1. Przywrócenie okna głównego, aby użytkownik widział wskaźniki VU i urządzenia
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

        self._refresh_audio_devices()
        if source_mode == RecordSourceMode.SYSTEM_ONLY:
            msg_body = (
                "EMANAGER Signal odświeżył listę urządzeń audio i aplikacji w systemie Windows.\n\n"
                "Zalecane kroki sprawdzające:\n"
                "1. Upewnij się, że wybrany program (np. Discord) faktycznie odtwarza dźwięk.\n"
                "2. Sprawdź w Ustawienia → Nagrywanie, czy wybrano właściwą aplikację lub «Wszystkie programy».\n"
                "3. Upewnij się, że aplikacja nie została wyciszona w mikserze głośności Windows.\n"
                "4. W razie potrzeby zakończ nagranie przyciskiem ■ i rozpocznij nowe."
            )
        else:
            msg_body = (
                "EMANAGER Signal odświeżył listę urządzeń audio w systemie Windows.\n\n"
                "Zalecane kroki sprawdzające:\n"
                "1. Sprawdź fizyczny przycisk MUTE na mikrofonie lub nadajniku bezprzewodowym.\n"
                "2. Upewnij się, że w Ustawienia → Nagrywanie wybrano właściwy mikrofon.\n"
                "3. Jeśli mikrofon został odłączony lub zawieszony, zakończ nagranie przyciskiem ■ i rozpocznij nowe."
            )

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setWindowTitle("Weryfikacja Urządzeń Audio")
        msg_box.setText(msg_body)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)

        # Precyzyjne wyśrodkowanie okna komunikatu na środku ekranu monitora
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            hint = msg_box.sizeHint()
            x = max(0, geom.center().x() - (hint.width() // 2))
            y = max(0, geom.center().y() - (hint.height() // 2))
            msg_box.move(x, y)

        msg_box.exec()

    def _handle_silence_confirmed(self, mins_str: str):
        if hasattr(self, "worker"):
            self.worker.reset_silence_alert()
        self._set_cloud_status("✅ Nagrywanie trwa (aktywność potwierdzona).", "success")
        self._update_tray_tooltip("Nagrywanie trwa")

    def _handle_silence_inspect_requested(self, source_mode: str):
        if hasattr(self, "worker"):
            self.worker.reset_silence_alert()
        self._update_tray_tooltip("Nagrywanie trwa")
        self._show_audio_inspection_dialog(source_mode)

    def show_silence_alert_preview(self, silence_sec: float):
        """Wyświetla próbkę powiadomienia na żądanie z okna ustawień."""
        src_mode = get_record_source_mode()
        self._on_silence_alert(silence_sec, src_mode)

    def _on_silence_alert(self, silence_sec: float, source_mode: str):
        """Obsługuje sygnał strażnika ciszy z wątku SmartAudioWorker lub testu ustawień."""
        mins = int(silence_sec // 60)
        mins_str = f"{mins} min" if mins > 0 else f"{int(silence_sec)} s"

        if getattr(self, "_active_silence_toast", None) is not None:
            try:
                self._active_silence_toast.close()
            except Exception:
                pass

        if hasattr(self, "worker") and self.worker is not None:
            self.worker.suppress_sys_audio_for(0.8)

        toast = SilenceToastBanner(self, silence_sec=silence_sec, source_mode=source_mode)
        self._active_silence_toast = toast
        toast.confirmed.connect(lambda: self._handle_silence_confirmed(mins_str))
        toast.inspect_requested.connect(lambda: self._handle_silence_inspect_requested(source_mode))
        toast.dismissed.connect(lambda: self._handle_silence_confirmed(mins_str))
        toast.timed_out.connect(lambda: self._handle_silence_timed_out_to_tray(mins_str, source_mode))
        toast.show()

    def _handle_silence_timed_out_to_tray(self, mins_str: str, source_mode: str):
        """Gdy nikt nie kliknął toasta przez 45s (nieobecność), przekazuje powiadomienie do Centrum Akcji Windows."""
        if hasattr(self, "worker"):
            self.worker.reset_silence_alert()
        title = f"⚠️ Brak dźwięku od {mins_str}"
        msg = f"EMANAGER Signal rejestruje czas, ale nie wykryto mowy ani dźwięku.\nKliknij tutaj, aby sprawdzić stan urządzeń."
        self._last_tray_message_type = "silence_alert"
        self._last_silence_source_mode = source_mode
        if getattr(self, "tray_icon", None) is not None:
            self._update_tray_tooltip(f"Brak dźwięku ({mins_str})")
            self.tray_icon.showMessage(
                title,
                msg,
                QSystemTrayIcon.MessageIcon.Warning,
                15000
            )
        self._set_cloud_status(f"⚠️ Brak dźwięku od {mins_str}.", "warning")

    def _handle_audio_error(self, err_msg):
        self._on_stop_clicked()
        QMessageBox.critical(
            self,
            "Urządzenie Audio",
            f"{err_msg}\n\n"
            "Upewnij się, że mikrofon jest podłączony w Windows i sprawny."
        )

    def _on_recording_double_clicked(self, item):
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path and os.path.exists(file_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))

    def _on_open_folder_clicked(self):
        if os.path.exists(self.recordings_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.recordings_dir))

    def closeEvent(self, event):
        # 1. Zapis geometrii okna (zawsze przed schowaniem lub zamknięciem)
        try:
            geom_hex = self.saveGeometry().toHex().data().decode("ascii")
            set_window_geometry(geom_hex)
        except Exception as e:
            import logging
            logging.getLogger("recorder").warning(f"Błąd zapisu geometrii okna: {e}")

        # 2. Ochrona minimalizacji do zasobnika systemowego (Minimize to Tray on Close)
        force_quit = getattr(self, "_force_quit", False) or getattr(self, "_force_close", False)
        tray_available = QSystemTrayIcon.isSystemTrayAvailable() if hasattr(QSystemTrayIcon, "isSystemTrayAvailable") else False
        tray_present = getattr(self, "tray_icon", None) is not None

        if is_minimize_to_tray_on_close() and not force_quit and tray_available and tray_present:
            event.ignore()
            self.hide()
            self._last_tray_message_type = "minimized"
            try:
                if self.tray_icon.isVisible():
                    self.tray_icon.showMessage(
                        "EMANAGER Signal",
                        "Aplikacja została zminimalizowana do zasobnika systemowego i nadal działa w tle.",
                        QSystemTrayIcon.MessageIcon.Information,
                        3000
                    )
            except Exception:
                pass
            return

        # 3. Standardowe, pełne zamykanie aplikacji
        try:
            self.timer.stop()

            # 1. Zatrzymanie wszystkich zarejestrowanych wątków w self._active_threads
            for th in list(self._active_threads):
                try:
                    th.blockSignals(True)
                    if hasattr(th, "stop"):
                        th.stop()
                    if hasattr(th, "quit"):
                        th.quit()
                    th.wait(2000)
                except Exception:
                    pass
            self._active_threads.clear()

            # 2. Zablokowanie sygnałów i zatrzymanie wątku audio oraz zapis audio
            if self.worker is not None:
                try:
                    self.worker.blockSignals(True)
                except Exception:
                    pass
                if self.worker.isRunning() or self.worker.state != SmartRecordState.STOPPED:
                    timestamp = getattr(self, "current_live_timestamp", datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
                    save_path = getattr(self, "current_live_wav_path", None) or os.path.join(self.recordings_dir, f"inteligentne_nagranie_{timestamp}.wav")
                    self.worker.stop_recording()
                    self.worker.wait(3000)
                    try:
                        self.worker.save_wav(save_path)
                    except Exception as e:
                        import logging
                        logging.getLogger("recorder").error(f"Błąd zapisu pliku audio WAV podczas zamykania aplikacji ('{save_path}'): {e}")
                elif self.worker.isRunning():
                    self.worker.wait(3000)

            # 3. Pozostałe wątki pomocnicze
            if getattr(self, "live_transcription_worker", None) is not None:
                try:
                    self.live_transcription_worker.blockSignals(True)
                except Exception:
                    pass
                if self.live_transcription_worker.isRunning():
                    self.live_transcription_worker.stop()
                    self.live_transcription_worker.wait(1500)

            if getattr(self, "transcription_thread", None) is not None:
                try:
                    self.transcription_thread.blockSignals(True)
                except Exception:
                    pass
                if self.transcription_thread.isRunning():
                    self.transcription_thread.quit()
                    self.transcription_thread.wait(1500)

            if getattr(self, "file_processing_worker", None) is not None:
                try:
                    self.file_processing_worker.blockSignals(True)
                except Exception:
                    pass
                if self.file_processing_worker.isRunning():
                    self.file_processing_worker.quit()
                    self.file_processing_worker.wait(1500)

            if getattr(self, "_startup_update_worker", None) is not None:
                try:
                    self._startup_update_worker.blockSignals(True)
                    if self._startup_update_worker.isRunning():
                        self._startup_update_worker.quit()
                        self._startup_update_worker.wait(1000)
                except Exception:
                    pass

            if getattr(self, "_active_silence_toast", None) is not None:
                try:
                    self._active_silence_toast.close()
                except Exception:
                    pass

            if getattr(self, "tray_icon", None) is not None:
                try:
                    self.tray_icon.hide()
                    self.tray_icon.deleteLater()
                except Exception:
                    pass

            # 4. Sprawdzenie oczekującej aktualizacji przy wyjściu (Install on exit)
            pending_zip = getattr(self, "_pending_update_zip_path", None)
            if pending_zip and os.path.exists(pending_zip):
                is_frozen = getattr(sys, "frozen", False)
                if is_frozen:
                    from recorder.core.updater import apply_in_place_update
                    print(f"[UPDATER] Uruchamianie instalacji w tle przy wyjściu: {pending_zip}")
                    apply_in_place_update(pending_zip, restart_after=False)

        except Exception as e:
            print(f"[closeEvent] Błąd zamykania okna: {e}")
        finally:
            event.accept()
