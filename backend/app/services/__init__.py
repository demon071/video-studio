r"""Media + AI services (Step 2).

    media_processor.py  FFmpeg: audio extraction, vocal separation, atempo
                        time-stretching, timestamp-aligned track assembly, render
    stt_service.py      faster-whisper / SenseVoice (offline) / Groq / OpenAI (online)
    translate_service.py llama-cpp-python Qwen2.5 / Gemma 3 GGUF, Gemini, OpenAI
    tts_service.py      CapCut, OmniVoice and VoiceStudio, synced through media_processor
    model_manager.py    on-demand download of weights into %APPDATA%\ShortVideoAI
"""
