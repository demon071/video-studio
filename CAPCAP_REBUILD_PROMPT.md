# YÊU CẦU XÂY DỰNG ỨNG DỤNG MỚI HOÀN TOÀN TỪ CAPCAP (v9.0.1)

## 1. BỐI CẢNH & MỤC TIÊU DỰ ÁN
Xây dựng một ứng dụng Desktop chuyên nghiệp **mới hoàn toàn từ con số 0 (from scratch)** dùng để biên tập video ngắn, tự động nhận dạng giọng nói/phụ đề cứng (ASR/OCR), dịch thuật AI đa ngữ cảnh, lồng tiếng đa nhân vật (TTS) và xuất bản video/CapCut Draft.

- **Nguồn chuẩn duy nhất về tính năng, thuật toán & giao diện (Single Source of Truth)**:
  - **`CapCap` (v9.0.1)**: Nguồn tham chiếu 1:1 cho toàn bộ chức năng, luồng nghiệp vụ (workflow), thuật toán xử lý, cấu trúc dữ liệu, prompt dịch thuật, danh mục giọng đọc và bố cục giao diện người dùng (UI/UX).

### Nguyên tắc cốt lõi:
1. **Xây dựng mới 100%**: Tự thiết kế kiến trúc thư mục chuẩn mực, sạch sẽ từ đầu; chuyển đổi toàn bộ chức năng của `CapCap` (vốn viết bằng `CustomTkinter`) sang kiến trúc **Client–Server Desktop hiện đại**.
2. **Bảo toàn tuyệt đối 100% tính năng & thuật toán của CapCap**: Mọi công thức xử lý thời gian, làm sạch phụ đề, phân vai người nói, phân tích ngữ cảnh xưng hô, giãn hình khớp giọng đọc (Fit Voice / Time-Warp), trộn âm thanh 3 kênh, hiệu ứng lớp phủ hình ảnh, hàng đợi xử lý hàng loạt và công cụ FFmpeg của `CapCap` đều phải được chuyển đổi đầy đủ, chính xác.
3. **Ngôn ngữ hiển thị**: 100% Tiếng Việt, giữ nguyên hệ thống thuật ngữ quen thuộc của `CapCap`.

---

## 2. KIẾN TRÚC CÔNG NGHỆ (TECH STACK)

### 2.1. Backend (`backend/`)
- **Ngôn ngữ & Framework**: Python 3.11+, **FastAPI**, **Uvicorn**, **Pydantic v2**.
- **Giao tiếp thời gian thực**:
  - **REST API**: Quản lý dự án, cấu hình, thực thi từng bước hoặc toàn bộ pipeline, truy vấn danh sách model/giọng đọc, điều khiển hàng đợi.
  - **WebSocket (`/ws/events`)**: Truyền phát trực tiếp (real-time streaming) phần trăm tiến độ (`progress`), bước xử lý hiện tại (`stage`), từng câu phụ đề vừa nhận dạng/dịch xong, trạng thái hàng đợi (`queue_state`) và nhật ký hệ thống (`logs`).
- **Tăng tốc phần cứng (Hardware & GPU Acceleration)**:
  - Tự động phát hiện và đăng ký đường dẫn DLL cho cả **CUDA 12** (`nvidia/cublas/bin`, `nvidia/cudnn/bin` phục vụ `ctranslate2` / `faster-whisper`) và **CUDA 13** (`torch/lib` phục vụ `onnxruntime-gpu` / `RapidOCR`).
  - Hỗ trợ mã hóa video bằng phần cứng **NVIDIA NVENC (`h264_nvenc`)** và tự động lùi về `libx264` khi chạy trên CPU.

### 2.2. Frontend & Desktop Shell (`frontend/`)
- **Giao diện người dùng**: **React 19 + TypeScript + Vite + Tailwind CSS + Lucide Icons**.
- **Quản lý trạng thái**: **Zustand** (lưu trữ trạng thái dự án, danh sách phụ đề TS1/TS2, các lớp phủ hình ảnh, cấu hình pipeline, lịch sử Undo/Redo và đồng bộ thời gian thực với Backend).
- **Desktop Runtime**: **Tauri v2 (`src-tauri/`)**:
  - Khởi chạy và giám sát tiến trình Python FastAPI dưới dạng Sidecar hoặc Local Daemon.
  - Cung cấp hộp thoại chọn file/thư mục gốc của Windows, kéo-thả file trực tiếp từ File Explorer và phát trực tiếp media cục bộ qua giao thức `asset://`.

---

## 3. ĐẶC TẢ CHI TIẾT CÁC PHÂN HỆ CHỨC NĂNG (THAM CHIẾU 1:1 TỪ `CapCap`)

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
   - **Lọc ảo giác (Hallucination Filter)**: Tự động loại bỏ các đoạn lặp từ bất thường, các câu xuất hiện trong khoảng lặng dài hơn ngưỡng `hallucination_silence_threshold` (mặc định `1.2s`), và các mẫu câu rác kinh điển của Whisper.
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
   - Hỗ trợ nhập trực tiếp file `.srt` vào Phụ đề chính (TS1) hoặc Phụ đề phụ (TS2).

---

### 3.2. Phân hệ Tách Phụ đề Cứng (OCR) & Dịch Vùng Ảnh Trực Tiếp (OCR Translator)
**File tham chiếu gốc trong `CapCap`**:
- `CapCap/app/ocr_processor.py`

**Yêu cầu triển khai**:
1. **Streaming Khung hình & Tiền xử lý Ảnh**:
   - Đọc khung hình trực tiếp từ video (hỗ trợ FFmpeg rawvideo pipe với hàng đợi trong RAM để tối ưu tốc độ).
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
   - Cho phép khoanh một vùng bất kỳ trên khung hình video (bảng hiệu, tin nhắn, chú thích), bấm 1 nút để OCR đọc chữ, dịch sang ngôn ngữ đích và tự động tạo một lớp `Text Layer` che đè lên vùng chữ gốc.

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
   - TS1 và TS2 có dữ liệu dòng riêng, bộ kiểu dáng (`SubtitleStyle`) riêng, vị trí hiển thị riêng trên màn hình và làn (track) riêng trên Timeline.

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
   - Cho phép gán giọng đọc riêng cho từng nhãn `SPEAKER_XX`, cũng như ghi đè giọng đọc (`voice`) và tốc độ đọc (`speed`) trên từng câu phụ đề riêng lẻ.
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
   - Hỗ trợ 3 chế độ vận hành:
     - **Chỉ xử lý (`process_only`)**: Chạy tự động Tách sub $\rightarrow$ Dịch $\rightarrow$ Lồng tiếng rồi dừng ở trạng thái `Sẵn sàng xuất` để người dùng kiểm tra lại trước khi render.
     - **Xử lý & Xuất luôn (`process_and_export`)**: Chạy trọn gói từ đầu đến khi ra file `.mp4` hoàn chỉnh.
     - **Xuất các video đã chọn (`export_selected`)**: Render hàng loạt các video đã ở trạng thái `Sẵn sàng xuất`.
   - Hỗ trợ **Dừng sau video hiện tại**, **Dừng khẩn cấp**, và **Dọn dữ liệu tạm của video (`Clean Video Data`)**.
2. **Đóng gói & Di chuyển Dự án (`ProjectPackageService`)**:
   - Lưu/Mở dự án đang làm dở và đóng gói thành tệp nén di động bao gồm toàn bộ tài nguyên liên quan.
3. **Công cụ Media FFmpeg 4-trong-1 (`MediaToolsModal`)**:
   - **Tab 1 — Cắt Video (Trim)**: Cắt đoạn video theo mốc bắt đầu/kết thúc (chế độ Copy siêu tốc hoặc Re-encode chính xác từng frame).
   - **Tab 2 — Ghép Video (Concat)**: Ghép nối nhiều video theo thứ tự tùy chọn.
   - **Tab 3 — Tách Âm thanh / Hình ảnh (Demux)**: Tách riêng file âm thanh (`.wav`/`.mp3`) và/hoặc file hình ảnh không tiếng (`.mp4`).
   - **Tab 4 — Ghép Nhạc vào Video (Mux)**: Thay thế âm thanh gốc bằng file âm thanh mới hoặc trộn chung hai luồng âm thanh.

---

## 4. ĐẶC TẢ GIAO DIỆN NGƯỜI DÙNG TRÊN REACT + TAURI (BÁM SÁT `CapCap/app/ui/`)

Giao diện được thiết kế theo phong cách Studio tối màu (Dark Mode), chia thành 5 vùng làm việc đồng bộ đúng với `CapCap`:

1. **Thanh Công Cụ Đỉnh (`TopHeaderBar` — từ `CapCap/app/ui/header.py`)**:
   - Nhập Video, Mở/Lưu Dự án, Xuất/Nhập Gói Dự Án.
   - Công tắc chuyển bố cục thích ứng **9:16 (Dọc)** ↔ **16:9 (Ngang)**.
   - Nút mở **Hàng Đợi Xử Lý Hàng Loạt (Performance Queue)**.
   - Nút mở **Công Cụ Media (4 Tab FFmpeg)**.
   - Nút mở **Cài Đặt Hệ Thống & Quản Lý Model AI / API Keys**.
   - Hiển thị trạng thái phần cứng (GPU/VRAM) và tiến trình đang chạy.

2. **Bảng Điều Khiển Quy Trình & Kiểu Chữ (`LeftControlPanel` — từ `CapCap/app/ui/left_panel.py` & `subtitle_style_tab.py`)**:
   - Chia thành các tab trực quan:
     - **Tab 1: Tạo & Dịch Phụ Đề** (Chọn nguồn ASR/OCR, tùy chọn làm sạch/phân vai, chọn Engine dịch, Preset thể loại, nút *Duyệt xưng hô*, chế độ hiển thị Đơn ngữ / Song ngữ / Không phụ đề).
     - **Tab 2: Kiểu Dáng Phụ Đề (Style & Presets)** (Chuyển đổi chỉnh sửa giữa *Phụ đề 1 TS1* và *Phụ đề 2 TS2*, quản lý *My Presets*, chỉnh Font, Màu sắc, Viền, Nền, Vị trí, Highlight từ khóa).
     - **Tab 3: Lồng Tiếng & Âm Thanh** (Chọn Engine TTS, lọc/tìm kiếm giọng đọc CapCut/ElevenLabs/Offline, gán giọng theo từng Speaker, bật *Fit Voice Time-Warp*, cắt khoảng lặng, chỉnh 3 thanh trượt âm lượng *Âm gốc / BGM / Giọng đọc*).
     - **Tab 4: Xuất Bản** (Chọn độ phân giải/tỷ lệ khung hình, chất lượng nén, bật NVENC GPU, *Xuất MP4*, *Xuất Audio rời Voice/BGM/Mix*, *Xuất Tài nguyên*, *Xuất sang CapCut Draft*).

3. **Khung Xem Trước Tương Tác (`InteractivePreviewCanvas` — từ `CapCap/app/ui/center_panel.py`)**:
   - Trình phát video đồng bộ thời gian thực với âm thanh lồng tiếng, nhạc nền và phụ đề TS1/TS2.
   - Cho phép **kéo-thả và co-giãn trực tiếp bằng chuột (Drag & 8-point Resize Handles)** ngay trên mặt khung hình video cho:
     - Vị trí Phụ đề 1 (TS1) và Phụ đề 2 (TS2).
     - Vùng quét OCR (`OCR Scan Region`).
     - Các lớp phủ `Blur`, `Mask`, `Logo`, `Text`.
   - **Thanh công cụ nhanh dưới màn hình Preview**:
     - `[+ Chữ (T)]`, `[+ Logo]`, `[+ Làm mờ (Blur)]`, `[+ Khung che (Mask)]`, `[Quét & Dịch vùng OCR]`, `[Lật ngang]`, và nút **`Xem thử 5s (Fast Preview)`**.

4. **Bảng Biên Tập Phụ Đề (`SubtitleTablePanel` — từ `CapCap/app/ui/right_panel.py`)**:
   - Chuyển đổi giữa **Phụ đề 1 (TS1)** và **Phụ đề 2 (TS2)**.
   - Hiển thị danh sách câu phụ đề với đầy đủ công cụ trên từng dòng: chỉnh mốc `Start -> End`, chọn nhãn `Speaker`, chọn `Giọng đọc / Tốc độ riêng` cho câu đó, xem hệ số khớp thời lượng TTS, sửa văn bản gốc & bản dịch, nút `Nghe thử câu`, `Tạo lại TTS câu`, `Quét lại OCR câu`, `Thêm/Gộp/Tách/Xóa dòng`.
   - Thanh công cụ phía trên bảng: Tìm kiếm & Thay thế, Dịch chuyển thời gian hàng loạt, Xuất/Nhập `.srt`/`.ass`, và nút **Khôi phục thời gian gốc (`Restore Original Timing`)**.

5. **Timeline Đa Tầng (`MultiTrackTimeline` — từ `CapCap/app/ui/bottom_panel.py` & `timeline.py`)**:
   - Thước thời gian hỗ trợ Zoom, bám theo Playhead, kéo chọn vùng (`Selection Range`).
   - Khi kéo chọn một vùng trên Timeline, hiển thị thanh tác vụ nổi: **`Alt: Quét lại bằng OCR`**, **`Alt: Tách lại bằng Whisper`**, **`Xóa phụ đề trong vùng`**.
   - Hiển thị đầy đủ các làn (lanes) độc lập, cho phép kéo biên trái/phải để thay đổi trực tiếp thời gian bắt đầu/kết thúc:
     - `Video / Time-Warp`
     - `Phụ đề 1 (TS1)`
     - `Phụ đề 2 (TS2)`
     - `Lồng tiếng (TTS)`
     - `Nhạc nền (BGM)`
     - `Hiệu ứng lớp phủ (Blur / Mask / Logo / Text)`

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
│   │   │   ├── ocr_engine.py           # Rawvideo pipe + XOR mask dedup + RapidOCR/WinOCR + CJK radical fix
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
│   │   ├── store/                      # Zustand store quản lý toàn bộ trạng thái ứng dụng
│   │   ├── services/                   # HTTP API client & WebSocket listener
│   │   └── components/
│   │       ├── layout/                 # TopHeaderBar, Adaptive 9:16 & 16:9 Workspace
│   │       ├── pipeline/               # Các bảng điều khiển ASR, Dịch, TTS, Mix & Xuất bản
│   │       ├── style/                  # SubtitleStyleTab (TS1/TS2, My Presets, Highlight)
│   │       ├── preview/                # InteractivePreviewCanvas (Drag/Resize Layers, Fast Preview 5s)
│   │       ├── subtitles/              # SubtitleTablePanel (TS1/TS2, Speaker, Per-line TTS, Restore Timing)
│   │       ├── timeline/               # MultiTrackTimeline (9 lanes + Range Selection Alt OCR/Whisper)
│   │       └── modals/                 # PronounReviewDialog, QueueModal, MediaToolsModal, SettingsModal
│   └── src-tauri/                      # Cấu hình Tauri v2 Desktop
└── resources/
    ├── capcut/Voice.json               # Danh mục giọng đọc CapCut từ CapCap
    └── prompts/                        # Toàn bộ prompt templates & genre presets từ CapCap
```

---

## 6. TIÊU CHÍ NGHIỆM THU (ACCEPTANCE CRITERIA)
1. **Đầy đủ 100% tính năng của `CapCap` (v9.0.1)**: Không thiếu bất kỳ công tắc, chế độ, hộp thoại hay luồng xử lý nào so với bản gốc `CapCap`.
2. **Tốc độ OCR & ASR tối ưu**: Chạy trực tiếp trên GPU CUDA (cả Whisper và RapidOCR) không cần cấu hình thủ công biến môi trường PATH; tốc độ tách phụ đề cứng đạt mức tối đa nhờ FFmpeg rawvideo pipe + mask deduplication.
3. **Kiểm thử tự động**:
   - Toàn bộ unit test và integration test phía Backend (`pytest`) đạt 100% PASS.
   - Toàn bộ mã nguồn Frontend biên dịch sạch sẽ (`tsc --noEmit && vite build`) với 0 lỗi.
