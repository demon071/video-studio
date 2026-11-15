//! Tauri entry point.
//!
//! Responsibilities kept here:
//!   * register the official plugins (dialog / fs / os / process / shell),
//!   * start the Python engine sidecar through [`engine`] and expose its port
//!     to the WebView (`backend_info`),
//!   * narrow the `asset://` scope to files the user actually picked
//!     (`grant_media_access`) instead of the wide-open `**` glob,
//!   * guarantee the engine dies with the shell (`RunEvent::Exit*`),
//!   * swap in / remove the optional AI pack and respawn the sidecar
//!     (`apply_ai_pack`, `remove_ai_pack`, `restart_engine`).

mod engine;

use tauri::{Manager, RunEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![
            engine::backend_info,
            engine::grant_media_access,
            engine::apply_ai_pack,
            engine::remove_ai_pack,
            engine::restart_engine
        ])
        .setup(|app| {
            engine::allow_engine_folders(app.handle());
            app.manage(engine::start(app.handle()));

            if let Some(window) = app.get_webview_window("main") {
                #[cfg(debug_assertions)]
                window.open_devtools();
                let _ = window.set_title("ShortVideoAI — Short-Form Video Editor");
            }
            Ok(())
        });

    let app = builder
        .build(tauri::generate_context!())
        .expect("error while building the ShortVideoAI desktop shell");

    app.run(|handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            let state = handle.state::<engine::EngineState>();
            engine::shutdown(&state);
        }
    });
}
