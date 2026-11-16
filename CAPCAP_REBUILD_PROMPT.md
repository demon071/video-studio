# YÊU CẦU XÂY DỰNG ỨNG DỤNG MỚI: LÕI TÍNH NĂNG CAPCAP (v9.0.1) + GIAO DIỆN NLE STUDIO HIỆN ĐẠI (PHONG CÁCH CAPCUT DESKTOP)

## 1. BỐI CẢNH & MỤC TIÊU DỰ ÁN
Xây dựng một ứng dụng Desktop biên tập, tách phụ đề, dịch thuật AI và lồng tiếng video tự động **mới hoàn toàn từ con số 0 (from scratch)** theo hai định hướng rõ ràng:

1. **Lõi tính năng, thuật toán & quy trình xử lý (Backend & AI Pipeline)**:
   - Lấy **`CapCap` (v9.0.1)** làm **nguồn chuẩn duy nhất (Single Source of Truth)**.
   - Chuyển đổi đầy đủ 100% tính năng, công thức tính toán, bộ lọc làm sạch phụ đề, phân vai người nói, dịch thuật ngữ cảnh, lồng tiếng đa nhân vật, giãn hình khớp giọng (Fit Voice / Time-Warp), trộn âm thanh 3 kênh, hàng đợi xử lý hàng loạt và công cụ FFmpeg từ `CapCap`.
2. **Giao diện & Trải nghiệm người dùng (Frontend UI/UX)**:
   - **KHÔNG sử dụng hay bắt chước bố cục giao diện cũ của `CapCap`**.
   - **Thiết kế mới hoàn toàn theo chuẩn trình dựng phim chuyên nghiệp (Modern NLE Video Studio)** tương tự **CapCut Desktop, DaVinci Resolve, Adobe Premiere Pro**: bố cục 4 vùng có thể kéo giãn kích thước (Dockable/Resizable Panes), thanh công cụ bên trái theo ngữ cảnh (Icon Rail + Drawer), màn hình Canvas tương tác trực quan ở giữa, bảng thuộc tính thông minh bên phải (Context-Aware Inspector) và Timeline đa tầng trải rộng toàn màn hình phía dưới.

---

## 2. KIẾN TRÚC CÔNG NGHỆ (TECH STACK)

### 2.1. Backend (`backend/`)
- **Ngôn ngữ & Framework**: Python 3.11+, **FastAPI**, **Uvicorn**, **Pydantic v2**.
- **Giao tiếp thời gian thực**:
  - **REST API**: Quản lý dự án, cấu hình, thực thi từng bước hoặc toàn bộ pipeline, truy vấn danh sách model/giọng đọc, điều khiển hàng đợi.
  - **WebSocket (`/ws/events`)**: Truyền phát trực tiếp (real-time streaming) phần trăm tiến độ (`progress`), bước xử lý hiện tại (`stage`), từng câu phụ đề vừa nhận dạng/dịch xong, dữ liệu dạng sóng âm thanh (`waveform`), trạng thái hàng đợi (`queue_state`) và nhật ký hệ thống (`logs`).
- **Tăng tốc phần cứng (Hardware & GPU Acceleration)**:
  - Tự động phát hiện và đăng ký đường dẫn DLL cho cả **CUDA 12** (`nvidia/cublas/bin`, `nvidia/cudnn/bin` phục vụ `ctranslate2` / `faster-whisper`) và **CUDA 13** (`torch/lib` phục vụ `onnxruntime-gpu` / `RapidOCR`).
  - Hỗ trợ mã hóa video bằng phần cứng **NVIDIA NVENC (`h264_nvenc`)** và tự động lùi về `libx264` khi chạy trên CPU.

### 2.2. Frontend & Desktop Shell (`frontend/`)
- **Giao diện người dùng**: **React 19 + TypeScript + Vite + Tailwind CSS + Lucide Icons**.
- **Quản lý trạng thái**: **Zustand** (lưu trữ trạng thái dự án, các track trên Timeline, danh sách phụ đề TS1/TS2, các lớp phủ hình ảnh, đối tượng đang chọn trên Inspector, lịch sử Undo/Redo `Ctrl+Z`/`Ctrl+Y`).
- **Desktop Runtime**: **Tauri v2 (`src-tauri/`)**:
  - Khởi chạy và giám sát tiến trình Python FastAPI dưới dạng Sidecar hoặc Local Daemon.
  - Cung cấp hộp thoại chọn file/thư mục gốc của hệ điều hành, kéo-thả file trực tiếp từ ngoài vào Media Bin/Timeline và phát trực tiếp media cục bộ qua giao thức `asset://`.

---

## 3. ĐẶC TẢ CHI TIẾT CÁC PHÂN HỆ LÕI (THAM CHIẾU 1:1 TỪ `CapCap`)

### 3.1. Phân hệ Nhận dạng Phụ đề (ASR), Làm sạch & Phân vai (Speaker Diarization)
**File tham chiếu gốc trong `CapCap`**:
- `CapCap/app/whisper_processor.py`
- `CapCap/app/sensevoice_processor.py`
- `CapCap/app/capcut_stt_processor.py`
- `CapCap/app/services/asr_options.py`
- `CapCap/app/services/asr_merge_service.py`
- `CapCap/app/services/speaker_diarization_service.py`
- `CapCap/app/capcut/transport.py` & `CapCap/app/capcut/pc_worker.py`

**Yêu cầu triển khai**:
1. **Faster-Whisper**:
   - Hỗ trợ các model `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo` thông qua `BatchedInferencePipeline` (`batch_size=8`), kiểu tính toán `int8_float16` trên GPU (hoặc `int8` trên CPU).
   - **Lọc ảo giác (Hallucination Filter)**: Tự động loại bỏ các đoạn lặp từ bất thường, các câu xuất hiện trong khoảng lặng dài hơn ngưỡng `hallucination_silence_threshold` (mặc định `1.2s`), và các mẫu câu rác của Whisper.
   - **Gộp câu trùng lặp ở biên đoạn (`AsrMergeService`)**: Khi `dedup_enabled=True` (ngưỡng `dedup_similarity=0.82`), tự động gộp các câu liền kề bị lặp chữ hoặc câu ngắn nằm trọn trong câu dài có nghĩa (`_is_informative_containment`: từ 10 ký tự chữ/số và 3 từ trở lên, hoặc từ 8 chữ Hán trở lên).
2. **SenseVoice (Sherpa-ONNX)**:
   - Chuyển đổi nguyên bản thuật toán `_smart_split_sensevoice_tokens`: tách câu theo dấu câu kết thúc (`.?!。？！`), dấu phẩy, khoảng nghỉ ngắn/dài (`0.4s` / `0.8s`), giới hạn chiều dài câu CJK (`26` ký tự) và Latin (`65` ký tự), đồng thời gộp các từ cảm thán 1 âm tiết (`啊`, `哦`, `呢`, `吧`...) vào câu liền kề.
3. **CapCut STT (Web API & CapCut PC `sscronet.dll`)**:
   - Hỗ trợ nhận dạng giọng nói qua đám mây CapCut và qua **CapCut PC Transport** (chạy tiến trình worker cô lập nạp `sscronet.dll` qua C ABI để gửi request mạng chuẩn CapCut Desktop).
4. **Phân biệt người nói (Speaker Diarization)**:
   - Tích hợp `pyannote-segmentation-3.0` + `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` qua `sherpa-onnx`.
   - Tự động gán mã người nói (`SPEAKER_00`, `SPEAKER_01`...) và màu sắc nhận diện (`SPEAKER_PALETTE`) cho từng dòng phụ đề.
5. **Tách giọng trước khi nhận dạng (Vocal Isolation Pre-pass)**:
   - Tùy chọn tách riêng giọng nói khỏi nhạc nền ồn ào trước khi đưa vào Whisper/SenseVoice để tăng độ chính xác.
6. **Quét lại theo Khoảng chọn (Range Re-Transcribe) & Nhập file SRT**:
   - Cho phép chọn một khoảng thời gian `[start_time, end_time]` trên Timeline và chạy lại riêng khoảng đó bằng engine đối lập (**Alt: OCR** hoặc **Alt: Whisper**), tự động ghép kết quả mới vào đúng vị trí trong danh sách phụ đề.
   - Hỗ trợ nhập trực tiếp file `.srt` vào Track Phụ đề chính (TS1) hoặc Track Phụ đề phụ (TS2).

---

### 3.2. Phân hệ Tách Phụ đề Cứng (OCR) & Dịch Vùng Ảnh Trực Tiếp (OCR Translator)
**File tham chiếu gốc trong `CapCap`**:
- `CapCap/app/ocr_processor.py`

**Yêu cầu triển khai**:
1. **Streaming Khung hình & Tiền xử lý Ảnh**:
   - Đọc khung hình trực tiếp từ video qua FFmpeg rawvideo pipe với hàng đợi trong RAM để đạt tốc độ tối đa.
   - Áp dụng bộ lọc mặt nạ chữ trắng/vàng viền tối (HSV mask + binary outline mask), làm tối nền `_dim_background_for_ocr`, phát hiện khung hình rỗng `_is_blank_region`.
   - So sánh độ sai khác mặt nạ chữ giữa 2 khung hình liên tiếp: nếu chữ trên hình chưa thay đổi thì tái sử dụng ngay kết quả frame trước, giảm 60–80% số lần gọi mô hình OCR.
2. **Làm sạch Văn bản OCR & Khôi phục Bộ thủ Chữ Hán**:
   - Chuyển đổi đầy đủ `_sanitize_ocr_line`: tự động xóa `@handle`, xóa watermark (`漫剧`, `专店`, `抖音号`...), và bảng ghép lại bộ thủ chữ Hán bị OCR tách rời (`_OCR_RADICAL_REPLACEMENTS`: `亻尔` $\rightarrow$ `你`, `讠兑` $\rightarrow$ `说`, `亻门` $\rightarrow$ `们`, `彳亍` $\rightarrow$ `行`...).
   - Sử dụng `_ReusableDetectorPreProcess` tái sử dụng vùng nhớ đệm `float32`/`float64` cho bộ dò chữ RapidOCR.
3. **Hỗ trợ Đa Engine OCR**:
   - **RapidOCR (ONNX Runtime GPU/CPU)**, **Windows Media OCR (`winocr`)** (kèm API liệt kê các gói ngôn ngữ OCR đã cài trên Windows), **PaddleOCR**, **EasyOCR**.
4. **Chế độ Bù lỗ hổng Âm thanh bằng OCR (OCR Hole-Filling)**:
   - Sau khi chạy ASR âm thanh, tự động tìm các khoảng thời gian có tiếng động nhưng ASR bỏ sót chữ để quét bổ sung bằng OCR.
5. **Công cụ Quét & Dịch Vùng Ảnh Bất Kỳ (`OCR Region Translator`)**:
   - Cho phép khoanh một vùng bất kỳ trên khung hình video (bảng hiệu, tin nhắn, chú thích), bấm 1 nút để OCR đọc chữ, dịch sang ngôn ngữ đích và tự động tạo một clip `Text Layer` trên Timeline che đè lên vùng chữ gốc.

---

### 3.3. Phân hệ Dịch Thuật AI Ngữ Cảnh, Duyệt Xưng Hô & Phụ Đề Song Ngữ (TS1 / TS2)
**File tham chiếu gốc trong `CapCap`**:
- `CapCap/app/translator.py`
- `CapCap/app/translation/context_analyzer.py`
- `CapCap/app/translation/presets.py`
- `CapCap/app/translation/prompts/*` (Sao chép nguyên vẹn toàn bộ thư mục `prompts/` và `presets/*.md`)
- `CapCap/app/services/browser_ai_bridge.py`

**Yêu cầu triển khai**:
1. **Đa Nhà cung cấp Dịch thuật**:
   - **Offline Local LLM (`llama-cpp-python` GGUF)**, **Gemini API**, **DeepSeek API**, **OpenAI API**, **Ollama (Local Server)**, **Google Translate (miễn phí)**, **Bing Translate (miễn phí)** (tự động chuyển sang Bing khi Google lỗi), và **Direct Browser AI Bridge** (điều khiển phiên dịch qua trình duyệt).
2. **Kho Preset Thể loại Phim (Genre Prompt Presets)**:
   - Tích hợp toàn bộ các preset thể loại từ `translation_prompt_presets.json` và `prompts/presets/*.md`: *Cổ trang / Tiên hiệp / Kiếm hiệp*, *Ngôn tình / Đô thị hiện đại*, *Phim ngắn kịch tính (Short Drama)*, *Anime học đường / Đời thường*, *Anime Isekai / Fantasy*, *Manhwa / Hunter Hàn Quốc*, *K-Drama hiện đại*, *Vlog / Mạng xã hội*, *Phim tài liệu / Kinh doanh*...
3. **Phân tích Ngữ cảnh & Duyệt Xưng Hô 2 Chiều (`ContextAnalyzer` & `RollingContextLedger`)**:
   - **Phân tích trước khi dịch (`learn_dialogue_context`)**: Quét toàn bộ kịch bản để lập danh sách Nhân vật (`id`, `name`, `gender`, `role`, `self_pronoun`, `address_others`), Quy tắc xưng hô (`pronoun_rules`), Bảng thuật ngữ/tên riêng (`term_glossary`) và Tóm tắt bối cảnh.
   - **Hộp thoại Duyệt & Chỉnh sửa Xưng hô (`PronounReviewDialog`)**: Cho phép người dùng xem, sửa trực tiếp đại từ xưng hô của từng cặp nhân vật, hoặc nhập yêu cầu để AI phân tích lại (`reanalyze_with_feedback`).
   - **Sổ cái Ngữ cảnh Liên tục (`RollingContextLedger`)**: Tự động cập nhật (`update_ledger_from_batch`) và tiêm hướng dẫn ngữ cảnh (`build_rolling_context_guidance`) qua từng lô (batch) dịch để đảm bảo từ đầu đến cuối video không bị loạn xưng hô.
4. **Hệ thống Phụ đề Song ngữ Độc lập (TS1 & TS2)**:
   - Hỗ trợ 3 chế độ xuất/hiển thị phụ đề (`presentation_mode`):
     - `Không phụ đề` (`none`)
     - `Có phụ đề - Đơn ngữ` (`single`: chỉ hiển thị Phụ đề chính TS1)
     - `Song ngữ` (`bilingual`: hiển thị đồng thời Phụ đề chính TS1 và Phụ đề phụ TS2)
   - TS1 và TS2 hoạt động như 2 Text Track độc lập trên Timeline, có kiểu dáng (`SubtitleStyle`) và vị trí riêng biệt.

---

### 3.4. Phân hệ Lồng Tiếng Đa Nhân Vật (TTS), Cắt Khoảng Lặng & Fit Voice (Time-Warp)
**File tham chiếu gốc trong `CapCap`**:
- `CapCap/app/tts_processor.py`
- `CapCap/app/engines/elevenlabs_tts.py`
- `CapCap/app/engines/vieneu_tts.py`
- `CapCap/app/services/tts_audio_policy.py`
- `CapCap/app/services/time_warp_plan.py`
- `CapCap/assets/capcut/Voice.json`

**Yêu cầu triển khai**:
1. **Đa Engine Giọng đọc (TTS)**:
   - **CapCut TTS** (nạp toàn bộ danh sách giọng phân loại theo ngôn ngữ/giới tính từ `Voice.json`, hỗ trợ cả Web API và CapCut PC transport).
   - **ElevenLabs TTS** (lấy danh sách giọng, kiểm tra hạn mức ký tự/tier tài khoản).
   - **Edge TTS** (miễn phí, đa ngôn ngữ).
   - **Piper TTS** & **VieNeu TTS** (tổng hợp giọng đọc tiếng Việt offline tốc độ cao).
   - **OmniVoice** & **VoiceStudio Local Server**.
2. **Phân vai Giọng đọc Đa Nhân vật (Multi-Speaker Voice Assignment)**:
   - Cho phép gán giọng đọc riêng cho từng nhãn `SPEAKER_XX`, cũng như ghi đè giọng đọc (`voice`) và tốc độ đọc (`speed`) trên từng clip phụ đề riêng lẻ.
   - Hỗ trợ nghe thử giọng (`Preview Voice`), nghe lại từng câu đã lồng tiếng và tạo lại TTS cho đúng 1 câu vừa sửa chữ mà không cần chạy lại toàn bộ video.
3. **Chính sách Cắt Khoảng Lặng Đầu/Cuối (`tts_audio_policy.py`)**:
   - Tự động cắt bỏ khoảng lặng thừa ở đầu và cuối mỗi đoạn âm thanh TTS (`BOUNDARY_SILENCE_DB = -60.0`, `BOUNDARY_MIN_SILENCE_SECONDS = 0.35`, giữ đệm tự nhiên `BOUNDARY_PADDING_SECONDS = 0.12`).
4. **Thuật toán Fit Voice / Time-Warp (`time_warp_plan.py`)**:
   - Khi thời lượng câu thoại TTS dài hơn khoảng thời gian `[start, end]` của phụ đề:
     - Hỗ trợ tự động viết gọn câu thoại bằng AI (Timing Rewrite) hoặc tăng tốc độ đọc trong ngưỡng cho phép (`atempo`).
     - Hỗ trợ chế độ **Fit Voice (Time-Warp)**: Tự động lập kế hoạch giãn thời gian khung hình video tại đoạn thoại đó (`WarpSpan` với chế độ `slow` làm chậm chuyển động video hoặc `freeze` dừng hình cuối câu) và tự động ánh xạ lại toàn bộ mốc thời gian (`media_to_timeline`, `timeline_to_media`) cho Phụ đề TS1, Phụ đề TS2 cùng mọi lớp Visual Layers để tiếng và hình khớp nhau 100%.

---

### 3.5. Phân hệ Biên tập Lớp Phủ (Visual Layers), Trộn Âm Thanh 3 Kênh & Xuất Bản
**File tham chiếu gốc trong `CapCap`**:
- `CapCap/app/video_processor.py`
- `CapCap/app/video_filter_chain.py`
- `CapCap/app/audio_mixer.py`
- `CapCap/app/subtitle_renderer.py`
- `CapCap/app/capcut_exporter.py`

**Yêu cầu triển khai**:
1. **Hệ thống Lớp Phủ Hình ảnh (Visual Layers — `video_filter_chain.py`)**:
   - Hỗ trợ thêm nhiều lớp phủ lên video với tọa độ tương đối `(x, y, w, h)` và khoảng thời gian hiển thị `[start, end]`:
     - **Blur Layer (`Làm mờ`)**: Làm mờ vùng phụ đề cứng hoặc logo có sẵn trên video gốc (hỗ trợ độ mạnh làm mờ & làm mềm viền feather).
     - **Mask Layer (`Khung che`)**: Vẽ hộp màu (độ mờ & màu sắc tùy chỉnh) che phụ đề gốc.
     - **Logo / Image Layer (`Chèn ảnh/Logo`)**: Chèn watermark hoặc ảnh PNG trong suốt lên video.
     - **Text Layer (`Chữ nổi`)**: Chèn tiêu đề, ghi chú hoặc bản dịch biển hiệu cố định lên khung hình.
   - Hỗ trợ **Lật ngang video (`Horizontal Flip / hflip`)** và chuyển đổi khung hình (`9:16`, `16:9`, `1:1`) kèm tùy chọn nền mờ (blurred background pad) hoặc cắt tràn khung (center crop).
2. **Kết xuất Phụ đề ASS Đa Phong Cách & Highlight Từ Khóa (`subtitle_renderer.py`)**:
   - Hỗ trợ đầy đủ kiểu chữ: Font, Cỡ chữ, Màu chính, Màu viền, Độ dày viền, Độ bóng đổ, Hộp nền (Background Box + Opacity), Căn lề 9 vị trí, Lề dọc/ngang.
   - Hỗ trợ **Highlight từ khóa (Keyword Highlight)**: Tự động đổi màu hoặc phóng to các từ khóa quan trọng trong câu thoại.
   - Hỗ trợ lưu, đổi tên và xóa các mẫu kiểu chữ cá nhân (**My Presets**).
3. **Bộ Trộn Âm Thanh 3 Kênh (`audio_mixer.py`)**:
   - Kết hợp 3 luồng âm thanh với thanh chỉnh âm lượng độc lập và bộ nén âm lượng tự động (Sidechain Ducking):
     1. **Âm thanh gốc / Nhạc nền tách từ video (Original / Instrumental Stem)**.
     2. **Nhạc nền BGM gắn thêm từ file ngoài (Custom BGM)**.
     3. **Giọng đọc lồng tiếng (Dubbing Voice Track)**.
4. **Hệ thống Xuất Bản Đa Năng**:
   - **Render Video MP4 (NVENC / x264)**: Chọn mức chất lượng `Cao (High)`, `Cân bằng (Balanced)`, `Nhẹ/Nhanh (Low)`.
   - **Xem thử 5 giây (`Fast Preview 5s`)**: Render tức thì 5 giây video tại vị trí con trỏ Playhead hiện tại với đầy đủ phụ đề, lớp làm mờ và giọng lồng tiếng.
   - **Xuất Âm Thanh Độc Lập**: Xuất riêng file `.wav`/`.mp3` cho *Giọng đọc (Voice Only)*, *Nhạc nền (BGM Only)*, hoặc *Bản trộn (Mixed Audio)*.
   - **Xuất Tài Nguyên Theo Nhóm (Resource Export)**: Xuất trọn bộ file `.srt`, `.ass`, từng câu thoại TTS rời và nhạc nền ra thư mục.
   - **Xuất Dự Án CapCut Desktop (`capcut_exporter.py`)**: Tạo dự án trực tiếp trong thư mục Drafts của CapCut PC gồm các track riêng biệt (Video gốc/Time-Warp, Phụ đề 1, Phụ đề 2 song ngữ, các câu lồng tiếng TTS rời, nhạc nền, lớp phủ) để người dùng mở CapCut chỉnh sửa tiếp ngay lập tức.

---

### 3.6. Phân hệ Hàng Đợi Xử Lý Hàng Loạt (Performance Queue), Gói Dự Án & Công Cụ Media
**File tham chiếu gốc trong `CapCap`**:
- `CapCap/app/services/processing_queue.py`
- `CapCap/app/services/project_service.py`
- `CapCap/app/services/project_package_service.py`
- `CapCap/app/ui/dialogs.py`

**Yêu cầu triển khai**:
1. **Chế độ Hiệu Năng — Hàng Đợi Nhiều Video (`ProcessingQueueManager`)**:
   - Lưu trữ bền vững trong `processing_queue.json`, tự động khôi phục danh sách khi mở lại ứng dụng.
   - Cho phép thêm hàng loạt video vào hàng đợi, theo dõi thanh tiến độ & bước xử lý của từng video.
   - Hỗ trợ 3 chế độ vận hành: **Chỉ xử lý (`process_only`)**, **Xử lý & Xuất luôn (`process_and_export`)**, và **Xuất các video đã chọn (`export_selected`)**.
   - Hỗ trợ **Dừng sau video hiện tại**, **Dừng khẩn cấp**, và **Dọn dữ liệu tạm của video (`Clean Video Data`)**.
2. **Đóng gói & Di chuyển Dự án (`ProjectPackageService`)**:
   - Lưu/Mở dự án đang làm dở và đóng gói thành tệp nén di động bao gồm toàn bộ tài nguyên liên quan.
3. **Công cụ Media FFmpeg 4-trong-1 (`MediaToolsModal`)**:
   - **Tab 1 — Cắt Video (Trim)**, **Tab 2 — Ghép Video (Concat)**, **Tab 3 — Tách Âm thanh / Hình ảnh (Demux)**, **Tab 4 — Ghép Nhạc vào Video (Mux)**.

---

## 4. THIẾT KẾ GIAO DIỆN STUDIO HIỆN ĐẠI (PHONG CÁCH CAPCUT DESKTOP / DAVINCI RESOLVE)

> **Lưu ý quan trọng**: Không tham chiếu bố cục giao diện `CustomTkinter` của `CapCap`. Giao diện mới phải được thiết kế chuẩn **Modern NLE Video Editor** (như **CapCut Desktop**), sử dụng hệ màu Dark Studio (`#0E1015` nền chính, `#161922` bề mặt panel, `#1E2230` thẻ nổi, điểm nhấn màu Cyan/Teal `#00E5FF` kiểu CapCut), có vách ngăn kéo giãn kích thước (`Resizable Split Panes`) giữa các khu vực.

### Sơ đồ Bố cục 5 Khu vực Chuẩn CapCut Desktop:

```text
+--------------------------------------------------------------------------------------------------+
| [Logo] [Dự án v] [Công cụ Media] [Hàng đợi Batch (3)]        [Tên Project] [GPU: CUDA] [XUẤT BẢN]|
+-------+------------------------------+-----------------------------------+-----------------------+
| ICON  |   LEFT DRAWER (TÀI NGUYÊN    |      CENTER PLAYER & CANVAS       |   RIGHT INSPECTOR     |
| RAIL  |     & BẢNG CÔNG CỤ AI)       |                                   |  (THUỘC TÍNH & PHỤ ĐỀ)|
|       |                              |   +---------------------------+   |                       |
| [Media|  (Nội dung thay đổi theo     |   |                           |   | [Phụ đề] [Kiểu chữ]   |
| [Sub] |   Icon được chọn bên trái:   |   |     VIDEO PREVIEW         |   | [Video]  [Âm thanh]   |
| [Dịch]|   - Kho Media & Nhạc nền     |   |   + Khung kéo/thả Layer   |   |                       |
| [TTS] |   - Tạo phụ đề (ASR / OCR)   |   |   + Phụ đề TS1 / TS2      |   | - Danh sách câu TS1/2 |
| [Chữ] |   - Dịch AI & Duyệt xưng hô  |   |   + 8 điểm neo Resize     |   | - Chỉnh Font/Màu/Viền |
| [Lớp] |   - Lồng tiếng & Phân vai    |   +---------------------------+   | - Gán giọng Speaker   |
| [Audio|   - Thư viện Lớp phủ & Mẫu)  |   [00:12.4 / 01:30.0] [|<][>][>|] | - Trộn 3 kênh Audio   |
|       |                              |   [9:16 v] [Xem thử 5s] [Zoom v]  |                       |
+-------+------------------------------+-----------------------------------+-----------------------+
| TIMELINE TOOLBAR: [Con trỏ V] [Cắt B] [Xoá] | [Đặt vùng I-O] [Alt:OCR] [Alt:Whisper] | [Zoom ---]|
+--------------+-----------------------------------------------------------------------------------+
| TRACK HEADER |                       MULTI-TRACK TIMELINE (TOÀN CHIỀU NGANG)                     |
| [M][L] Lớp   |  [=== Blur che sub gốc ===]          [=== Text biển hiệu ===]                     |
| [M][L] Sub 2 |  [ Câu 1 (Bản gốc) ]  [ Câu 2 (Bản gốc) ]  [ Câu 3 (Bản gốc) ]                    |
| [M][L] Sub 1 |  [ Câu 1 (Bản dịch)]  [ Câu 2 (Bản dịch)]  [ Câu 3 (Bản dịch)]                    |
| [M][L] Video |  [================== Video Gốc + Nhãn Time-Warp (Slow/Freeze) ==================] |
| [M][L] TTS   |  [ Waveform Câu 1  ]  [ Waveform Câu 2  ]  [ Waveform Câu 3  ]                    |
| [M][L] BGM   |  [~~~~~~~~~~~~~~~~~~~~~~ Dạng sóng Nhạc nền / Âm gốc ~~~~~~~~~~~~~~~~~~~~~~~~~~~] |
+--------------+-----------------------------------------------------------------------------------+
```

### Chi tiết thiết kế từng khu vực:

#### 4.1. Thanh Top Header Bar (Kiểu CapCut Desktop)
- **Góc trái**: Biểu tượng ứng dụng, menu **Dự án** (`Tạo mới`, `Mở dự án`, `Lưu dự án`, `Đóng gói dự án di động .zip`, `Nhập gói dự án`), nút mở nhanh **Công cụ Media (4-trong-1)** và nút **Hàng đợi xử lý hàng loạt (Batch Queue)** có badge đếm số video đang chạy.
- **Chính giữa**: Tên dự án (click để đổi tên trực tiếp), chỉ báo tự động lưu (`Đã lưu`).
- **Góc phải**: Badge phần cứng (`CUDA · RTX ...` hoặc `CPU`), nút mở **Cài đặt & Quản lý Model AI**, và nút **Xuất bản (Export)** nổi bật màu Cyan mở hộp thoại Xuất đa năng (giống hộp thoại Export của CapCut Desktop: cho phép chọn xuất Video MP4, chỉ xuất Âm thanh Voice/BGM/Mix, xuất Phụ đề SRT/ASS, xuất Tài nguyên rời, hoặc **Đẩy thẳng sang CapCut Desktop Draft**).

#### 4.2. Cột Trái: Navigation Icon Rail + Tool Drawer (`LeftDock`)
Thay vì nhồi nhét mọi thứ vào một cột dài rối mắt, sử dụng thanh **Icon Rail dọc** (rộng `56px`) kết hợp ngăn **Tool Drawer** (`300px – 360px`, có thể thu gọn):
1. **Tab `Media`**: Nhập video nguồn, nhập file nhạc nền BGM ngoài, nhập file `.srt` có sẵn, quản lý tài nguyên trong dự án.
2. **Tab `Phụ đề AI (Captions)`**:
   - Chọn phương thức tách: **Nhận dạng giọng nói (`Whisper` / `SenseVoice` / `CapCut`)** hoặc **Quét phụ đề cứng (`OCR`)**.
   - Các tùy chọn thông minh: `Tách giọng sạch trước khi nghe`, `Gộp câu trùng lặp (Dedup)`, `Lọc ảo giác khoảng lặng`, `Phân vai người nói (Speaker Diarization)`, `Bù đoạn trống bằng OCR`.
3. **Tab `Dịch thuật AI (Translate)`**:
   - Chọn ngôn ngữ đích, Engine dịch (`Local GGUF`, `Gemini`, `DeepSeek`, `OpenAI`, `Ollama`, `Google`, `Bing`, `Browser Bridge`).
   - Chọn **Preset Thể loại phim** (hiển thị dạng thẻ trực quan: *Tiên hiệp*, *Ngôn tình*, *Short Drama*, *Anime*, *K-Drama*...).
   - Nút **Phân tích & Duyệt Xưng Hô Nhân Vật** (mở Modal ma trận nhân vật & đại từ xưng hô).
   - Công tắc chế độ phụ đề: `Đơn ngữ (TS1)` / `Song ngữ (TS1 + TS2)` / `Tắt phụ đề`.
4. **Tab `Lồng tiếng (TTS & Voice)`**:
   - Thư viện giọng đọc trực quan (tìm kiếm, lọc Nam/Nữ/Trẻ em/Review phim từ `Voice.json`, `ElevenLabs`, `VieNeu`, `Piper`, `Edge`, `OmniVoice`), có nút nghe thử từng giọng.
   - Bảng **Phân vai giọng đọc theo Nhân vật (`SPEAKER_00`, `SPEAKER_01`...)**.
   - Tùy chọn **Fit Voice (Time-Warp: Tự động làm chậm/giữ khung hình video khớp độ dài câu đọc)** và **Cắt khoảng lặng đầu/cuối (`-60dB`)**.
5. **Tab `Lớp phủ & Hiệu ứng (Layers & Stickers)`**:
   - Thêm nhanh các lớp: **Làm mờ phụ đề gốc (`Blur`)**, **Khung che (`Mask`)**, **Chữ tiêu đề (`Text`)**, **Chèn ảnh (`Logo`)**, hoặc công cụ **Quét & Dịch vùng ảnh (`OCR Translator`)**.

#### 4.3. Khu vực Giữa: Trình phát Video & Màn hình Canvas Tương tác (`CenterPlayerCanvas`)
- **Interactive Stage**:
  - Video hiển thị giữa bàn dựng tối màu, hỗ trợ chuyển đổi tỷ lệ khung hình (`9:16`, `16:9`, `1:1`, `Gốc`) ngay dưới góc khung phát.
  - **Thao tác trực tiếp trên màn hình (On-Canvas Manipulation)**:
    - Click vào Phụ đề TS1, Phụ đề TS2, Lớp làm mờ (`Blur`), Khung che (`Mask`), `Logo`, `Text` hoặc `Vùng quét OCR` sẽ hiện khung **Bounding Box màu Cyan với 8 điểm neo kéo giãn (Resize Handles)** và đường gióng căn giữa tự động (`Smart Alignment Guides` dọc/ngang như CapCut).
- **Thanh điều khiển phát (Transport Bar)**:
  - Đồng hồ Timecode chuẩn `MM:SS.ms`, nút Play/Pause (`Space`), lùi/tiến 1 frame (`←`/`→`), lùi/tiến 1 câu phụ đề, nút **Lật ngang video (`Flip Horizontal`)**, nút bật/tắt nhanh lớp phủ, và nút **Xem thử 5s (`Fast Preview 5s`)** để kiểm tra kết quả render thực tế ngay lập tức.

#### 4.4. Cột Phải: Bảng Thuộc Tính Ngữ Cảnh & Biên Tập Phụ Đề (`RightInspector`)
Hoạt động giống hệt **Inspector Panel** của CapCut Desktop, gồm 2 chế độ hoặc chia tab thông minh:
1. **Tab `Biên tập Phụ đề (Subtitles List)`**:
   - Thanh chuyển đổi nhanh **Track Phụ đề 1 (TS1)** / **Track Phụ đề 2 (TS2)** / **Xem Song Song**.
   - Thanh công cụ nhanh: Tìm kiếm & Thay thế từ, Dịch chuyển thời gian (`Shift Timing`), **Khôi phục thời gian gốc (`Restore Timing`)**, Xuất nhanh `.srt`/`.ass`.
   - Danh sách các thẻ câu thoại (Cue Cards):
     - Chỉnh sửa trực tiếp `Start -> End`, gán nhãn `Speaker` (có màu riêng), chọn giọng đọc & tốc độ riêng cho câu đó.
     - Ô nhập câu gốc và câu dịch êm mượt, tự động cuộn theo vị trí Playhead.
     - Cụm nút thao tác nhanh trên từng câu: `Nghe câu này`, `Tạo lại TTS riêng câu này`, `Quét lại OCR khung hình câu này`, `Chia đôi câu tại con trỏ`, `Gộp với câu dưới`, `Xóa`.
2. **Tab `Kiểu chữ & Mẫu (Text Style & Presets)`** (hiển thị khi chọn Phụ đề hoặc Text Layer):
   - Bộ chọn chỉnh sửa cho `TS1` hoặc `TS2`.
   - Kho **My Presets** (hiển thị dạng các ô chữ mẫu trực quan như CapCut, cho phép Lưu / Đổi tên / Xóa mẫu cá nhân).
   - Chỉnh chi tiết: Phông chữ, Cỡ chữ, Màu chữ, Viền chữ (Stroke), Bóng đổ (Shadow), Hộp nền (Background Box + Độ bo góc + Độ mờ), Vị trí căn lề 9 điểm, và hiệu ứng **Highlight từ khóa (Đổi màu / Phóng to từ nhấn mạnh)**.
3. **Tab `Video & Âm thanh (Video & Audio Mixer)`** (hiển thị khi chọn track Video/Audio):
   - **Bộ trộn 3 kênh âm thanh (3-Track Mixer)**: Thanh trượt âm lượng độc lập cho `Âm thanh gốc`, `Nhạc nền BGM ngoài`, `Giọng đọc lồng tiếng TTS`, kèm nút `Tách nhạc nền (Vocal Isolation)` và độ nén `Sidechain Ducking`.
   - Thuộc tính lớp phủ (`Blur` intensity, `Feather`, `Mask` opacity, tọa độ `X/Y/W/H`).

#### 4.5. Khu vực Dưới: Timeline Đa Tầng Chuyên Nghiệp (`BottomMultiTrackTimeline`)
Trải rộng 100% chiều ngang màn hình dưới cùng giống hệt CapCut Desktop:
- **Thanh Công cụ Timeline (Timeline Toolbar)**:
  - Công cụ Chọn (`V`), Cắt/Chia đôi tại Playhead (`B` / `Ctrl+B`), Xóa (`Delete`), Undo/Redo.
  - Công cụ **Đặt vùng chọn (`In [` / `Out ]`)**: Khi có một khoảng thời gian được bôi chọn trên Timeline, hiển thị cụm nút nổi:
    - **`Alt: Quét lại vùng này bằng OCR`**
    - **`Alt: Nhận dạng lại vùng này bằng Whisper`**
    - **`Xóa các câu trong vùng chọn`**
  - Công tắc **Bật/Tắt từ tính (`Snapping`)**, **Tự động cuộn theo Playhead**, nút **Thu phóng vừa khít (`Fit Timeline`)** và thanh trượt **Zoom (`px/s`)**.
- **Cột Đầu Track (`Track Headers`) & Các Làn Dữ Liệu (`Timeline Lanes`)**:
  - Mỗi track có nút `Ẩn/Hiện (Eye)`, `Tắt tiếng (Mute)`, `Khóa (Lock)`:
    1. **Track Hiệu ứng / Lớp phủ (`Overlays`)**: Các khối `Blur`, `Mask`, `Logo`, `Text` (kéo hai đầu để chỉnh thời điểm bắt đầu/kết thúc).
    2. **Track Phụ đề 2 (`TS2 - Phụ đề phụ`)**: Các khối câu phụ đề phụ song ngữ.
    3. **Track Phụ đề 1 (`TS1 - Phụ đề chính`)**: Các khối câu phụ đề chính (kéo trực tiếp mép trái/phải để tinh chỉnh timing, tự động bắt dính vào câu kế tiếp).
    4. **Track Video chính (`Main Video & Time-Warp`)**: Hiển thị dải ảnh thu nhỏ (filmstrip) và đánh dấu trực quan các đoạn đang được giãn chậm (`Slow`) hoặc dừng hình (`Freeze`) bởi thuật toán Fit Voice.
    5. **Track Lồng tiếng (`TTS Voice`)**: Hiển thị từng khối âm thanh thoại kèm biểu đồ sóng âm (`Waveform`), đổi màu cảnh báo vàng/đỏ nếu câu thoại bị tràn thời lượng.
    6. **Track Nhạc nền (`BGM & Original Audio`)**: Hiển thị biểu đồ sóng âm của nhạc nền và đường bao âm lượng (volume envelope).

---

## 5. CẤU TRÚC THƯ MỤC DỰ ÁN MỚI ĐỀ XUẤT

```text
capcap-studio/
├── backend/
│   ├── app/
│   │   ├── main.py                     # Khởi tạo FastAPI, CORS, WebSocket, Static/Asset routes
│   │   ├── config.py                   # Cấu hình hệ thống, đường dẫn model, FFmpeg, API keys
│   │   ├── core/
│   │   │   ├── hardware.py             # Phát hiện GPU, tự động nạp DLL CUDA 12 & CUDA 13
│   │   │   └── events.py               # Quản lý kết nối WebSocket & phát sự kiện tiến trình
│   │   ├── schemas/                    # Pydantic v2 models cho Project, Subtitle, TTS, Render, Queue
│   │   ├── engines/                    # Các bộ xử lý cốt lõi chuyển đổi 1:1 từ CapCap
│   │   │   ├── whisper_engine.py       # Faster-Whisper + VAD + Hallucination filter + Dedup
│   │   │   ├── sensevoice_engine.py    # SenseVoice ONNX + Smart token splitting
│   │   │   ├── ocr_engine.py           # Rawvideo pipe + Mask dedup + RapidOCR/WinOCR + CJK radical fix
│   │   │   ├── diarization_engine.py   # Sherpa-ONNX Speaker Diarization
│   │   │   ├── capcut_engine.py        # CapCut Web & PC (sscronet.dll worker) STT/TTS + Voice.json
│   │   │   ├── elevenlabs_engine.py    # ElevenLabs TTS + Quota/Voice list
│   │   │   └── local_tts_engine.py     # Edge TTS, Piper TTS, VieNeu TTS, OmniVoice, VoiceStudio
│   │   ├── translation/                # Phân hệ dịch thuật chuyển đổi 1:1 từ CapCap/app/translation
│   │   │   ├── translator.py           # Local GGUF, Gemini, DeepSeek, OpenAI, Ollama, Google->Bing
│   │   │   ├── context_analyzer.py     # Phân tích nhân vật, đại từ xưng hô & RollingContextLedger
│   │   │   ├── browser_bridge.py       # Direct Browser AI Bridge
│   │   │   └── presets.py              # Quản lý Genre Prompt Presets
│   │   ├── media/                      # Xử lý Video / Audio / Phụ đề
│   │   │   ├── filter_chain.py         # Visual Layers (Blur, Mask, Logo, Text, Flip, Aspect)
│   │   │   ├── audio_mixer.py          # Vocal separation, 3-track mixer, boundary silence trim
│   │   │   ├── time_warp.py            # Fit Voice time-warp planning & coordinate mapping
│   │   │   ├── subtitle_renderer.py    # Xuất/Nhập SRT, ASS song ngữ TS1/TS2, Keyword Highlight
│   │   │   ├── capcut_exporter.py      # Xuất dự án sang CapCut Desktop Drafts
│   │   │   └── media_tools.py          # 4 công cụ FFmpeg (Trim, Concat, Demux, Mux)
│   │   ├── services/
│   │   │   ├── queue_service.py        # Hàng đợi xử lý nhiều video (Performance Queue)
│   │   │   └── project_service.py      # Lưu/Mở dự án & Đóng gói Portable Project Package
│   │   └── api/                        # Các tuyến REST API
│   └── tests/                          # Bộ kiểm thử tự động pytest
├── frontend/
│   ├── src/
│   │   ├── types/                      # Định nghĩa TypeScript đồng bộ 1:1 với Backend schemas
│   │   ├── store/                      # Zustand store (Project, Timeline Tracks, Selection, Undo/Redo)
│   │   ├── services/                   # HTTP API client & WebSocket listener
│   │   └── components/
│   │       ├── layout/                 # TopHeaderBar, Resizable Split Workspace
│   │       ├── dock/                   # LeftIconRail + drawers (Media, ASR/OCR, Translate, TTS, Layers)
│   │       ├── player/                 # CenterPlayerCanvas (Interactive Bounding Box, Snapping Guides)
│   │       ├── inspector/              # RightInspector (Subtitle List TS1/TS2, Text Style, Audio Mixer)
│   │       ├── timeline/               # BottomMultiTrackTimeline (6+ tracks, Waveform, Range Actions)
│   │       └── modals/                 # ExportModal, PronounReviewModal, BatchQueueModal, MediaToolsModal
│   └── src-tauri/                      # Cấu hình Tauri v2 Desktop
└── resources/
    ├── capcut/Voice.json               # Danh mục giọng đọc CapCut từ CapCap
    └── prompts/                        # Toàn bộ prompt templates & genre presets từ CapCap
```

---

## 6. TIÊU CHÍ NGHIỆM THU (ACCEPTANCE CRITERIA)
1. **Đầy đủ 100% tính năng nghiệp vụ của `CapCap` (v9.0.1)**: Không thiếu bất kỳ thuật toán, chế độ xử lý hay công cụ nào so với bản gốc `CapCap`.
2. **Trải nghiệm giao diện chuẩn Studio chuyên nghiệp (như CapCut Desktop)**: Bố cục rõ ràng, hiện đại, có thanh công cụ bên trái theo ngữ cảnh (`Icon Rail + Drawer`), màn hình Canvas kéo-thả/co-giãn trực tiếp ở giữa, bảng thuộc tính `Inspector` bên phải và `Multi-Track Timeline` mượt mà ở phía dưới.
3. **Tốc độ OCR & ASR tối ưu**: Chạy trực tiếp trên GPU CUDA (cả Whisper và RapidOCR) không cần cấu hình thủ công biến môi trường PATH; tốc độ tách phụ đề cứng đạt mức tối đa nhờ FFmpeg rawvideo pipe + mask deduplication.
4. **Kiểm thử tự động**:
   - Toàn bộ unit test và integration test phía Backend (`pytest`) đạt 100% PASS.
   - Toàn bộ mã nguồn Frontend biên dịch sạch sẽ (`tsc --noEmit && vite build`) với 0 lỗi.
