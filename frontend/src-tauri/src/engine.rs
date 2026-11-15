//! Sidecar supervisor for the Python engine.
//!
//! Production lifecycle (installed app):
//!   1. the window opens immediately, the WebView asks `backend_info` for the port,
//!   2. this module spawns `resources/engine/shortvideoai-engine.exe` on a free
//!      loopback port and passes the parent PID,
//!   3. on exit the child is killed here, and the engine's own watchdog also
//!      terminates itself if this process disappears abruptly (Task Manager).
//!
//! AI pack: the installer ships the *core* engine. When the user installs the
//! optional heavy bundle, it lands in `%APPDATA%\ShortVideoAI\ai-pack\engine`
//! and this supervisor prefers it over the bundled binary. The swap itself has
//! to happen here because Windows locks a running executable: the Python side
//! only extracts into `ai-pack.staging`, then `apply_ai_pack` stops the engine,
//! moves the folder and respawns.
//!
//! Development lifecycle (`npm run dev`): the engine is started by
//! `scripts/dev_backend.ps1` on `SVA_DEV_PORT` (8756) and nothing is spawned.

use std::fs;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::Manager;

/// Fallback port used by `scripts/dev_backend.ps1` when no override is given.
const DEV_PORT: u16 = 8756;

const ENGINE_EXE: &str = "shortvideoai-engine.exe";
const ENGINE_FOLDER: &str = "engine";
const PACK_FOLDER: &str = "ai-pack";
const STAGING_FOLDER: &str = "ai-pack.staging";
const MANIFEST_NAME: &str = "ai-pack.json";

#[derive(Default)]
pub struct EngineState {
    inner: Mutex<Inner>,
}

#[derive(Default)]
struct Inner {
    port: u16,
    managed: bool,
    runtime: String,
    child: Option<Child>,
}

#[derive(Serialize, Clone)]
pub struct BackendInfo {
    /// Loopback port the WebView should talk to.
    pub port: u16,
    /// True when this process spawned the engine (installed app), false in dev.
    pub managed: bool,
    /// True when the engine binary lives inside the installer's resources.
    pub packaged: bool,
    /// `ai-pack` when the optional heavy engine is running, `bundled`
    /// otherwise, `external` in development.
    pub runtime: String,
    /// True when an installed AI pack exists on disk (active after a restart).
    pub ai_pack: bool,
    /// Absolute log file the UI can mention in error banners.
    pub log_path: Option<String>,
}

fn free_port() -> u16 {
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .unwrap_or(DEV_PORT)
}

/// `%APPDATA%\ShortVideoAI`, overridable with `SVA_DATA_DIR` for tests.
fn data_dir() -> Option<PathBuf> {
    if let Some(explicit) = std::env::var_os("SVA_DATA_DIR") {
        let path = PathBuf::from(explicit);
        if !path.as_os_str().is_empty() {
            return Some(path);
        }
    }
    std::env::var_os("APPDATA").map(|base| PathBuf::from(base).join("ShortVideoAI"))
}

fn pack_engine_binary() -> Option<PathBuf> {
    let binary = data_dir()?.join(PACK_FOLDER).join(ENGINE_FOLDER).join(ENGINE_EXE);
    binary.is_file().then_some(binary)
}

fn ai_pack_installed() -> bool {
    pack_engine_binary().is_some()
}

/// Prefer the installed AI pack, fall back to the bundled core engine.
fn pick_engine(resources: &Path) -> (PathBuf, &'static str) {
    if let Some(binary) = pack_engine_binary() {
        (binary, "ai-pack")
    } else {
        (resources.join(ENGINE_FOLDER).join(ENGINE_EXE), "bundled")
    }
}

#[cfg(windows)]
fn hide_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_console(_: &mut Command) {}

fn spawn_at(resources: &Path, port: u16) -> (PathBuf, &'static str, std::io::Result<Child>) {
    let (binary, runtime) = pick_engine(resources);
    let working_dir = binary
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| resources.to_path_buf());

    let mut command = Command::new(&binary);
    command
        .current_dir(&working_dir)
        .env("SVA_HOST", "127.0.0.1")
        .env("SVA_PORT", port.to_string())
        .env("SVA_PACKAGED", "1")
        .env("SVA_PARENT_PID", std::process::id().to_string())
        .env("SVA_LOG_LEVEL", "INFO")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let ffmpeg = resources.join("ffmpeg");
    if ffmpeg.join("ffmpeg.exe").is_file() {
        command.env("SVA_FFMPEG_DIR", ffmpeg);
    }

    hide_console(&mut command);
    (binary, runtime, command.spawn())
}

/// Try up to `attempts` times; a rejected port or a broken bundle makes the
/// process exit within a few hundred milliseconds, which must not be reported
/// to the WebView as a usable port.
fn launch(resources: &Path, attempts: u32) -> Option<(u16, &'static str, Child)> {
    for attempt in 1..=attempts {
        let port = free_port();
        let (binary, runtime, spawned) = spawn_at(resources, port);
        match spawned {
            Ok(mut child) => {
                std::thread::sleep(Duration::from_millis(350));
                match child.try_wait() {
                    Ok(None) => {
                        eprintln!(
                            "[engine] sidecar started (pid {}, port {port}, {runtime}) from {}",
                            child.id(),
                            binary.display()
                        );
                        return Some((port, runtime, child));
                    }
                    Ok(Some(status)) => {
                        eprintln!(
                            "[engine] attempt {attempt}: sidecar exited immediately ({status}) - see the engine log"
                        );
                    }
                    Err(error) => {
                        eprintln!("[engine] attempt {attempt}: cannot poll the sidecar: {error}");
                    }
                }
            }
            Err(error) => {
                eprintln!("[engine] attempt {attempt}: cannot start {}: {error}", binary.display());
            }
        }
    }
    None
}

/// Windows releases the mappings of a killed process a moment after it exits;
/// deleting its folder once can still hit a transient `Access is denied`.
fn remove_dir_retry(path: &Path, attempts: u32) -> std::io::Result<()> {
    let mut last = Ok(());
    for attempt in 0..attempts {
        match fs::remove_dir_all(path) {
            Ok(()) => return Ok(()),
            Err(error) => {
                if !path.exists() {
                    return Ok(());
                }
                last = Err(error);
                std::thread::sleep(Duration::from_millis(150 * u64::from(attempt) + 150));
            }
        }
    }
    last
}

/// `fs::rename` of a folder that was just unmapped can also fail once.
fn move_dir_retry(from: &Path, to: &Path, attempts: u32) -> std::io::Result<()> {
    let mut last = Ok(());
    for attempt in 0..attempts {
        match fs::rename(from, to) {
            Ok(()) => return Ok(()),
            Err(error) => {
                last = Err(error);
                std::thread::sleep(Duration::from_millis(150 * u64::from(attempt) + 150));
            }
        }
    }
    last
}

fn kill_child(inner: &mut Inner) {
    if let Some(mut child) = inner.child.take() {
        let _ = child.kill();
        let _ = child.wait();
        eprintln!("[engine] sidecar stopped");
    }
    inner.runtime = "none".to_string();
}

/// Stop the current engine and start the one that is on disk now.
fn respawn(inner: &mut Inner, resources: &Path) -> Result<(), String> {
    kill_child(inner);
    if !inner.managed {
        // Development: `scripts/dev_backend.ps1` owns the process.
        return Ok(());
    }
    match launch(resources, 3) {
        Some((port, runtime, child)) => {
            inner.port = port;
            inner.child = Some(child);
            inner.runtime = runtime.to_string();
            Ok(())
        }
        None => Err(format!(
            "the engine did not start after switching runtime - see {}",
            log_path().unwrap_or_else(|| "the engine log".to_string())
        )),
    }
}

fn log_path() -> Option<String> {
    data_dir()
        .map(|dir| dir.join("logs").join("engine.log"))
        .map(|path| path.display().to_string())
}

fn snapshot(inner: &Inner) -> BackendInfo {
    BackendInfo {
        port: inner.port,
        managed: inner.managed,
        packaged: cfg!(not(debug_assertions)),
        runtime: inner.runtime.clone(),
        ai_pack: ai_pack_installed(),
        log_path: log_path(),
    }
}

fn resources_dir(app: &tauri::AppHandle) -> PathBuf {
    app.path().resource_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// Decide whether this build owns the engine, then start it.
pub fn start(app: &tauri::AppHandle) -> EngineState {
    let resources = resources_dir(app);

    // `SVA_SIDECAR=1` forces the supervisor in a debug build so the packaged
    // flow can be exercised without producing an installer first.
    let forced = std::env::var("SVA_SIDECAR").map(|value| value == "1").unwrap_or(false);
    let managed = forced || !cfg!(debug_assertions);

    if !managed {
        let port = std::env::var("SVA_DEV_PORT")
            .ok()
            .and_then(|value| value.parse::<u16>().ok())
            .unwrap_or(DEV_PORT);
        eprintln!("[engine] development mode - expecting the engine on 127.0.0.1:{port}");
        return EngineState {
            inner: Mutex::new(Inner {
                port,
                managed: false,
                runtime: "external".to_string(),
                child: None,
            }),
        };
    }

    if let Some(data) = data_dir() {
        let _ = fs::create_dir_all(&data);
    }

    match launch(&resources, 3) {
        Some((port, runtime, child)) => EngineState {
            inner: Mutex::new(Inner {
                port,
                managed: true,
                runtime: runtime.to_string(),
                child: Some(child),
            }),
        },
        None => {
            eprintln!("[engine] falling back to port {DEV_PORT} after three failed launches");
            EngineState {
                inner: Mutex::new(Inner {
                    port: DEV_PORT,
                    managed: true,
                    runtime: "none".to_string(),
                    child: None,
                }),
            }
        }
    }
}

/// Terminate the engine when the shell shuts down.
pub fn shutdown(state: &EngineState) {
    let mut inner = match state.inner.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    kill_child(&mut inner);
}

#[tauri::command]
pub fn backend_info(state: tauri::State<'_, EngineState>) -> BackendInfo {
    let inner = state.inner.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    snapshot(&inner)
}

/// Move `ai-pack.staging/engine` into place and restart into it.
///
/// The Python engine only extracts the archive; the swap lives here because a
/// running executable (and the DLLs it mapped) cannot be replaced.
#[tauri::command]
pub fn apply_ai_pack(app: tauri::AppHandle, state: tauri::State<'_, EngineState>) -> Result<BackendInfo, String> {
    let data = data_dir().ok_or("APPDATA is not set - cannot locate the AI pack folder")?;
    let staging = data.join(STAGING_FOLDER);
    let target = data.join(PACK_FOLDER);
    if !staging.join(ENGINE_FOLDER).join(ENGINE_EXE).is_file() {
        return Err(format!(
            "no staged AI pack at {} - run the install step first",
            staging.display()
        ));
    }

    let resources = resources_dir(&app);
    let mut inner = state.inner.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let managed = inner.managed;
    kill_child(&mut inner);

    if target.exists() {
        remove_dir_retry(&target, 12)
            .map_err(|error| format!("cannot replace {}: {error}", target.display()))?;
    }
    fs::create_dir_all(&target).map_err(|error| format!("cannot create {}: {error}", target.display()))?;
    move_dir_retry(&staging.join(ENGINE_FOLDER), &target.join(ENGINE_FOLDER), 12)
        .map_err(|error| format!("cannot move the staged engine into place: {error}"))?;
    let manifest = staging.join(MANIFEST_NAME);
    if manifest.is_file() {
        let _ = fs::rename(&manifest, target.join(MANIFEST_NAME));
    }
    let _ = fs::remove_dir_all(&staging);

    inner.managed = managed;
    inner.runtime = "ai-pack".to_string();
    respawn(&mut inner, &resources)?;
    eprintln!("[engine] AI pack applied -> runtime {}", inner.runtime);
    Ok(snapshot(&inner))
}

/// Delete the installed AI pack and go back to the bundled core engine.
#[tauri::command]
pub fn remove_ai_pack(app: tauri::AppHandle, state: tauri::State<'_, EngineState>) -> Result<BackendInfo, String> {
    let data = data_dir().ok_or("APPDATA is not set - cannot locate the AI pack folder")?;
    let resources = resources_dir(&app);
    let mut inner = state.inner.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let managed = inner.managed;
    kill_child(&mut inner);

    let target = data.join(PACK_FOLDER);
    if target.exists() {
        remove_dir_retry(&target, 12)
            .map_err(|error| format!("cannot delete {}: {error}", target.display()))?;
    }
    let _ = fs::remove_dir_all(data.join(STAGING_FOLDER));

    inner.managed = managed;
    inner.runtime = "bundled".to_string();
    respawn(&mut inner, &resources)?;
    eprintln!("[engine] AI pack removed -> runtime {}", inner.runtime);
    Ok(snapshot(&inner))
}

/// Restart the sidecar (same runtime) after an external change.
#[tauri::command]
pub fn restart_engine(app: tauri::AppHandle, state: tauri::State<'_, EngineState>) -> Result<BackendInfo, String> {
    let resources = resources_dir(&app);
    let mut inner = state.inner.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if !inner.managed {
        return Err("the engine is not managed by the shell in development mode".to_string());
    }
    respawn(&mut inner, &resources)?;
    Ok(snapshot(&inner))
}

/// Grant the asset protocol read access to one media file picked by the user.
///
/// The bundled `tauri.conf.json` keeps the static scope empty, so a compromised
/// WebView cannot read arbitrary files on disk.
#[tauri::command]
pub fn grant_media_access(app: tauri::AppHandle, path: String) -> Result<(), String> {
    app.asset_protocol_scope()
        .allow_file(&path)
        .map_err(|error| format!("cannot grant asset access to {path}: {error}"))
}

/// Allow the engine's own folders (dub tracks, renders, thumbnails) up front.
pub fn allow_engine_folders(app: &tauri::AppHandle) {
    if let Some(data) = data_dir() {
        let _ = app.asset_protocol_scope().allow_directory(&data, true);
    }
}
